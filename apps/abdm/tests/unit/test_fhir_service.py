"""Tests for the FHIR R4 mapping (AUDIT 4.2 M2).

FHIR R4 is a published standard, so structural correctness is verifiable and is
verified here. What is not covered is the NDHM Implementation Guide's profile
constraints on top of base FHIR - those are the ABDM-specific layer and need a
sandbox to confirm.

``TestNoClinicalClaimIsUpgraded`` is the set that matters most. A bundle is a
record leaving the building, and the mapping is exactly where a machine
suggestion could quietly become a diagnosis.
"""

from __future__ import annotations

import pytest

from apps.abdm.models import AbhaLink, RegistrationState
from apps.abdm.services.fhir_service import (
    FhirService,
    bundle_sha256,
    canonical_json,
)
from apps.accounts.models import Clinic, Role
from apps.accounts.services.membership_service import MembershipService
from apps.patients.models import Patient
from apps.triage.models import ReviewState, TriageAssessment, UrgencyLevel
from utils.fhir import find, validate_document_bundle

pytestmark = pytest.mark.django_db


@pytest.fixture
def clinic() -> Clinic:
    return Clinic.objects.create(name="FHIR Clinic", hfr_facility_id="HFR-12345678")


@pytest.fixture
def patient(clinic) -> Patient:
    return Patient.objects.create(
        clinic=clinic, name="Bundle Person", contact="9876500001",
        age=42, height_cm=170, weight_kg=70,
    )


@pytest.fixture
def assessment(clinic, patient) -> TriageAssessment:
    return TriageAssessment.objects.create(
        clinic=clinic,
        patient=patient,
        symptoms=["itching", "skin_rash", "nodal_skin_eruptions"],
        days=4,
        abstained=False,
        differential=[
            {"disease": "Fungal infection", "confidence": 0.79},
            {"disease": "Acne", "confidence": 0.07},
        ],
        urgency_level=UrgencyLevel.ROUTINE,
    )


@pytest.fixture
def service() -> FhirService:
    return FhirService()


class TestStructuralValidity:
    def test_the_bundle_validates(self, service, assessment):
        bundle = service.build_assessment_document(assessment=assessment)
        assert validate_document_bundle(bundle).ok

    def test_it_is_a_document_bundle(self, service, assessment):
        """A document is an attested snapshot; a collection is a bag of
        resources with no statement about what it is.
        """
        bundle = service.build_assessment_document(assessment=assessment)
        assert bundle["type"] == "document"
        assert bundle["timestamp"]

    def test_the_composition_is_first(self, service, assessment):
        bundle = service.build_assessment_document(assessment=assessment)
        assert bundle["entry"][0]["resource"]["resourceType"] == "Composition"

    def test_every_reference_resolves_inside_the_bundle(self, service, assessment):
        """A document that points outside itself is not self-contained."""
        bundle = service.build_assessment_document(assessment=assessment)
        ids = {f"urn:uuid:{e['resource']['id']}" for e in bundle["entry"]}

        def walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("reference"), str):
                    yield node["reference"]
                for key, value in node.items():
                    if key != "reference":
                        yield from walk(value)
            elif isinstance(node, list):
                for item in node:
                    yield from walk(item)

        for target in walk(bundle):
            assert target in ids, f"{target} is not in the bundle"

    def test_it_carries_the_expected_resources(self, service, assessment):
        bundle = service.build_assessment_document(assessment=assessment)

        assert len(find(bundle, "Patient")) == 1
        assert len(find(bundle, "Organization")) == 1
        assert len(find(bundle, "Encounter")) == 1
        assert len(find(bundle, "Observation")) == 1
        assert len(find(bundle, "Condition")) == 2

    def test_the_validator_catches_a_broken_bundle(self):
        """Guards the guard: a validator that passes everything proves nothing."""
        broken = {
            "resourceType": "Bundle",
            "type": "document",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "entry": [
                {
                    "fullUrl": "urn:uuid:a",
                    "resource": {
                        "resourceType": "Patient",  # should be Composition
                        "id": "a",
                        "name": [{"text": "x"}],
                        "managingOrganization": {"reference": "urn:uuid:missing"},
                    },
                }
            ],
        }
        result = validate_document_bundle(broken)
        assert not result.ok
        assert any("Composition" in e for e in result.errors)
        assert any("not in the bundle" in e for e in result.errors)


