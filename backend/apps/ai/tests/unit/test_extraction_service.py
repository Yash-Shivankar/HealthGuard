"""Tests for the extraction service (AUDIT 6, Tier 1).

Two things being established here beyond "it works": that consent is checked
before anything leaves, and that redaction happens only when something is about
to leave. The second matters because redacting on the local path would be
pointless work that also degrades the result.
"""

from __future__ import annotations

import pytest

from apps.ai.models import AiCall, AiCapability, DraftState, InputLanguage
from apps.ai.services import providers
from apps.ai.services.extraction_service import MAX_TEXT_CHARS, ExtractionService
from apps.accounts.models import Clinic
from apps.consent.models import ConsentPurpose
from apps.consent.services.consent_service import ConsentService
from apps.patients.models import Patient
from utils.exceptions.domain import ConsentRequiredError, ValidationError

pytestmark = pytest.mark.django_db


@pytest.fixture
def clinic():
    return Clinic.objects.create(name="Extraction Clinic")


@pytest.fixture
def patient(clinic):
    return Patient.objects.create(
        clinic=clinic, name="Sunita Deshmukh", contact="9876500001",
        age=41, height_cm=160, weight_kg=58,
    )


@pytest.fixture
def service():
    return ExtractionService(extractor=providers.LexiconExtractor())


class FakeLlm:
    """Stands in for a configured external extractor.

    A fake rather than a mock so it can record what it was handed: the assertion
    that matters is about the payload, and a mock's call args are a weaker way to
    say the same thing.
    """

    name = "llm"
    simulated = False

    def __init__(self):
        self.received: list[str] = []

    def extract(self, *, text, language):
        self.received.append(text)
        return providers.Extraction(
            matches=[providers.SymptomMatch("itching", "itching", 0.9)],
            unmapped=[],
            language=language,
            provider=self.name,
            model_version="fake-1",
            simulated=False,
            external=True,
        )


class ExternalFakeLlm(FakeLlm, providers.LlmExtractor):
    """Passes the ``isinstance`` check the service uses to decide externality.

    The service asks the instance rather than reading settings, because the
    instance is what will actually run - a factory that fell back to the local
    path must not trigger a consent check the patient does not need.
    """

    def __init__(self):
        FakeLlm.__init__(self)


class TestItProducesADraft:
    def test_a_description_becomes_proposed_symptoms(self, service, clinic, patient):
        draft = service.extract(
            text="I have itching and a skin rash", language="en",
            patient=patient, clinic=clinic,
        )
        assert set(draft.proposed_symptom_names) == {"itching", "skin_rash"}

    def test_it_starts_as_a_draft(self, service, clinic, patient):
        draft = service.extract(text="itching", patient=patient, clinic=clinic)
        assert draft.state == DraftState.DRAFT

    def test_the_patients_own_words_are_kept(self, service, clinic, patient):
        draft = service.extract(
            text="mujhe khujli hai", language="hi", patient=patient, clinic=clinic
        )
        assert draft.source_text == "mujhe khujli hai"

    def test_the_language_is_recorded(self, service, clinic, patient):
        """The one fairness-adjacent label this project can honestly collect."""
        draft = service.extract(text="khujli", language="hi", patient=patient, clinic=clinic)
        assert draft.language == InputLanguage.HINDI

    def test_an_unknown_language_is_stored_as_unknown_not_guessed(
        self, service, clinic, patient
    ):
        draft = service.extract(text="itching", language="fr", patient=patient, clinic=clinic)
        assert draft.language == InputLanguage.UNKNOWN

    def test_understanding_nothing_is_distinct_from_no_symptoms(
        self, service, clinic, patient
    ):
        """The UI must not present the first as the second."""
        draft = service.extract(
            text="my tongue feels furry and metallic", patient=patient, clinic=clinic
        )
        assert draft.understood_nothing is True
        assert draft.unmapped_phrases

    def test_the_provenance_is_stamped(self, service, clinic, patient):
        draft = service.extract(text="itching", patient=patient, clinic=clinic)
        assert draft.provider == "lexicon"
        assert draft.model_version

    def test_a_lexicon_draft_is_not_simulated(self, service, clinic, patient):
        """Which is what makes it confirmable."""
        draft = service.extract(text="itching", patient=patient, clinic=clinic)
        assert draft.simulated is False
        assert draft.can_be_confirmed is True

    def test_persist_false_leaves_no_row(self, service, clinic, patient):
        from apps.ai.models import IntakeDraft

        draft = service.extract(
            text="itching", patient=patient, clinic=clinic, persist=False
        )
        assert draft.pk is None
        assert IntakeDraft.objects.count() == 0

    def test_an_anonymous_draft_is_allowed_on_the_local_path(self, service, clinic):
        """Intake can be run before anyone is identified, and requiring identity
        for a function that does not need it is what DPDP notice obligations
        exist to discourage.
        """
        draft = service.extract(text="itching", patient=None, clinic=clinic)
        assert draft.patient_id is None


