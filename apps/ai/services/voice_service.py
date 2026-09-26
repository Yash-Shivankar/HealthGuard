"""Voice intake (AUDIT 6, Tier 1).

The headline Tier 1 feature: the patient speaks Hindi or Marathi, and the doctor
sees structured English. This is the thin service that joins the two halves -
transcribe, then extract - and it exists as its own layer because transcription
and extraction have different consent implications, different providers and
different failure modes.

## The state of this path, stated plainly

AUDIT 11.5 - voice build or buy - is unanswered, and the audit's own
recommendation is to buy Indian-language medical ASR for the pilot because it is
hard. No vendor is contracted, so the default transcriber returns a canned
sentence and every draft it produces is ``simulated``, which the review gate
refuses to let anyone confirm.

That makes this endpoint genuinely useful for exactly one thing today: proving
the pipeline works end to end, so that contracting a vendor is a configuration
change rather than a build. It is not useful for seeing patients, and the
``simulated`` flag is what stops anyone mistaking one for the other.

## Audio is not stored

The bytes are transcribed and dropped. The transcript is health data and lives on
the draft, where it cascades with the patient; the recording would be biometric
data about them, held for no purpose that the transcript does not already serve.
Keeping it would be a second copy outside the erasure path, and a more sensitive
one than the first.
"""

from __future__ import annotations

import logging

from apps.ai.models import AiCapability, InputLanguage
from apps.ai.services import providers
from apps.ai.services.extraction_service import ExtractionService
from apps.ai.services.recorder import AiRecorder, sha256_bytes, sha256_text
from utils.exceptions.domain import ConsentRequiredError, ValidationError

logger = logging.getLogger(__name__)

#: Roughly a minute of compressed speech. An intake description is short, and an
#: unbounded upload on an endpoint anyone can reach is a way to fill a disk.
MAX_AUDIO_BYTES = 2 * 1024 * 1024


class VoiceIntakeService:
    def __init__(
        self,
        transcriber: providers.Transcriber | None = None,
        extraction: ExtractionService | None = None,
        recorder: AiRecorder | None = None,
    ):
        self.transcriber = transcriber or providers.build_transcriber()
        self.extraction = extraction or ExtractionService()
        self.recorder = recorder or AiRecorder()

    def check_consent(self, *, patient) -> None:
        """Raise unless this patient agreed to third-party AI processing.

        Voice reaches this check more often than text does, because the local
        transcriber is a stub rather than a working implementation: any real
        transcription today is external by definition. That asymmetry is the
        honest shape of the feature until 11.5 is answered.
        """
        if patient is None:
            raise ConsentRequiredError(
                "An anonymous check cannot use a third-party transcription "
                "service, because there is nobody to have consented to it.",
                code="ai_external_needs_identified_patient",
            )

        from apps.consent.models import ConsentPurpose
        from apps.consent.services.consent_service import ConsentService

        if not ConsentService().has_active(
            patient=patient, purpose=ConsentPurpose.AI_ASSISTANCE
        ):
            raise ConsentRequiredError(
                "This patient has not agreed to AI assistance that sends a "
                "recording of their voice to a third-party service.",
                code="ai_assistance_consent_required",
            )

    def transcribe(
        self,
        *,
        audio: bytes,
        language: str = InputLanguage.UNKNOWN,
        patient=None,
        clinic=None,
    ) -> providers.Transcript:
        """Speech to text. Does not persist anything by itself."""
        _check_audio(audio)

        external = _is_external(self.transcriber)
        if external:
            self.check_consent(patient=patient)

        with self.recorder.record(AiCapability.TRANSCRIPTION, clinic=clinic) as outcome:
            outcome.provider = getattr(self.transcriber, "name", "unknown")
            outcome.external = external
            # Nothing to redact: audio cannot be redacted by string
            # substitution, which is a real reason the voice path is the one
            # most dependent on the consent gate rather than on minimisation.
            outcome.redacted = False
            outcome.input_sha256 = sha256_bytes(audio)
            outcome.input_chars = len(audio)

            transcript = self.transcriber.transcribe(audio=audio, language=language)

            outcome.model_version = transcript.model_version
            outcome.simulated = transcript.simulated
            outcome.output_sha256 = sha256_text(transcript.text)

        return transcript

    def intake(
        self,
        *,
        audio: bytes,
        language: str = InputLanguage.UNKNOWN,
        patient=None,
        clinic=None,
    ):
        """Audio in, intake draft out.

        The draft inherits ``simulated`` from **either** step: a real extraction
        of a canned transcript is still a draft about words nobody said, so the
        flag has to travel forward rather than be recomputed from the extractor
        alone.
        """
        transcript = self.transcribe(
            audio=audio, language=language, patient=patient, clinic=clinic
        )

        draft = self.extraction.extract(
            text=transcript.text,
            language=transcript.language or language,
            patient=patient,
            clinic=clinic,
            from_audio=True,
        )

        if transcript.simulated and not draft.simulated:
            draft.simulated = True
            draft.provider = f"{transcript.provider}+{draft.provider}"
            self.extraction.drafts.save(draft, ["simulated", "provider"])

        if transcript.external and not draft.externally_processed:
            draft.externally_processed = True
            self.extraction.drafts.save(draft, ["externally_processed"])

        logger.info(
            "Voice intake completed",
            extra={
                "event": "ai_voice_intake",
                "draft_id": draft.pk,
                "transcriber": transcript.provider,
                "simulated": draft.simulated,
                "language": draft.language,
            },
        )
        return draft


def _check_audio(audio: bytes) -> None:
    if not audio:
        raise ValidationError(
            "No recording was received. Please try again, or type the "
            "description instead.",
            code="ai_empty_audio",
        )
    if len(audio) > MAX_AUDIO_BYTES:
        raise ValidationError(
            "That recording is too long. Please keep it under a minute, or type "
            "the description instead.",
            code="ai_audio_too_large",
        )


def _is_external(transcriber) -> bool:
    return isinstance(transcriber, providers.VendorTranscriber)
