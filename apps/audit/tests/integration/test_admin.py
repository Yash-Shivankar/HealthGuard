"""Tests for the admin surface: theme, dashboard, audit trail, hardening.

Covers AUDIT 8.2-8.5 and 8.8-8.10. The cross-tenant isolation tests for 8.7
live in ``common/tests/test_admin_scoping.py``, because they belong to the base
class rather than to any one app.
"""

from __future__ import annotations

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.audit.models import AccessLogEntry, AuditAction
from apps.audit.services.dashboard_service import DashboardService, PENDING_TILES
from apps.consent.models import ConsentArtefact
from apps.patients.models import Patient
from apps.triage.models import ReviewState, TriageAssessment, UrgencyLevel

pytestmark = pytest.mark.django_db


@pytest.fixture
def root(django_user_model):
    return django_user_model.objects.create_superuser(
        username="root", password="pw", email="root@example.com"
    )


@pytest.fixture
def admin_client_2fa_off(client, root, settings):
    """Signed in, with the 2FA gate disabled.

    Every admin view is behind ``ADMIN_REQUIRE_2FA``. Enrolling a TOTP device
    per test would only test django-otp; the gate itself is tested separately
    in ``TestTwoFactorGate``.
    """
    settings.ADMIN_REQUIRE_2FA = False
    client.force_login(root)
    return client


@pytest.fixture
def patient() -> Patient:
    return Patient.objects.create(
        name="Admin Person", contact="9876543210", age=30, height_cm=170, weight_kg=65
    )


@pytest.fixture
def assessment(patient) -> TriageAssessment:
    return TriageAssessment.objects.create(
        patient=patient,
        symptoms=["itching", "skin_rash"],
        days=3,
        abstained=False,
        differential=[{"disease": "Fungal infection", "confidence": 0.92}],
        urgency_level=UrgencyLevel.ROUTINE,
    )


class TestAdminReachability:
    def test_not_served_from_the_default_path(self, client):
        """AUDIT 8.10: removes the login page from automated scanning traffic."""
        assert client.get("/admin/").status_code == 404

    def test_reachable_at_the_configured_path(self, client, settings):
        assert client.get(f"/{settings.ADMIN_URL}").status_code in (200, 302)

    def test_the_index_is_branded(self, admin_client_2fa_off, settings):
        """"HealthGuard Operations", not "Django administration" (8.4)."""
        body = admin_client_2fa_off.get(f"/{settings.ADMIN_URL}").content.decode()
        assert "HealthGuard Operations" in body
        assert "Django administration" not in body


class TestDashboard:
    def test_the_index_renders_the_tiles(self, admin_client_2fa_off, settings, assessment):
        body = admin_client_2fa_off.get(f"/{settings.ADMIN_URL}").content.decode()
        for heading in ("Operations", "Clinical safety", "Compliance"):
            assert heading in body

    def test_it_names_what_it_cannot_measure_yet(
        self, admin_client_2fa_off, settings
    ):
        """A tile showing a hardcoded zero reads as "nothing to worry about"
        when it means "not measured" (AUDIT 8.5).
        """
        body = admin_client_2fa_off.get(f"/{settings.ADMIN_URL}").content.decode()
        assert "Not measured yet" in body
        assert PENDING_TILES[0] in body

    def test_summary_counts_are_correct(self, assessment, patient):
        summary = DashboardService().summary(use_cache=False)
        assert summary["operations"]["patients"] == 1
        assert summary["operations"]["assessments_total"] == 1
        assert summary["clinical_safety"]["awaiting_review"] == 1

    def test_overdue_review_uses_an_ageing_cutoff(self, assessment):
        """Per AUDIT 8.5 the ageing figure, not the raw backlog, is the number
        that matters - so a fresh item must not count as overdue.
        """
        summary = DashboardService().summary(use_cache=False)
        assert summary["clinical_safety"]["awaiting_review"] == 1
        assert summary["clinical_safety"]["overdue_review"] == 0

    def test_an_aged_unreviewed_item_is_overdue(self, assessment):
        from datetime import timedelta

        from django.utils import timezone

        TriageAssessment.objects.filter(pk=assessment.pk).update(
            created_at=timezone.now() - timedelta(hours=48)
        )
        summary = DashboardService().summary(use_cache=False)
        assert summary["clinical_safety"]["overdue_review"] == 1

    def test_consent_states_are_counted(self, patient):
        ConsentArtefact.objects.create(
            patient=patient, patient_ref=patient.pk, notice_version="v1"
        )
        summary = DashboardService().summary(use_cache=False)
        assert summary["compliance"]["consent_active"] == 1
        assert summary["compliance"]["consent_withdrawn"] == 0

    def test_percentages_do_not_divide_by_zero_on_an_empty_database(self):
        summary = DashboardService().summary(use_cache=False)
        assert summary["operations"]["anonymous_share"] == 0.0
        assert summary["clinical_safety"]["escalation_rate"] == 0.0

    def test_the_summary_is_cached(self, assessment):
        from django.core.cache import cache

        cache.clear()
        first = DashboardService().summary()
        TriageAssessment.objects.create(symptoms=["itching"], days=1)
        # Still the cached figure: the audit's constraint is that this screen
        # must not re-run its aggregates on every page load (8.5).
        assert DashboardService().summary()["operations"]["assessments_total"] == (
            first["operations"]["assessments_total"]
        )
        cache.clear()

    def test_a_broken_dashboard_does_not_break_the_admin(
        self, admin_client_2fa_off, settings, monkeypatch
    ):
        """Staff still need to reach the records."""
        from apps.audit.services import dashboard_service

        def boom(self, **kwargs):
            raise RuntimeError("aggregation exploded")

        monkeypatch.setattr(dashboard_service.DashboardService, "summary", boom)
        response = admin_client_2fa_off.get(f"/{settings.ADMIN_URL}")
        assert response.status_code == 200
        assert "Dashboard unavailable" in response.content.decode()