class TestInputValidation:
    def test_empty_text_is_refused(self, service, clinic, patient):
        with pytest.raises(ValidationError) as exc:
            service.extract(text="   ", patient=patient, clinic=clinic)
        assert exc.value.code == "ai_empty_text"

    def test_an_enormous_description_is_refused_rather_than_truncated(
        self, service, clinic, patient
    ):
        """Silently dropping the second half of what someone told you is worse
        than asking them to shorten it.
        """
        with pytest.raises(ValidationError) as exc:
            service.extract(text="x" * (MAX_TEXT_CHARS + 1), patient=patient, clinic=clinic)
        assert exc.value.code == "ai_text_too_long"


class TestTheLocalPathNeedsNoExtraConsent:
    """Our own processing, on this machine, of data already held under
    SYMPTOM_CHECK. Demanding a second consent for it would train people to click
    through the one that matters.
    """

    def test_no_ai_assistance_consent_is_required(self, service, clinic, patient):
        assert not ConsentService().has_active(
            patient=patient, purpose=ConsentPurpose.AI_ASSISTANCE
        )
        draft = service.extract(text="itching", patient=patient, clinic=clinic)
        assert draft.proposed_symptom_names == ["itching"]

    def test_nothing_is_recorded_as_external(self, service, clinic, patient):
        service.extract(text="itching", patient=patient, clinic=clinic)
        call = AiCall.objects.get(capability=AiCapability.EXTRACTION)
        assert call.external is False
        assert call.redacted is False


class TestTheExternalPathNeedsConsent:
    def test_it_is_refused_without_consent(self, clinic, patient):
        service = ExtractionService(extractor=ExternalFakeLlm())
        with pytest.raises(ConsentRequiredError) as exc:
            service.extract(text="itching", patient=patient, clinic=clinic)
        assert exc.value.code == "ai_assistance_consent_required"

    def test_it_proceeds_once_granted(self, clinic, patient):
        ConsentService().grant(patient=patient, purpose=ConsentPurpose.AI_ASSISTANCE)
        service = ExtractionService(extractor=ExternalFakeLlm())

        draft = service.extract(text="itching", patient=patient, clinic=clinic)
        assert draft.externally_processed is True

    def test_symptom_check_consent_is_not_enough(self, clinic, patient):
        """Itemised means itemised. Agreeing to a symptom check is not agreeing
        that your description goes to a third party.
        """
        ConsentService().grant(patient=patient, purpose=ConsentPurpose.SYMPTOM_CHECK)
        service = ExtractionService(extractor=ExternalFakeLlm())

        with pytest.raises(ConsentRequiredError):
            service.extract(text="itching", patient=patient, clinic=clinic)

    def test_an_anonymous_patient_cannot_use_the_external_path(self, clinic):
        """There is nobody to have consented."""
        service = ExtractionService(extractor=ExternalFakeLlm())
        with pytest.raises(ConsentRequiredError) as exc:
            service.extract(text="itching", patient=None, clinic=clinic)
        assert exc.value.code == "ai_external_needs_identified_patient"

    def test_nothing_is_sent_when_consent_is_missing(self, clinic, patient):
        extractor = ExternalFakeLlm()
        service = ExtractionService(extractor=extractor)

        with pytest.raises(ConsentRequiredError):
            service.extract(text="itching", patient=patient, clinic=clinic)
        assert extractor.received == []