class TestNoClinicalClaimIsUpgraded:
    """A bundle is a record leaving the building, and the mapping is where a
    machine suggestion could quietly become a diagnosis.
    """

    def test_an_unreviewed_condition_is_unconfirmed(self, service, assessment):
        bundle = service.build_assessment_document(assessment=assessment)
        for condition in find(bundle, "Condition"):
            codes = [c["code"] for c in condition["verificationStatus"]["coding"]]
            assert codes == ["unconfirmed"]

    def test_a_reviewed_condition_is_confirmed(self, service, assessment):
        assessment.review_state = ReviewState.REVIEWED
        assessment.save()

        bundle = service.build_assessment_document(assessment=assessment)
        for condition in find(bundle, "Condition"):
            codes = [c["code"] for c in condition["verificationStatus"]["coding"]]
            assert codes == ["confirmed"]

    def test_the_narrative_says_it_is_automated(self, service, assessment):
        """A receiving system may not surface a status code; the sentence
        survives being rendered as plain text.
        """
        bundle = service.build_assessment_document(assessment=assessment)
        note = find(bundle, "Condition")[0]["note"][0]["text"]

        assert "Not a diagnosis" in note
        assert "automated symptom checker" in note
        assert "NOT REVIEWED BY A CLINICIAN" in note

    def test_a_reviewed_note_drops_the_unreviewed_warning(self, service, assessment):
        assessment.review_state = ReviewState.REVIEWED
        assessment.save()

        bundle = service.build_assessment_document(assessment=assessment)
        note = find(bundle, "Condition")[0]["note"][0]["text"]

        assert "NOT REVIEWED" not in note
        assert "Not a diagnosis" in note, "still not a diagnosis, even reviewed"

    def test_the_match_score_is_worded_as_a_match_not_a_probability(
        self, service, assessment
    ):
        bundle = service.build_assessment_document(assessment=assessment)
        note = find(bundle, "Condition")[0]["note"][0]["text"]

        assert "Symptom match 79%" in note
        assert "not the probability" in note

    def test_no_snomed_code_is_invented(self, service, assessment):
        """Mapping 41 free-text labels to SNOMED without clinician review would
        invent precision. AUDIT 10 lists that mapping as real work.
        """
        bundle = service.build_assessment_document(assessment=assessment)
        condition = find(bundle, "Condition")[0]

        assert "coding" not in condition["code"]
        assert condition["code"]["text"] == "Fungal infection"

    def test_the_symptoms_travel_as_evidence_in_their_own_right(
        self, service, assessment
    ):
        """So a receiving clinician can work from what the patient said without
        trusting our model at all.
        """
        bundle = service.build_assessment_document(assessment=assessment)
        observation = find(bundle, "Observation")[0]

        assert "Itching" in observation["valueString"]
        assert "4 day" in observation["note"][0]["text"]


class TestAbstention:
    def test_an_abstained_assessment_still_produces_a_document(
        self, service, clinic, patient
    ):
        """The symptoms are real and the abstention is information. Suppressing
        it would look like the visit never happened.
        """
        abstained = TriageAssessment.objects.create(
            clinic=clinic, patient=patient, symptoms=["fatigue"], days=2,
            abstained=True, differential=[],
        )
        bundle = service.build_assessment_document(assessment=abstained)

        assert validate_document_bundle(bundle).ok
        assert find(bundle, "Condition") == []
        assert len(find(bundle, "Observation")) == 1

    def test_the_narrative_says_why_there_is_no_condition(
        self, service, clinic, patient
    ):
        abstained = TriageAssessment.objects.create(
            clinic=clinic, patient=patient, symptoms=["fatigue"], days=2,
            abstained=True, differential=[],
        )
        bundle = service.build_assessment_document(assessment=abstained)
        sections = bundle["entry"][0]["resource"]["section"]
        narrative = " ".join(s.get("text", {}).get("div", "") for s in sections)

        assert "did not have enough information" in narrative