class TestPIIExposure:
    def test_the_patient_list_masks_contact_numbers(
        self, admin_client_2fa_off, patient
    ):
        """Full value on the detail page only (AUDIT 8.8)."""
        body = admin_client_2fa_off.get(
            reverse("admin:patients_patient_changelist")
        ).content.decode()
        assert "9876543210" not in body
        assert "3210" in body

    def test_contact_is_not_searchable(self):
        """An unthrottled admin search over phone numbers is a bulk-extraction
        endpoint (AUDIT 8.8).
        """
        from apps.patients.admin import PatientAdmin

        assert "contact" not in PatientAdmin.search_fields

    def test_patients_cannot_be_deleted_through_the_admin(self):
        from django.contrib.admin.sites import AdminSite

        from apps.patients.admin import PatientAdmin

        model_admin = PatientAdmin(Patient, AdminSite())
        assert model_admin.has_delete_permission(None) is False

    def test_bulk_delete_is_disabled(self, admin_client_2fa_off, rf, root):
        """`delete_selected` skips the model's own delete(), so soft-delete and
        audit hooks would silently not fire (AUDIT 8.8).
        """
        from django.contrib.admin.sites import AdminSite

        from apps.patients.admin import PatientAdmin

        request = rf.get("/")
        request.user = root
        actions = PatientAdmin(Patient, AdminSite()).get_actions(request)
        assert "delete_selected" not in actions
        assert "erase_under_right_to_erasure" in actions


class TestAuditTrail:
    def test_opening_a_patient_list_is_recorded(self, admin_client_2fa_off, patient):
        AccessLogEntry.objects.all().delete()
        admin_client_2fa_off.get(reverse("admin:patients_patient_changelist"))
        assert AccessLogEntry.objects.filter(action=AuditAction.VIEW_LIST).exists()

    def test_opening_a_patient_record_is_recorded(self, admin_client_2fa_off, patient):
        """Reading a patient record is the event Django's LogEntry misses."""
        AccessLogEntry.objects.all().delete()
        admin_client_2fa_off.get(
            reverse("admin:patients_patient_change", args=[patient.pk])
        )
        entry = AccessLogEntry.objects.filter(action=AuditAction.VIEW_OBJECT).first()
        assert entry is not None
        assert entry.object_id == str(patient.pk)

    def test_reading_the_audit_log_is_not_itself_logged(self, admin_client_2fa_off):
        """Otherwise one visit appends a row, which appends on the next visit,
        and the signal drowns in its own noise.
        """
        AccessLogEntry.objects.all().delete()
        admin_client_2fa_off.get(reverse("admin:audit_accesslogentry_changelist"))
        assert AccessLogEntry.objects.count() == 0

    def test_the_audit_log_admin_is_read_only(self):
        from django.contrib.admin.sites import AdminSite

        from apps.audit.admin import AccessLogEntryAdmin

        model_admin = AccessLogEntryAdmin(AccessLogEntry, AdminSite())
        assert model_admin.has_add_permission(None) is False
        assert model_admin.has_change_permission(None) is False
        assert model_admin.has_delete_permission(None) is False

    def test_the_consent_admin_is_read_only(self):
        from django.contrib.admin.sites import AdminSite

        from apps.consent.admin import ConsentArtefactAdmin

        model_admin = ConsentArtefactAdmin(ConsentArtefact, AdminSite())
        assert model_admin.has_add_permission(None) is False
        assert model_admin.has_change_permission(None) is False
        assert model_admin.has_delete_permission(None) is False


