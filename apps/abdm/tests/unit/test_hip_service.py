"""Tests for sharing records under an ABDM consent (AUDIT 4.2 M2).

The consent gate is what these are for. Four questions, and failing any one
means no disclosure - including the fourth, which looks redundant and is the
one that catches a cross-patient leak every other check waves through.

They also pin the distinction the models are careful about: **HIE-CM consent is
not DPDP consent.** Holding one does not grant the other, and treating either
as the other would be a disclosure without a lawful basis.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.abdm.models import (
    ConsentState,
    HealthInformationTransfer,
    HieConsentArtefact,
    HiType,
    TransferState,
)
from apps.abdm.services import crypto
from apps.abdm.services.gateway import GatewayError, StubTransport
from apps.abdm.services.hip_service import HipService
from apps.accounts.models import Clinic
from apps.audit.models import AccessLogEntry, AuditAction
from apps.patients.models import Patient
from apps.triage.models import TriageAssessment
from utils.exceptions.domain import ConsentRequiredError, DomainError

pytestmark = pytest.mark.django_db


@pytest.fixture
def clinic() -> Clinic:
    return Clinic.objects.create(name="HIP Clinic", hfr_facility_id="HFR-999")


@pytest.fixture
def patient(clinic) -> Patient:
    return Patient.objects.create(
        clinic=clinic, name="Shared Person", contact="9876500001",
        age=40, height_cm=168, weight_kg=64,
    )


@pytest.fixture
def assessment(clinic, patient) -> TriageAssessment:
    return TriageAssessment.objects.create(
        clinic=clinic, patient=patient,
        symptoms=["itching", "skin_rash"], days=3,
        abstained=False,
        differential=[{"disease": "Fungal infection", "confidence": 0.8}],
    )


@pytest.fixture
def consent(clinic, patient) -> HieConsentArtefact:
    return HieConsentArtefact.objects.create(
        patient=patient,
        clinic=clinic,
        consent_id="consent-abc",
        requester_id="hiu-1",
        requester_name="Referral Hospital",
        state=ConsentState.GRANTED,
        hi_types=[HiType.OP_CONSULTATION],
        granted_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=30),
    )


@pytest.fixture
def requester():
    """The HIU's side of the key exchange."""
    return crypto.generate_key_material()


@pytest.fixture
def service() -> HipService:
    return HipService(transport=StubTransport())


def _share(service, consent, assessment, requester, **kwargs):
    return service.share(
        consent=consent,
        assessment=assessment,
        requester_public_key_b64=requester.public_key_b64,
        requester_nonce_b64=requester.nonce_b64,
        **kwargs,
    )


class TestTheConsentGate:
    def test_a_usable_consent_permits_sharing(self, service, consent, assessment, requester):
        transfer = _share(service, consent, assessment, requester)
        assert transfer.state == TransferState.SENT

    def test_a_revoked_consent_is_refused(self, service, consent, assessment, requester):
        consent.revoked_at = timezone.now()
        consent.state = ConsentState.REVOKED
        consent.save()

        with pytest.raises(ConsentRequiredError) as exc:
            _share(service, consent, assessment, requester)
        assert exc.value.code == "abdm_consent_not_usable"

    def test_an_expired_consent_is_refused_even_while_still_marked_granted(
        self, service, consent, assessment, requester
    ):
        """Expiry is evaluated live. A consent that lapsed five minutes ago
        still reads `granted` until some job notices, and honouring it in the
        meantime would be a disclosure without a basis.
        """
        HieConsentArtefact.objects.filter(pk=consent.pk).update(
            expires_at=timezone.now() - timedelta(minutes=5)
        )
        consent.refresh_from_db()
        assert consent.state == ConsentState.GRANTED

        with pytest.raises(ConsentRequiredError):
            _share(service, consent, assessment, requester)

    def test_a_consent_that_was_never_granted_is_refused(
        self, service, consent, assessment, requester
    ):
        consent.state = ConsentState.REQUESTED
        consent.save()

        with pytest.raises(ConsentRequiredError):
            _share(service, consent, assessment, requester)

    def test_a_consent_for_a_different_category_is_refused(
        self, service, consent, assessment, requester
    ):
        consent.hi_types = [HiType.PRESCRIPTION]
        consent.save()

        with pytest.raises(ConsentRequiredError) as exc:
            _share(service, consent, assessment, requester)
        assert exc.value.code == "abdm_consent_scope"

    def test_a_record_outside_the_consented_date_range_is_refused(
        self, service, consent, assessment, requester
    ):
        consent.records_from = timezone.now() + timedelta(days=1)
        consent.save()

        with pytest.raises(ConsentRequiredError) as exc:
            _share(service, consent, assessment, requester)
        assert exc.value.code == "abdm_consent_date_range"

    def test_another_patients_record_is_refused_under_a_valid_consent(
        self, service, consent, clinic, requester
    ):
        """The check that looks redundant and is not: a consent authorises
        disclosure of one person's records, and this is the only check that
        catches assembling someone else's.
        """
        other = Patient.objects.create(
            clinic=clinic, name="Different Person", contact="9876500002",
            age=50, height_cm=160, weight_kg=58,
        )
        theirs = TriageAssessment.objects.create(
            clinic=clinic, patient=other, symptoms=["headache"], days=1,
            differential=[{"disease": "Migraine", "confidence": 0.6}],
        )

        with pytest.raises(DomainError) as exc:
            _share(service, consent, theirs, requester)
        assert exc.value.code == "abdm_consent_subject_mismatch"

    def test_nothing_is_sent_when_the_gate_refuses(
        self, service, consent, assessment, requester
    ):
        consent.state = ConsentState.REVOKED
        consent.save()

        with pytest.raises(ConsentRequiredError):
            _share(service, consent, assessment, requester)

        assert HealthInformationTransfer.objects.count() == 0

    def test_check_consent_can_be_called_without_side_effects(
        self, service, consent, assessment
    ):
        """A UI greying out a button needs the answer without the transfer."""
        service.check_consent(consent=consent, assessment=assessment)
        assert HealthInformationTransfer.objects.count() == 0


