"""Tests for ABHA linkage (AUDIT 4.2 M1).

What is tested here is everything *except* the wire format: consent gating,
idempotency, the failure path, masking, and that the ABHA never reaches the
access log. Those are the parts that are mine to get right.

The wire format is not tested against reality because there is no sandbox
(AUDIT 11.3). The stub transport is shaped to match what the services consume,
not to imitate ABDM byte for byte - imitating an unverified spec in detail would
manufacture confidence that the integration works.
"""

from __future__ import annotations

import json

import pytest

from apps.abdm.models import AbhaLink, GatewayCall, RegistrationState
from apps.abdm.services.abha_service import AbhaService
from apps.abdm.services.gateway import GatewayError, StubTransport
from apps.accounts.models import Clinic
from apps.audit.models import AccessLogEntry
from apps.consent.models import ConsentPurpose
from apps.consent.services.consent_service import ConsentService
from apps.patients.models import Patient
from utils.exceptions.domain import ConsentRequiredError, DomainError, ValidationError

pytestmark = pytest.mark.django_db


@pytest.fixture
def clinic() -> Clinic:
    return Clinic.objects.create(name="ABDM Clinic")


@pytest.fixture
def patient(clinic) -> Patient:
    return Patient.objects.create(
        clinic=clinic, name="Linked Person", contact="9876500001",
        age=35, height_cm=170, weight_kg=68,
    )


@pytest.fixture
def consented(patient):
    """A patient who has agreed to ABDM linkage specifically."""
    ConsentService().grant(patient=patient, purpose=ConsentPurpose.ABDM_LINKAGE)
    return patient


@pytest.fixture
def transport() -> StubTransport:
    return StubTransport()


@pytest.fixture
def service(transport) -> AbhaService:
    return AbhaService(transport=transport)


class TestConsentGating:
    def test_creating_without_linkage_consent_is_refused(self, service, patient):
        """Agreeing to a symptom check is not agreeing to link a national
        health identifier (AUDIT 4.1 - notice is itemised per purpose).
        """
        ConsentService().grant(patient=patient, purpose=ConsentPurpose.SYMPTOM_CHECK)

        with pytest.raises(ConsentRequiredError):
            service.create(patient=patient)

    def test_verifying_without_linkage_consent_is_refused(self, service, patient):
        with pytest.raises(ConsentRequiredError):
            service.verify(patient=patient, abha_number="12345678901234")

    def test_nothing_is_written_when_consent_is_missing(self, service, patient):
        with pytest.raises(ConsentRequiredError):
            service.create(patient=patient)
        assert AbhaLink.objects.count() == 0
        assert GatewayCall.objects.count() == 0, "no call should reach the gateway"

    def test_withdrawn_linkage_consent_stops_further_linking(self, service, consented):
        ConsentService().withdraw_all(patient=consented)
        with pytest.raises(ConsentRequiredError):
            service.create(patient=consented)

    def test_the_artefact_that_permitted_it_is_recorded(self, service, consented):
        link = service.create(patient=consented)
        assert link.consent_artefact is not None
        assert link.consent_artefact.purpose == ConsentPurpose.ABDM_LINKAGE


class TestCreate:
    def test_creates_a_verified_link(self, service, consented):
        link = service.create(patient=consented)

        assert link.state == RegistrationState.VERIFIED
        assert len(link.abha_number) == 14
        assert link.abha_address
        assert link.verified_at is not None

    def test_it_is_idempotent(self, service, consented):
        """A retry after a timeout must not mint a second national identity."""
        first = service.create(patient=consented)
        second = service.create(patient=consented)

        assert first.pk == second.pk
        assert first.abha_number == second.abha_number
        assert AbhaLink.objects.count() == 1

    def test_the_link_belongs_to_the_patients_clinic(self, service, consented, clinic):
        assert service.create(patient=consented).clinic_id == clinic.pk


class TestVerify:
    def test_links_an_existing_abha(self, service, consented):
        link = service.verify(patient=consented, abha_number="12-3456-7890-1234")

        assert link.state == RegistrationState.VERIFIED
        assert link.abha_number == "12345678901234", "stored as digits only"

    def test_a_short_number_is_rejected_before_any_call(self, service, consented):
        with pytest.raises(ValidationError):
            service.verify(patient=consented, abha_number="123")
        assert GatewayCall.objects.count() == 0

    def test_an_abha_already_on_another_record_is_refused(
        self, service, consented, clinic
    ):
        """Two patient records for one national identity cannot both be right,
        and merging them later is far harder than refusing now.
        """
        service.verify(patient=consented, abha_number="12345678901234")

        other = Patient.objects.create(
            clinic=clinic, name="Someone Else", contact="9876500002",
            age=44, height_cm=160, weight_kg=60,
        )
        ConsentService().grant(patient=other, purpose=ConsentPurpose.ABDM_LINKAGE)

        with pytest.raises(DomainError) as exc:
            service.verify(patient=other, abha_number="12345678901234")
        assert exc.value.code == "abha_already_linked"

    def test_relinking_the_same_number_to_the_same_patient_is_fine(
        self, service, consented
    ):
        service.verify(patient=consented, abha_number="12345678901234")
        again = service.verify(patient=consented, abha_number="12345678901234")
        assert again.state == RegistrationState.VERIFIED


