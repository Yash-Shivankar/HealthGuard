"""Tests for voice intake (AUDIT 6, Tier 1).

The headline Tier 1 feature, and the one furthest from being usable: AUDIT 11.5
is unanswered, so there is no speech vendor and the default transcriber returns
canned text.

``TestSimulationTravelsForward`` is the set that matters. A real extraction of a
fabricated transcript is still a draft about words nobody said, so the flag has
to propagate rather than be recomputed from the extractor alone - and getting
that wrong would make a stub draft confirmable.
"""

from __future__ import annotations

import pytest

from apps.ai.models import AiCall, AiCapability, IntakeDraft
from apps.ai.services import providers
from apps.ai.services.voice_service import MAX_AUDIO_BYTES, VoiceIntakeService
from apps.accounts.models import Clinic
from apps.consent.models import ConsentPurpose
from apps.consent.services.consent_service import ConsentService
from apps.patients.models import Patient
from utils.exceptions.domain import ConsentRequiredError, ValidationError

pytestmark = pytest.mark.django_db


@pytest.fixture
def clinic():
    return Clinic.objects.create(name="Voice Clinic")


@pytest.fixture
def patient(clinic):
    return Patient.objects.create(
        clinic=clinic, name="Voice Person", contact="9876500003",
        age=29, height_cm=158, weight_kg=52,
    )


@pytest.fixture
def service():
    return VoiceIntakeService(transcriber=providers.StubTranscriber())


#: Chosen so the stub's hash lands on a transcript with recognisable symptoms.
AUDIO = b"itching-and-rash-clip"


class FixedTranscriber:
    """A transcriber returning known text, so extraction can be asserted on."""

    name = "fixed"

    def __init__(self, text: str, *, simulated=False, external=False):
        self.text = text
        self.simulated = simulated
        self.external = external

    def transcribe(self, *, audio, language):
        return providers.Transcript(
            text=self.text, language=language, provider=self.name,
            model_version="fixed-1", simulated=self.simulated, external=self.external,
        )


class ExternalTranscriber(FixedTranscriber, providers.VendorTranscriber):
    """Passes the isinstance check the service uses to decide externality."""

    def __init__(self, text):
        FixedTranscriber.__init__(self, text, simulated=False, external=True)


class TestTheHappyPath:
    def test_audio_becomes_a_draft(self, clinic, patient):
        service = VoiceIntakeService(
            transcriber=FixedTranscriber("I have itching and a skin rash")
        )
        draft = service.intake(audio=AUDIO, language="en", patient=patient, clinic=clinic)

        assert set(draft.proposed_symptom_names) == {"itching", "skin_rash"}
        assert draft.from_audio is True

    def test_the_transcript_is_kept_as_the_source_text(self, clinic, patient):
        service = VoiceIntakeService(transcriber=FixedTranscriber("I have itching"))
        draft = service.intake(audio=AUDIO, patient=patient, clinic=clinic)
        assert draft.source_text == "I have itching"

    def test_hindi_audio_produces_english_symptoms(self, clinic, patient):
        """The point of the whole feature: the patient speaks Hindi, the doctor
        reads structured English.
        """
        service = VoiceIntakeService(
            transcriber=FixedTranscriber("मुझे सिरदर्द और उल्टी हो रही है")
        )
        draft = service.intake(audio=AUDIO, language="hi", patient=patient, clinic=clinic)

        assert set(draft.proposed_symptom_names) == {"headache", "vomiting"}
        assert draft.language == "hi"

    def test_both_steps_are_recorded_separately(self, service, clinic, patient):
        """Transcription and extraction are different providers with different
        consent implications; one row for both would hide which did what.
        """
        service.intake(audio=AUDIO, patient=patient, clinic=clinic)
        capabilities = set(AiCall.objects.values_list("capability", flat=True))
        assert capabilities == {AiCapability.TRANSCRIPTION, AiCapability.EXTRACTION}


class TestAudioIsNotStored:
    def test_no_field_holds_the_recording(self):
        """The transcript serves every purpose the audio would, and a recording
        of someone's voice is biometric data about them - a second copy outside
        the erasure path, and a more sensitive one than the first.
        """
        fields = {f.name for f in IntakeDraft._meta.fields}
        assert not fields & {"audio", "recording", "audio_file", "voice"}

        types = {f.get_internal_type() for f in IntakeDraft._meta.fields}
        assert "FileField" not in types
        assert "BinaryField" not in types

    def test_the_call_log_keeps_only_a_hash_of_it(self, service, clinic, patient):
        service.intake(audio=AUDIO, patient=patient, clinic=clinic)
        call = AiCall.objects.get(capability=AiCapability.TRANSCRIPTION)
        assert len(call.input_sha256) == 64
        assert call.input_chars == len(AUDIO)