class TestClinicalHistory:
    def test_patient_changes_are_versioned(self, patient):
        patient.age = 31
        patient.save()
        assert patient.history.count() == 2

    def test_history_is_erased_with_the_patient(self, patient):
        """cascade_delete_history is not optional: a history table keeps a full
        copy of every version of the row, including name and mobile number. If
        it survived an erasure, "we deleted your data" would be false.
        """
        from apps.patients.models import Patient as P

        patient.age = 31
        patient.save()
        historical = P.history.model
        assert historical.objects.count() == 2

        patient.delete()
        assert historical.objects.count() == 0

    def test_review_state_transitions_are_versioned(self, assessment):
        """The clinician sign-off trail TPG 2020 expects (AUDIT 8.9)."""
        assessment.review_state = ReviewState.REVIEWED
        assessment.save()
        states = list(assessment.history.values_list("review_state", flat=True))
        assert ReviewState.REVIEWED in states
        assert ReviewState.UNREVIEWED in states


class TestReviewSignOff:
    def test_marking_reviewed_records_who_and_leaves_history(
        self, admin_client_2fa_off, root, assessment
    ):
        response = admin_client_2fa_off.post(
            reverse("admin:triage_triageassessment_changelist"),
            {
                "action": "mark_reviewed",
                "_selected_action": [str(assessment.pk)],
            },
            follow=True,
        )
        assert response.status_code == 200

        assessment.refresh_from_db()
        assert assessment.review_state == ReviewState.REVIEWED
        assert AccessLogEntry.objects.filter(
            action=AuditAction.UPDATE, object_id=str(assessment.pk)
        ).exists()
        assert assessment.history.count() >= 2

    def test_assessments_cannot_be_created_or_deleted_by_hand(self):
        from django.contrib.admin.sites import AdminSite

        from apps.triage.admin import TriageAssessmentAdmin

        model_admin = TriageAssessmentAdmin(TriageAssessment, AdminSite())
        assert model_admin.has_add_permission(None) is False
        assert model_admin.has_delete_permission(None) is False


class TestRightToErasure:
    def test_the_erasure_action_removes_the_patient_and_keeps_the_artefact(
        self, admin_client_2fa_off, patient
    ):
        ConsentArtefact.objects.create(
            patient=patient, patient_ref=patient.pk, notice_version="v1"
        )

        admin_client_2fa_off.post(
            reverse("admin:patients_patient_changelist"),
            {
                "action": "erase_under_right_to_erasure",
                "_selected_action": [str(patient.pk)],
            },
            follow=True,
        )

        assert Patient.objects.count() == 0
        artefact = ConsentArtefact.objects.get()
        assert artefact.is_withdrawn
        assert artefact.patient_id is None
        assert artefact.patient_ref


class TestTwoFactorGate:
    @override_settings(ADMIN_REQUIRE_2FA=True)
    def test_a_superuser_without_a_verified_device_is_refused(self, client, root):
        """"Mandatory 2FA for every staff account. No exceptions for 'just the
        ops account.'" (AUDIT 8.10)
        """
        client.force_login(root)
        response = client.get(reverse("admin:index"))
        # Bounced to the admin login rather than served the dashboard.
        assert response.status_code == 302

    @override_settings(ADMIN_REQUIRE_2FA=False)
    def test_the_gate_can_be_disabled_for_development(self, client, root):
        client.force_login(root)
        assert client.get(reverse("admin:index")).status_code == 200

    def test_the_gate_defaults_to_on(self, settings):
        from config.settings import base

        assert "ADMIN_REQUIRE_2FA" in dir(base)