class TestFailureHandling:
    def test_a_gateway_failure_leaves_the_attempt_visible(self, consented):
        """Rolling the record away would leave the front desk wondering whether
        they clicked the button.
        """
        service = AbhaService(
            transport=StubTransport(fail_with=GatewayError("gateway down", retryable=True))
        )

        with pytest.raises(DomainError) as exc:
            service.create(patient=consented)
        assert exc.value.code == "abdm_unavailable"

        link = AbhaLink.objects.get()
        assert link.state == RegistrationState.FAILED
        assert "gateway down" in link.last_error

    def test_a_failed_attempt_is_still_recorded_as_a_call(self, consented):
        """A gap in the call record is worst exactly when the integration is
        least stable.
        """
        service = AbhaService(transport=StubTransport(fail_with=GatewayError("boom")))
        with pytest.raises(DomainError):
            service.create(patient=consented)

        call = GatewayCall.objects.get()
        assert call.succeeded is False
        assert call.operation == "abha.create"

    def test_a_retry_after_failure_can_succeed(self, consented):
        failing = AbhaService(transport=StubTransport(fail_with=GatewayError("boom")))
        with pytest.raises(DomainError):
            failing.create(patient=consented)

        working = AbhaService(transport=StubTransport())
        link = working.create(patient=consented)
        assert link.state == RegistrationState.VERIFIED
        assert AbhaLink.objects.count() == 1


class TestTheAbhaIsTreatedAsSensitive:
    def test_str_is_masked(self, service, consented):
        link = service.create(patient=consented)
        assert link.abha_number not in str(link)
        assert link.abha_number[-4:] in str(link)

    def test_the_access_log_never_holds_the_number(self, service, consented):
        """The access log outlives the patient record, so an ABHA in it would
        be a permanent nationally-resolvable pointer to someone whose data was
        erased.
        """
        link = service.create(patient=consented)
        serialised = json.dumps(list(AccessLogEntry.objects.values()), default=str)
        assert link.abha_number not in serialised
        assert link.abha_address not in serialised

    def test_the_call_record_holds_no_payload(self, service, consented):
        call = GatewayCall.objects.filter(operation="abha.create").first()
        service.create(patient=consented)
        call = GatewayCall.objects.filter(operation="abha.create").first()

        stored = json.dumps(
            {f.name: getattr(call, f.name) for f in GatewayCall._meta.fields}, default=str
        )
        assert "Linked Person" not in stored
        assert "9876500001" not in stored

    def test_the_link_is_erased_with_the_patient(self, service, consented):
        """An ABHA left behind after a DPDP erasure is a permanent pointer to
        the person whose data you just promised to delete.
        """
        service.create(patient=consented)
        assert AbhaLink.objects.count() == 1

        consented.delete()
        assert AbhaLink.objects.count() == 0

    def test_formatted_number_groups_the_digits(self, service, consented):
        link = service.verify(patient=consented, abha_number="12345678901234")
        assert link.formatted_number == "12-3456-7890-1234"


class TestSimulationIsVisible:
    def test_stub_calls_are_flagged_simulated(self, service, consented):
        """Without this a sandbox-era record is indistinguishable from a
        production one, which makes the audit trail misleading rather than
        merely incomplete.
        """
        service.create(patient=consented)
        assert GatewayCall.objects.filter(simulated=True).exists()
        assert not GatewayCall.objects.filter(simulated=False).exists()


class TestUnlink:
    def test_unlinking_removes_the_local_record(self, service, consented):
        service.create(patient=consented)
        service.unlink(patient=consented)
        assert AbhaLink.objects.count() == 0

    def test_unlinking_is_recorded(self, service, consented):
        service.create(patient=consented)
        AccessLogEntry.objects.all()._raw_delete(AccessLogEntry.objects.db)

        service.unlink(patient=consented)
        entry = AccessLogEntry.objects.filter(object_type="abdm.AbhaLink").first()
        assert entry is not None
        assert "unlinked" in entry.reason

    def test_unlinking_when_there_is_no_link_is_harmless(self, service, consented):
        service.unlink(patient=consented)