class TestSimulationTravelsForward:
    def test_a_stub_transcript_yields_a_simulated_draft(self, service, clinic, patient):
        """Even though the lexicon extraction of it was real."""
        draft = service.intake(audio=AUDIO, patient=patient, clinic=clinic)
        assert draft.simulated is True

    def test_that_draft_cannot_be_confirmed(self, service, clinic, patient):
        draft = service.intake(audio=AUDIO, patient=patient, clinic=clinic)
        assert draft.can_be_confirmed is False

    def test_the_provenance_names_both_providers(self, service, clinic, patient):
        """So it is obvious which half was simulated."""
        draft = service.intake(audio=AUDIO, patient=patient, clinic=clinic)
        assert "stub" in draft.provider
        assert "lexicon" in draft.provider

    def test_a_real_transcriber_yields_a_confirmable_draft(self, clinic, patient):
        service = VoiceIntakeService(transcriber=FixedTranscriber("I have itching"))
        draft = service.intake(audio=AUDIO, patient=patient, clinic=clinic)

        assert draft.simulated is False
        assert draft.can_be_confirmed is True

    def test_external_transcription_marks_the_draft(self, clinic, patient):
        ConsentService().grant(patient=patient, purpose=ConsentPurpose.AI_ASSISTANCE)
        service = VoiceIntakeService(transcriber=ExternalTranscriber("I have itching"))

        draft = service.intake(audio=AUDIO, patient=patient, clinic=clinic)
        assert draft.externally_processed is True


class TestConsent:
    def test_external_transcription_needs_consent(self, clinic, patient):
        service = VoiceIntakeService(transcriber=ExternalTranscriber("I have itching"))
        with pytest.raises(ConsentRequiredError) as exc:
            service.intake(audio=AUDIO, patient=patient, clinic=clinic)
        assert exc.value.code == "ai_assistance_consent_required"

    def test_an_anonymous_patient_cannot_use_an_external_transcriber(self, clinic):
        service = VoiceIntakeService(transcriber=ExternalTranscriber("I have itching"))
        with pytest.raises(ConsentRequiredError):
            service.intake(audio=AUDIO, patient=None, clinic=clinic)

    def test_the_stub_needs_no_consent(self, service, clinic, patient):
        """Nothing leaves the building, so there is nothing to consent to."""
        draft = service.intake(audio=AUDIO, patient=patient, clinic=clinic)
        assert draft.pk is not None

    def test_nothing_is_transcribed_when_consent_is_missing(self, clinic, patient):
        service = VoiceIntakeService(transcriber=ExternalTranscriber("I have itching"))
        with pytest.raises(ConsentRequiredError):
            service.intake(audio=AUDIO, patient=patient, clinic=clinic)
        assert AiCall.objects.count() == 0


class TestInputValidation:
    def test_empty_audio_is_refused(self, service, clinic, patient):
        with pytest.raises(ValidationError) as exc:
            service.intake(audio=b"", patient=patient, clinic=clinic)
        assert exc.value.code == "ai_empty_audio"

    def test_an_oversized_recording_is_refused(self, service, clinic, patient):
        with pytest.raises(ValidationError) as exc:
            service.intake(
                audio=b"x" * (MAX_AUDIO_BYTES + 1), patient=patient, clinic=clinic
            )
        assert exc.value.code == "ai_audio_too_large"

    def test_the_refusal_offers_the_alternative(self, service, clinic, patient):
        """Voice is the fragile path; typing always works."""
        with pytest.raises(ValidationError) as exc:
            service.intake(audio=b"", patient=patient, clinic=clinic)
        assert "type" in str(exc.value).lower()

    def test_nothing_is_recorded_for_rejected_input(self, service, clinic, patient):
        with pytest.raises(ValidationError):
            service.intake(audio=b"", patient=patient, clinic=clinic)
        assert AiCall.objects.count() == 0
        assert IntakeDraft.objects.count() == 0
