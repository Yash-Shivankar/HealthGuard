"""Tests for the access log (AUDIT 8.9)."""

from __future__ import annotations

import pytest

from apps.audit.models import AccessLogEntry, AuditAction
from apps.audit.services.audit_service import AuditService
from apps.patients.models import Patient

pytestmark = pytest.mark.django_db


@pytest.fixture
def service() -> AuditService:
    return AuditService()


@pytest.fixture
def patient() -> Patient:
    return Patient.objects.create(
        name="Audited Person", contact="9876543210", age=30, height_cm=170, weight_kg=65
    )


@pytest.fixture
def staff(django_user_model):
    return django_user_model.objects.create_user(
        username="nurse", password="pw", is_staff=True
    )


class TestRecording:
    def test_records_actor_action_and_object(self, service, staff, patient):
        service.record(action=AuditAction.VIEW_OBJECT, actor=staff, obj=patient)

        entry = AccessLogEntry.objects.get()
        assert entry.actor == staff
        assert entry.actor_label == "nurse"
        assert entry.action == AuditAction.VIEW_OBJECT
        assert entry.object_type == "patients.Patient"
        assert entry.object_id == str(patient.pk)

    def test_records_an_anonymous_actor(self, service, patient):
        service.record(action=AuditAction.VIEW_OBJECT, actor=None, obj=patient)
        assert AccessLogEntry.objects.get().actor_label == "anonymous"

    def test_captures_request_metadata(self, service, staff, patient, rf):
        request = rf.get("/", HTTP_USER_AGENT="TestAgent/1.0")
        service.record(
            action=AuditAction.VIEW_OBJECT, actor=staff, obj=patient, request=request
        )

        entry = AccessLogEntry.objects.get()
        assert entry.ip_address == "127.0.0.1"
        assert entry.user_agent == "TestAgent/1.0"

    def test_a_long_user_agent_is_truncated(self, service, staff, patient, rf):
        request = rf.get("/", HTTP_USER_AGENT="x" * 5000)
        service.record(
            action=AuditAction.VIEW_OBJECT, actor=staff, obj=patient, request=request
        )
        assert len(AccessLogEntry.objects.get().user_agent) <= 300

    def test_a_forwarded_header_does_not_override_remote_addr(
        self, service, staff, patient, rf
    ):
        """X-Forwarded-For is client-controlled unless a proxy overwrites it,
        so trusting it over REMOTE_ADDR would let a caller forge its address.
        """
        request = rf.get("/", HTTP_X_FORWARDED_FOR="1.2.3.4")
        service.record(
            action=AuditAction.VIEW_OBJECT, actor=staff, obj=patient, request=request
        )
        assert AccessLogEntry.objects.get().ip_address == "127.0.0.1"


class TestAppendOnly:
    def test_an_existing_entry_cannot_be_modified(self, service, staff, patient):
        """A log that can be edited is not evidence."""
        service.record(action=AuditAction.VIEW_OBJECT, actor=staff, obj=patient)
        entry = AccessLogEntry.objects.get()
        entry.action = AuditAction.CREATE

        with pytest.raises(ValueError, match="append-only"):
            entry.save()

    def test_an_entry_cannot_be_deleted(self, service, staff, patient):
        service.record(action=AuditAction.VIEW_OBJECT, actor=staff, obj=patient)
        with pytest.raises(ValueError, match="append-only"):
            AccessLogEntry.objects.get().delete()

    def test_the_repository_exposes_no_write_path_beyond_create(self):
        from apps.audit.repositories.access_log_repository import AccessLogRepository

        methods = {m for m in dir(AccessLogRepository) if not m.startswith("_")}
        assert "update" not in methods
        assert "delete" not in methods


class TestResilience:
    def test_a_failed_write_does_not_raise(self, service, staff, patient, monkeypatch):
        """Recording must never break the thing it is recording.

        A patient must not be denied care because a log table was full. The
        trade-off is stated in the service's module docstring: this log is
        best-effort for availability and authoritative for what it contains.
        """

        def boom(**kwargs):
            raise RuntimeError("table is full")

        monkeypatch.setattr(service.repository, "create", boom)
        assert service.record(action=AuditAction.VIEW_OBJECT, actor=staff, obj=patient) is None


class TestErasureInteraction:
    def test_the_log_holds_no_personal_data(self, service, staff, patient):
        """The log survives the patient's erasure, so it must not contain
        anything the erasure was meant to remove.
        """
        service.record(action=AuditAction.VIEW_OBJECT, actor=staff, obj=patient)
        entry = AccessLogEntry.objects.get()
        serialised = " ".join(
            str(getattr(entry, f.name)) for f in AccessLogEntry._meta.fields
        )
        assert patient.name not in serialised
        assert patient.contact not in serialised

    def test_an_entry_outlives_the_object_it_refers_to(self, service, staff, patient):
        patient_pk = patient.pk
        service.record_consent_withdrawal(patient_ref=patient_pk, actor=staff)
        patient.delete()

        entry = AccessLogEntry.objects.get()
        assert entry.object_id == str(patient_pk)
        assert entry.action == AuditAction.CONSENT_WITHDRAWN

    def test_an_entry_survives_the_actors_account_being_removed(
        self, service, staff, patient
    ):
        service.record(action=AuditAction.VIEW_OBJECT, actor=staff, obj=patient)
        staff.delete()

        entry = AccessLogEntry.objects.get()
        assert entry.actor is None
        assert entry.actor_label == "nurse", "the label must keep it readable"
