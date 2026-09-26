"""Tests for retention (AUDIT 4.1: "enforced in code, not in a doc").

The property that matters is the one the two-pass design exists for: **nothing
is erased before its notice period has elapsed.** A single pass that noticed and
erased together would satisfy the letter of "give notice" and none of its
purpose, so that is asserted directly rather than inferred from the code shape.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.audit.models import AccessLogEntry, AuditAction
from apps.compliance.models import (
    ERASURE_NOTICE_HOURS,
    RETENTION_YEARS,
    RetentionNotice,
)
from apps.compliance.services.retention_service import RetentionService
from apps.consent.models import ConsentArtefact
from apps.patients.models import Patient
from apps.triage.models import TriageAssessment

pytestmark = pytest.mark.django_db


@pytest.fixture
def service() -> RetentionService:
    return RetentionService()


def _patient(*, age_days: int, name: str = "Dormant Person") -> Patient:
    """A patient whose record is ``age_days`` old.

    created_at is auto_now_add, so it has to be pushed back with an update
    rather than passed to create().
    """
    patient = Patient.objects.create(
        name=name, contact="9876500001", age=30, height_cm=170, weight_kg=65
    )
    moment = timezone.now() - timedelta(days=age_days)
    Patient.objects.filter(pk=patient.pk).update(created_at=moment, updated_at=moment)
    patient.refresh_from_db()
    return patient


@pytest.fixture
def dormant() -> Patient:
    return _patient(age_days=365 * RETENTION_YEARS + 10)


@pytest.fixture
def recent() -> Patient:
    return _patient(age_days=30, name="Recent Person")


class TestIssueNotices:
    def test_a_dormant_record_gets_a_notice(self, service, dormant):
        result = service.issue_notices()
        assert result["issued"] == 1

        notice = RetentionNotice.objects.get()
        assert notice.patient_ref == dormant.pk
        assert notice.is_open

    def test_the_notice_period_is_48_hours(self, service, dormant):
        service.issue_notices()
        notice = RetentionNotice.objects.get()
        hours = (notice.due_at - notice.issued_at).total_seconds() / 3600
        assert round(hours) == ERASURE_NOTICE_HOURS

    def test_a_recent_record_is_left_alone(self, service, recent):
        assert service.issue_notices()["issued"] == 0
        assert RetentionNotice.objects.count() == 0

    def test_issuing_notices_erases_nothing(self, service, dormant):
        """Pass 1 must not delete. That separation is the 48 hours."""
        service.issue_notices()
        assert Patient.objects.filter(pk=dormant.pk).exists()

    def test_it_is_idempotent(self, service, dormant):
        """These run on a schedule; a re-run after a partial failure must not
        double-notify.
        """
        service.issue_notices()
        second = service.issue_notices()
        assert second["issued"] == 0
        assert second["already_under_notice"] == 1
        assert RetentionNotice.objects.count() == 1

    def test_dry_run_writes_nothing(self, service, dormant):
        result = service.issue_notices(dry_run=True)
        assert result["issued"] == 1
        assert RetentionNotice.objects.count() == 0


class TestRunErasures:
    def test_nothing_is_erased_before_the_notice_period_elapses(
        self, service, dormant
    ):
        """The guarantee the two-pass design exists for."""
        service.issue_notices()
        result = service.run_erasures()

        assert result["erased"] == 0
        assert Patient.objects.filter(pk=dormant.pk).exists()

    def test_a_record_is_erased_once_the_notice_is_due(self, service, dormant):
        service.issue_notices()
        _age_notice(hours=ERASURE_NOTICE_HOURS + 1)

        assert service.run_erasures()["erased"] == 1
        assert not Patient.objects.filter(pk=dormant.pk).exists()

    def test_the_notice_is_closed_after_erasure(self, service, dormant):
        service.issue_notices()
        _age_notice(hours=ERASURE_NOTICE_HOURS + 1)
        service.run_erasures()

        notice = RetentionNotice.objects.get()
        assert notice.erased_at is not None
        assert not notice.is_open

    def test_the_erasure_is_recorded_in_the_access_log(self, service, dormant):
        service.issue_notices()
        _age_notice(hours=ERASURE_NOTICE_HOURS + 1)
        service.run_erasures()

        entry = AccessLogEntry.objects.filter(action=AuditAction.DELETE).first()
        assert entry is not None
        assert "retention" in entry.reason

    def test_the_consent_artefact_survives_the_erasure(self, service, dormant):
        """Evidence that the erasure happened and was lawful (AUDIT 4.1)."""
        ConsentArtefact.objects.create(
            patient=dormant, patient_ref=dormant.pk, notice_version="v1"
        )
        service.issue_notices()
        _age_notice(hours=ERASURE_NOTICE_HOURS + 1)
        service.run_erasures()

        artefact = ConsentArtefact.objects.get()
        assert artefact.is_withdrawn
        assert artefact.patient_id is None
        assert artefact.patient_ref == dormant.pk

    def test_history_does_not_survive_the_erasure(self, service, dormant):
        """A history table holds full snapshots including name and contact, so
        it must go with the record or the erasure is a fiction.

        The edit that creates the history has to be pushed back afterwards:
        ``save()`` sets ``updated_at`` to now, and backdating the notice would
        then make that edit look like the person had returned - which the
        service correctly treats as a reason to cancel rather than erase. That
        behaviour has its own test in TestReturningPeopleAreNotErased; here the
        scenario needs to stay a dormant one.
        """
        historical = Patient.history.model
        dormant.age = 31
        dormant.save()
        assert historical.objects.filter(id=dormant.pk).count() == 2

        _mark_dormant(dormant)
        service.issue_notices()
        _age_notice(hours=ERASURE_NOTICE_HOURS + 1)
        assert service.run_erasures()["erased"] == 1

        assert not historical.objects.filter(id=dormant.pk).exists()

    def test_dry_run_erases_nothing(self, service, dormant):
        service.issue_notices()
        _age_notice(hours=ERASURE_NOTICE_HOURS + 1)

        assert service.run_erasures(dry_run=True)["erased"] == 1
        assert Patient.objects.filter(pk=dormant.pk).exists()

    def test_an_already_absent_record_closes_its_notice(self, service, dormant):
        """Erased by a consent withdrawal in the meantime."""
        service.issue_notices()
        _age_notice(hours=ERASURE_NOTICE_HOURS + 1)
        dormant.delete()

        result = service.run_erasures()
        assert result["already_absent"] == 1
        assert RetentionNotice.objects.get().erased_at is not None


class TestReturningPeopleAreNotErased:
    def test_an_edit_inside_the_window_cancels_the_notice(self, service, dormant):
        service.issue_notices()
        dormant.age = 31
        dormant.save()
        _age_notice(hours=ERASURE_NOTICE_HOURS + 1)

        result = service.run_erasures()
        assert result["cancelled"] == 1
        assert result["erased"] == 0
        assert Patient.objects.filter(pk=dormant.pk).exists()

    def test_a_new_symptom_check_cancels_the_notice(self, service, dormant):
        service.issue_notices()
        TriageAssessment.objects.create(patient=dormant, symptoms=["itching"], days=1)
        _age_notice(hours=ERASURE_NOTICE_HOURS + 1)

        assert service.run_erasures()["cancelled"] == 1
        assert Patient.objects.filter(pk=dormant.pk).exists()

    def test_cancel_for_patient_stops_the_clock_directly(self, service, dormant):
        """Called from the intake and triage paths, so simply returning to use
        the service is enough.
        """
        service.issue_notices()
        assert service.cancel_for_patient(dormant) == 1
        assert RetentionNotice.objects.get().cancelled_at is not None

    def test_cancelling_with_no_open_notice_is_harmless(self, service, recent):
        assert service.cancel_for_patient(recent) == 0


class TestManagementCommand:
    def test_a_bare_invocation_is_refused(self):
        """Defaulting to --erase would make an accidental run destructive."""
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("run_retention")

    def test_notices_pass_runs(self, dormant, capsys):
        from django.core.management import call_command

        call_command("run_retention", "--notices", verbosity=0)
        assert RetentionNotice.objects.count() == 1

    def test_both_passes_in_one_invocation_do_not_erase_immediately(self, dormant):
        """A notice issued in this run is not yet due, so running both passes
        together is still safe.
        """
        from django.core.management import call_command

        call_command("run_retention", "--notices", "--erase", verbosity=0)
        assert Patient.objects.filter(pk=dormant.pk).exists()
        assert RetentionNotice.objects.get().is_open


def _age_notice(*, hours: int) -> None:
    """Push the open notice back so its deadline has passed."""
    moment = timezone.now() - timedelta(hours=hours)
    RetentionNotice.objects.update(
        issued_at=moment, due_at=moment + timedelta(hours=ERASURE_NOTICE_HOURS)
    )


def _mark_dormant(patient: Patient) -> None:
    """Push ``updated_at`` far enough back to precede any backdated notice.

    Uses queryset.update so ``auto_now`` does not immediately overwrite it.
    """
    Patient.objects.filter(pk=patient.pk).update(
        updated_at=timezone.now() - timedelta(days=365 * RETENTION_YEARS)
    )
    patient.refresh_from_db()