class TestRedactionHappensOnlyOnTheWayOut:
    def test_identifiers_are_stripped_before_an_external_call(self, clinic, patient):
        ConsentService().grant(patient=patient, purpose=ConsentPurpose.AI_ASSISTANCE)
        extractor = ExternalFakeLlm()
        ExtractionService(extractor=extractor).extract(
            text="Sunita here, ring 9876500001, I have itching",
            patient=patient, clinic=clinic,
        )

        sent = extractor.received[0]
        assert "Sunita" not in sent
        assert "9876500001" not in sent
        assert "itching" in sent, "the clinical content must survive"

    def test_the_unredacted_words_are_still_what_we_store(self, clinic, patient):
        """The draft is the patient's own record and cascades with them; the
        redacted copy existed only for the wire.
        """
        ConsentService().grant(patient=patient, purpose=ConsentPurpose.AI_ASSISTANCE)
        draft = ExtractionService(extractor=ExternalFakeLlm()).extract(
            text="Sunita here, I have itching", patient=patient, clinic=clinic
        )
        assert "Sunita" in draft.source_text

    def test_the_call_log_records_that_redaction_ran(self, clinic, patient):
        ConsentService().grant(patient=patient, purpose=ConsentPurpose.AI_ASSISTANCE)
        ExtractionService(extractor=ExternalFakeLlm()).extract(
            text="I have itching", patient=patient, clinic=clinic
        )
        call = AiCall.objects.get(capability=AiCapability.EXTRACTION)
        assert call.external is True
        assert call.redacted is True

    def test_the_local_path_is_not_redacted(self, service, clinic, patient):
        """Pointless work that also degrades the result - the lexicon does better
        with the sentence intact, and nothing has left the building.
        """
        draft = service.extract(
            text="Sunita here, I have itching", patient=patient, clinic=clinic
        )
        assert "Sunita" in draft.source_text
        assert AiCall.objects.get().redacted is False


class TestTheCallLogKeepsShapeNotContent:
    def test_no_text_is_stored(self, service, clinic, patient):
        import json

        service.extract(
            text="I have itching and a skin rash", patient=patient, clinic=clinic
        )
        call = AiCall.objects.get()
        stored = json.dumps(
            {f.name: getattr(call, f.name) for f in AiCall._meta.fields}, default=str
        )
        assert "itching" not in stored
        assert "Sunita" not in stored

    def test_the_hashes_are_recorded(self, service, clinic, patient):
        service.extract(text="I have itching", patient=patient, clinic=clinic)
        call = AiCall.objects.get()
        assert len(call.input_sha256) == 64
        assert call.input_chars > 0

    def test_the_model_version_is_recorded(self, service, clinic, patient):
        """MDSW traceability: which version produced this output."""
        service.extract(text="itching", patient=patient, clinic=clinic)
        assert AiCall.objects.get().model_version

    def test_the_log_is_append_only(self, service, clinic, patient):
        service.extract(text="itching", patient=patient, clinic=clinic)
        call = AiCall.objects.get()
        with pytest.raises(ValueError):
            call.save()

    def test_a_failing_provider_still_leaves_a_record(self, clinic, patient):
        """Exactly the case someone will be investigating."""

        class Broken:
            name = "broken"
            simulated = False

            def extract(self, *, text, language):
                raise RuntimeError("provider exploded")

        with pytest.raises(RuntimeError):
            ExtractionService(extractor=Broken()).extract(
                text="itching", patient=patient, clinic=clinic
            )

        call = AiCall.objects.get()
        assert call.succeeded is False
        assert "exploded" in call.error_message


class TestDuration:
    def test_it_reads_a_duration_from_the_description(self, service):
        assert service.duration_from("itching for four days") == 4

    def test_it_is_not_proposed_as_a_symptom(self, service, clinic, patient):
        draft = service.extract(
            text="itching for four days", patient=patient, clinic=clinic
        )
        assert draft.proposed_symptom_names == ["itching"]
