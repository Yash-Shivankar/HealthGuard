"""Tests for Scan-&-Share (AUDIT 4.2 M1).

The one part of M1 that is fully verifiable without a sandbox, because the QR is
generated locally and what it encodes is a decision rather than a gateway
response.

The test that matters most is ``TestTheQrCarriesNoPatientData``. A QR is a
broadcast: it gets printed, stuck to a desk, photographed by whoever is standing
there, and it cannot be un-shared. Anything patient-specific on it would be
handed to everyone who walks past reception.
"""

from __future__ import annotations

import base64

import pytest

from apps.abdm.models import RegistrationState
from apps.abdm.services.registry_service import FacilityService
from apps.abdm.services.gateway import StubTransport
from apps.abdm.services.scan_share_service import PAYLOAD_VERSION, ScanShareService
from apps.accounts.models import Clinic
from apps.patients.models import Patient
from utils.exceptions.domain import DomainError

pytestmark = pytest.mark.django_db


@pytest.fixture
def clinic() -> Clinic:
    return Clinic.objects.create(
        name="Scan Clinic", contact_email="desk@example.com", contact_phone="9876500001"
    )


@pytest.fixture
def registered(clinic) -> Clinic:
    FacilityService(transport=StubTransport()).register(clinic=clinic)
    clinic.refresh_from_db()
    return clinic


@pytest.fixture
def service() -> ScanShareService:
    return ScanShareService()


class TestGeneration:
    def test_generates_a_code_for_a_registered_facility(self, service, registered):
        code = service.generate(clinic=registered)

        assert code.hfr_id
        assert code.clinic_name == "Scan Clinic"
        assert code.token
        assert code.payload.startswith(PAYLOAD_VERSION)

    def test_an_unregistered_facility_is_refused(self, service, clinic):
        """A QR naming an unregistered facility fails on the patient's phone,
        at the desk, in front of them.
        """
        with pytest.raises(DomainError) as exc:
            service.generate(clinic=clinic)
        assert exc.value.code == "facility_not_registered"

    def test_a_failed_registration_does_not_count_as_registered(
        self, service, clinic
    ):
        from apps.abdm.models import FacilityRegistration

        FacilityRegistration.objects.create(
            clinic=clinic, state=RegistrationState.FAILED, hfr_id="HFR-nope"
        )
        with pytest.raises(DomainError):
            service.generate(clinic=clinic)

    def test_the_svg_is_a_real_image(self, service, registered):
        svg = service.generate(clinic=registered).svg
        # Asserting the structure rather than just "not empty": a broken
        # renderer returning an empty string would otherwise pass.
        assert svg.lstrip().startswith(("<?xml", "<svg"))
        assert "<svg" in svg and "path" in svg

    def test_the_data_uri_round_trips_to_the_same_svg(self, service, registered):
        code = service.generate(clinic=registered)
        assert code.data_uri.startswith("data:image/svg+xml;base64,")
        decoded = base64.b64decode(code.data_uri.split(",", 1)[1]).decode("utf-8")
        assert decoded == code.svg


class TestRotation:
    def test_each_generation_gets_a_new_token(self, service, registered):
        """A static code printed once and left up for a year has been
        photographed and reposted, and there is no way to tell.
        """
        first = service.generate(clinic=registered)
        second = service.generate(clinic=registered)
        assert first.token != second.token
        assert first.payload != second.payload

    def test_an_explicit_token_is_honoured(self, service, registered):
        code = service.generate(clinic=registered, token="fixed-token")
        assert code.token == "fixed-token"
        assert "fixed-token" in code.payload


class TestTheQrCarriesNoPatientData:
    """A QR is a broadcast and cannot be un-shared."""

    @pytest.fixture
    def patient(self, registered) -> Patient:
        return Patient.objects.create(
            clinic=registered, name="Desk Patient", contact="9876500077",
            age=30, height_cm=170, weight_kg=65,
        )

    def test_the_payload_holds_no_patient_name_or_number(
        self, service, registered, patient
    ):
        payload = service.generate(clinic=registered).payload
        assert patient.name not in payload
        assert patient.contact not in payload

    def test_the_payload_holds_no_abha(self, service, registered, patient):
        from apps.abdm.models import AbhaLink

        AbhaLink.objects.create(
            patient=patient, clinic=registered,
            abha_number="12345678901234", abha_address="desk@sbx",
            state=RegistrationState.VERIFIED,
        )
        payload = service.generate(clinic=registered).payload
        assert "12345678901234" not in payload
        assert "desk@sbx" not in payload

    def test_the_payload_is_only_facility_identity(self, service, registered):
        """Four fields, all about the facility. Anything patient-specific
        belongs in the authenticated exchange after the scan.
        """
        parsed = ScanShareService.parse(service.generate(clinic=registered).payload)
        assert set(parsed) == {"version", "hfr_id", "clinic_id", "token"}


class TestParsing:
    def test_round_trips(self, service, registered):
        code = service.generate(clinic=registered)
        parsed = ScanShareService.parse(code.payload)

        assert parsed["hfr_id"] == code.hfr_id
        assert parsed["clinic_id"] == str(registered.pk)
        assert parsed["token"] == code.token

    def test_an_unknown_version_is_refused_clearly(self):
        """A future payload should fail with a message, not be silently
        misread as this one.
        """
        with pytest.raises(DomainError) as exc:
            ScanShareService.parse("hg9|HFR-1|1|token")
        assert exc.value.code == "bad_payload"

    @pytest.mark.parametrize("payload", ["", "garbage", "hg1|too|few", "hg1|a|b|c|d"])
    def test_malformed_payloads_are_refused(self, payload):
        with pytest.raises(DomainError):
            ScanShareService.parse(payload)