class TestAuthorship:
    def test_without_a_clinician_the_author_is_the_organisation(
        self, service, assessment
    ):
        """Attributing a machine-generated summary to a named person who never
        saw it would be the worst kind of convenient default.
        """
        bundle = service.build_assessment_document(assessment=assessment)
        composition = bundle["entry"][0]["resource"]
        org_ref = f"urn:uuid:{find(bundle, 'Organization')[0]['id']}"

        assert composition["author"][0]["reference"] == org_ref
        assert find(bundle, "Practitioner") == []

    def test_with_a_clinician_the_author_is_the_practitioner(
        self, service, assessment, clinic, django_user_model
    ):
        doctor = django_user_model.objects.create_user(username="fhir-doc", password="pw")
        MembershipService().grant(user=doctor, clinic=clinic, role=Role.DOCTOR)

        bundle = service.build_assessment_document(
            assessment=assessment, practitioner=doctor
        )
        practitioner = find(bundle, "Practitioner")[0]
        composition = bundle["entry"][0]["resource"]

        assert composition["author"][0]["reference"] == f"urn:uuid:{practitioner['id']}"


class TestMinimalDisclosure:
    def test_the_mobile_number_is_not_in_the_bundle(self, service, assessment, patient):
        """FHIR has telecom and it would be easy to fill. A receiving system
        has no need for it, and sending the minimum that serves the purpose is
        what purpose limitation means.
        """
        bundle = service.build_assessment_document(assessment=assessment)
        assert patient.contact not in canonical_json(bundle)

    def test_no_birth_date_is_fabricated(self, service, assessment):
        """Intake collects an age, not a date of birth. Deriving one would
        invent precision the record does not have.
        """
        bundle = service.build_assessment_document(assessment=assessment)
        assert "birthDate" not in find(bundle, "Patient")[0]

    def test_the_abha_is_included_when_one_is_linked(
        self, service, assessment, patient, clinic
    ):
        """This is the one identifier that *should* travel: it is how the
        receiving system knows whose record this is.
        """
        link = AbhaLink.objects.create(
            patient=patient, clinic=clinic, abha_number="12345678901234",
            abha_address="bundle@sbx", state=RegistrationState.VERIFIED,
        )
        bundle = service.build_assessment_document(assessment=assessment, abha_link=link)
        identifiers = find(bundle, "Patient")[0]["identifier"]

        assert any(i["value"] == "12345678901234" for i in identifiers)


class TestAnonymousAssessments:
    def test_sharing_an_anonymous_assessment_is_refused(self, service, clinic):
        """Inventing a placeholder patient would create a record about nobody."""
        anonymous = TriageAssessment.objects.create(
            clinic=clinic, patient=None, symptoms=["itching"], days=1
        )
        with pytest.raises(ValueError, match="needs a subject"):
            service.build_assessment_document(assessment=anonymous)


class TestHashing:
    def test_the_same_bundle_hashes_the_same_way(self, service, assessment):
        bundle = service.build_assessment_document(assessment=assessment)
        assert bundle_sha256(bundle) == bundle_sha256(bundle)

    def test_canonical_json_is_key_order_independent(self):
        """The same bundle must hash the same on two machines, or the transfer
        record proves nothing.
        """
        assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})

    def test_a_changed_bundle_hashes_differently(self, service, assessment):
        bundle = service.build_assessment_document(assessment=assessment)
        before = bundle_sha256(bundle)

        bundle["entry"][0]["resource"]["title"] = "Changed"
        assert bundle_sha256(bundle) != before