class TestHieConsentIsNotDpdpConsent:
    """Holding one does not grant the other."""

    def test_dpdp_consent_alone_does_not_permit_an_abdm_disclosure(
        self, service, assessment, requester, patient, clinic
    ):
        from apps.consent.models import ConsentPurpose
        from apps.consent.services.consent_service import ConsentService

        ConsentService().grant(patient=patient, purpose=ConsentPurpose.SYMPTOM_CHECK)
        ConsentService().grant(patient=patient, purpose=ConsentPurpose.ABDM_LINKAGE)

        # Both DPDP purposes held, but no HIE-CM artefact exists.
        assert HieConsentArtefact.objects.count() == 0

        unusable = HieConsentArtefact.objects.create(
            patient=patient, clinic=clinic, requester_id="hiu-1",
            state=ConsentState.REQUESTED,
            hi_types=[HiType.OP_CONSULTATION],
            expires_at=timezone.now() + timedelta(days=1),
        )
        with pytest.raises(ConsentRequiredError):
            _share(service, unusable, assessment, requester)

    def test_an_abdm_consent_has_an_expiry_by_construction(self, consent):
        """A consent with no expiry cannot lapse by doing nothing."""
        assert consent.expires_at is not None

    def test_it_cascades_with_the_patient(self, consent, patient):
        """An erased patient has no consents to honour."""
        assert HieConsentArtefact.objects.count() == 1
        patient.delete()
        assert HieConsentArtefact.objects.count() == 0


class TestTheTransferRecord:
    def test_the_bundle_is_not_stored(self, service, consent, assessment, requester):
        """Storing it would be a second copy of clinical data outside the
        erasure path - the same mistake the access log avoids.
        """
        transfer = _share(service, consent, assessment, requester)
        stored = json.dumps(
            {
                f.name: getattr(transfer, f.name)
                for f in HealthInformationTransfer._meta.fields
            },
            default=str,
        )

        assert "Shared Person" not in stored
        assert "Fungal infection" not in stored
        assert "itching" not in stored

    def test_the_hash_records_what_was_sent(self, service, consent, assessment, requester):
        transfer = _share(service, consent, assessment, requester)
        assert len(transfer.bundle_sha256) == 64
        assert transfer.entry_count > 0

    def test_a_disclosure_is_recorded_in_the_access_log(
        self, service, consent, assessment, requester
    ):
        AccessLogEntry.objects.all()._raw_delete(AccessLogEntry.objects.db)
        _share(service, consent, assessment, requester)

        entry = AccessLogEntry.objects.filter(action=AuditAction.EXPORT).first()
        assert entry is not None
        assert "ABDM consent" in entry.reason
        # By reference, never content.
        assert "Shared Person" not in entry.reason

    def test_a_gateway_failure_leaves_the_attempt_visible(
        self, consent, assessment, requester
    ):
        failing = HipService(transport=StubTransport(fail_with=GatewayError("relay down")))

        with pytest.raises(DomainError) as exc:
            failing.share(
                consent=consent, assessment=assessment,
                requester_public_key_b64=requester.public_key_b64,
                requester_nonce_b64=requester.nonce_b64,
            )
        assert exc.value.code == "abdm_transfer_failed"

        transfer = HealthInformationTransfer.objects.get()
        assert transfer.state == TransferState.FAILED
        assert "relay down" in transfer.last_error

    def test_a_malformed_requester_key_fails_before_any_call(
        self, service, consent, assessment
    ):
        from apps.abdm.models import GatewayCall

        with pytest.raises(DomainError):
            service.share(
                consent=consent, assessment=assessment,
                requester_public_key_b64="not-a-key",
                requester_nonce_b64="AAAA",
            )
        assert not GatewayCall.objects.filter(operation="hi.transfer").exists()


class TestTheHiuCanActuallyReadIt:
    def test_the_requester_decrypts_what_we_sent(
        self, service, consent, assessment, requester, monkeypatch
    ):
        """End to end: assemble, encrypt, and confirm the intended recipient
        recovers the exact bundle. Without this the encryption could be
        producing well-formed ciphertext nobody can open.
        """
        captured = {}

        original = crypto.encrypt

        def capture(**kwargs):
            payload = original(**kwargs)
            captured["payload"] = payload
            captured["plaintext"] = kwargs["plaintext"]
            return payload

        monkeypatch.setattr(crypto, "encrypt", capture)
        _share(service, consent, assessment, requester)

        recovered = crypto.decrypt(payload=captured["payload"], receiver=requester)
        assert recovered == captured["plaintext"]

        bundle = json.loads(recovered.decode("utf-8"))
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "document"
