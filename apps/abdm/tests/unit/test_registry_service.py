"""Tests for HFR and HPR registration (AUDIT 4.2 M1)."""

from __future__ import annotations

import pytest

from apps.abdm.models import (
    FacilityRegistration,
    GatewayCall,
    PractitionerRegistration,
    RegistrationState,
)
from apps.abdm.services.gateway import GatewayError, StubTransport
from apps.abdm.services.registry_service import FacilityService, PractitionerService
from apps.accounts.models import Clinic
from utils.exceptions.domain import DomainError

pytestmark = pytest.mark.django_db


@pytest.fixture
def clinic() -> Clinic:
    return Clinic.objects.create(
        name="Registry Clinic", contact_email="desk@example.com", contact_phone="9876500001"
    )


@pytest.fixture
def facilities() -> FacilityService:
    return FacilityService(transport=StubTransport())


@pytest.fixture
def practitioners() -> PractitionerService:
    return PractitionerService(transport=StubTransport())


@pytest.fixture
def doctor(django_user_model):
    return django_user_model.objects.create_user(
        username="hpr-doc", password="pw", email="doc@example.com"
    )


class TestFacilityRegistration:
    def test_registers_and_stores_the_hfr_id(self, facilities, clinic):
        registration = facilities.register(clinic=clinic)

        assert registration.state == RegistrationState.VERIFIED
        assert registration.hfr_id.startswith("HFR-")
        assert registration.verified_at is not None

    def test_the_clinic_mirrors_the_id(self, facilities, clinic):
        """Clinic.hfr_facility_id is denormalised so the clinic record reads
        without a join. This service is the only writer of either, which is
        what keeps them in step.
        """
        registration = facilities.register(clinic=clinic)
        clinic.refresh_from_db()
        assert clinic.hfr_facility_id == registration.hfr_id

    def test_it_is_idempotent(self, facilities, clinic):
        """A second facility id for one clinic is worse than none."""
        first = facilities.register(clinic=clinic)
        second = facilities.register(clinic=clinic)

        assert first.pk == second.pk
        assert first.hfr_id == second.hfr_id
        assert FacilityRegistration.objects.count() == 1

    def test_a_failure_is_visible_and_retryable(self, clinic):
        failing = FacilityService(transport=StubTransport(fail_with=GatewayError("down")))

        with pytest.raises(DomainError) as exc:
            failing.register(clinic=clinic)
        assert exc.value.code == "abdm_unavailable"

        registration = FacilityRegistration.objects.get()
        assert registration.state == RegistrationState.FAILED
        assert "down" in registration.last_error

        # And the retry works, leaving one record rather than two.
        FacilityService(transport=StubTransport()).register(clinic=clinic)
        registration.refresh_from_db()
        assert registration.state == RegistrationState.VERIFIED
        assert FacilityRegistration.objects.count() == 1

    def test_a_failed_attempt_is_still_recorded_as_a_call(self, clinic):
        failing = FacilityService(transport=StubTransport(fail_with=GatewayError("down")))
        with pytest.raises(DomainError):
            failing.register(clinic=clinic)

        call = GatewayCall.objects.get()
        assert call.operation == "hfr.register"
        assert call.succeeded is False


class TestPractitionerRegistration:
    def test_registers_and_stores_the_hpr_id(self, practitioners, doctor, clinic):
        registration = practitioners.register(user=doctor, clinic=clinic)

        assert registration.state == RegistrationState.VERIFIED
        assert registration.hpr_id.startswith("HPR-")

    def test_it_is_idempotent(self, practitioners, doctor, clinic):
        first = practitioners.register(user=doctor, clinic=clinic)
        second = practitioners.register(user=doctor, clinic=clinic)

        assert first.pk == second.pk
        assert PractitionerRegistration.objects.count() == 1

    def test_the_same_person_can_register_at_two_clinics(
        self, practitioners, doctor, clinic
    ):
        """ABDM treats the HPR id as the person while facility linkage is
        per-place - the same reason accounts.Membership is a join.
        """
        other = Clinic.objects.create(name="Second Clinic")
        practitioners.register(user=doctor, clinic=clinic)
        practitioners.register(user=doctor, clinic=other)

        assert PractitionerRegistration.objects.filter(user=doctor).count() == 2

    def test_a_second_registration_at_one_clinic_is_refused_by_the_database(
        self, doctor, clinic
    ):
        from django.db.utils import IntegrityError

        PractitionerRegistration.objects.create(user=doctor, clinic=clinic)
        with pytest.raises(IntegrityError):
            PractitionerRegistration.objects.create(user=doctor, clinic=clinic)

    def test_a_failure_is_visible(self, doctor, clinic):
        failing = PractitionerService(
            transport=StubTransport(fail_with=GatewayError("down"))
        )
        with pytest.raises(DomainError):
            failing.register(user=doctor, clinic=clinic)

        assert PractitionerRegistration.objects.get().state == RegistrationState.FAILED


class TestCallRecording:
    def test_every_call_is_recorded_before_the_result_is_known(
        self, facilities, clinic
    ):
        facilities.register(clinic=clinic)
        call = GatewayCall.objects.get()

        assert call.operation == "hfr.register"
        assert call.succeeded is True
        assert call.request_id
        assert call.duration_ms is not None

    def test_stub_calls_are_flagged_simulated(self, facilities, clinic):
        facilities.register(clinic=clinic)
        assert GatewayCall.objects.get().simulated is True

    def test_call_records_cannot_be_modified(self, facilities, clinic):
        """Append-only, like the access log: a call record is evidence."""
        facilities.register(clinic=clinic)
        call = GatewayCall.objects.get()
        call.succeeded = False

        with pytest.raises(ValueError, match="append-only"):
            call.save()


class TestTransportSelection:
    def test_the_stub_is_the_default(self, settings):
        """A project that cannot run without third-party credentials is a
        project nobody can contribute to.
        """
        from apps.abdm.services.gateway import StubTransport, build_transport

        settings.ABDM_ENABLED = False
        assert isinstance(build_transport(), StubTransport)

    def test_enabling_without_credentials_falls_back_to_the_stub(self, settings):
        """Refusing to start would be worse; silently making live calls with
        blank credentials would be worse still.
        """
        from apps.abdm.services.gateway import StubTransport, build_transport

        settings.ABDM_ENABLED = True
        settings.ABDM_CLIENT_ID = ""
        settings.ABDM_CLIENT_SECRET = ""
        assert isinstance(build_transport(), StubTransport)

    def test_credentials_plus_the_flag_select_the_real_transport(self, settings):
        from apps.abdm.services.gateway import HttpTransport, build_transport

        settings.ABDM_ENABLED = True
        settings.ABDM_BASE_URL = "https://sandbox.example"
        settings.ABDM_CLIENT_ID = "id"
        settings.ABDM_CLIENT_SECRET = "secret"
        assert isinstance(build_transport(), HttpTransport)
