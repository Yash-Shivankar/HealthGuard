"""Free text to canonical symptoms (AUDIT 6, Tier 1).

The patient describes their problem in their own words and their own language;
this turns that into symptom names the triage model already knows, and records a
draft for a clinician to check.

## What it refuses to do

It does not classify. There is no call to the predictor anywhere in this module
and there must never be one - the ranked differential comes from the versioned
artefact, after a human has confirmed the symptom list. An architecture test
fails the build if this file imports the predictor, because the separation is
the whole point:

    The LLM normalises language. The deterministic model classifies.

## The order of operations matters

Consent, then redaction, then the provider. Checking consent after building the
payload would mean a refusal happened only after the data was assembled; redacting
after the call would mean redacting nothing. So:

1. is external processing even possible in this deployment?
2. if it is, has *this patient* consented to it, specifically?
3. redact
4. call the provider
5. record the call - hashes only
6. persist a draft, in ``draft`` state, with the language recorded

Steps 1 and 2 are separate because they answer different questions: one is about
the deployment, one is about a person, and having the first does not give you the
second.
"""

from __future__ import annotations

import logging

from apps.ai.models import AiCapability, InputLanguage
from apps.ai.repositories.ai_repository import IntakeDraftRepository
from apps.ai.services import lexicon, providers, redaction
from apps.ai.services.recorder import AiRecorder, sha256_text
from utils.exceptions.domain import ConsentRequiredError, ValidationError

logger = logging.getLogger(__name__)

#: Longer than this and it is not a symptom description. Rejected rather than
#: truncated: silently dropping the second half of what someone told you is
#: worse than asking them to shorten it.
MAX_TEXT_CHARS = 4000


class ExtractionService:
    def __init__(
        self,
        extractor: providers.Extractor | None = None,
        drafts: IntakeDraftRepository | None = None,
        recorder: AiRecorder | None = None,
    ):
        self.extractor = extractor or providers.build_extractor()
        self.drafts = drafts or IntakeDraftRepository()
        self.recorder = recorder or AiRecorder()

    # --- The gate -----------------------------------------------------------
    def check_consent(self, *, patient) -> None:
        """Raise unless this patient agreed to third-party AI processing.

        Only called when the chosen extractor is external. The local lexicon
        path needs no separate consent: it is our own processing of data we
        already hold under ``SYMPTOM_CHECK``, on this machine, and inventing a
        consent requirement for it would train people to click through the one
        that matters.
        """
        if patient is None:
            raise ConsentRequiredError(
                "An anonymous check cannot use third-party AI processing, "
                "because there is nobody to have consented to it.",
                code="ai_external_needs_identified_patient",
            )

        # Imported here rather than at module scope: apps.consent already
        # depends on apps.patients and a top-level import invites a cycle as
        # this app grows.
        from apps.consent.models import ConsentPurpose
        from apps.consent.services.consent_service import ConsentService

        if not ConsentService().has_active(
            patient=patient, purpose=ConsentPurpose.AI_ASSISTANCE
        ):
            raise ConsentRequiredError(
                "This patient has not agreed to AI assistance that sends their "
                "description to a third-party service.",
                code="ai_assistance_consent_required",
            )

    # --- The work -----------------------------------------------------------
    def extract(
        self,
        *,
        text: str,
        language: str = InputLanguage.UNKNOWN,
        patient=None,
        clinic=None,
        from_audio: bool = False,
        persist: bool = True,
    ):
        """Read a description and return an :class:`~apps.ai.models.IntakeDraft`.

        ``persist=False`` returns an unsaved instance, for a preview that must
        not leave a row behind.
        """
        cleaned = _clean(text)
        language = _known_language(language)

        external = getattr(self.extractor, "name", "") and _is_external(self.extractor)
        if external:
            self.check_consent(patient=patient)

        payload, was_redacted = self._prepare(cleaned, patient=patient, external=external)

        with self.recorder.record(AiCapability.EXTRACTION, clinic=clinic) as outcome:
            outcome.provider = getattr(self.extractor, "name", "unknown")
            outcome.external = bool(external)
            outcome.redacted = was_redacted
            outcome.input_sha256 = sha256_text(payload)
            outcome.input_chars = len(payload)

            extraction = self.extractor.extract(text=payload, language=language)

            outcome.model_version = extraction.model_version
            outcome.simulated = extraction.simulated
            outcome.output_sha256 = sha256_text(",".join(extraction.symptom_names))
            outcome.extra = {"symptoms_found": len(extraction.matches)}

        draft = self._build_draft(
            cleaned=cleaned,
            extraction=extraction,
            language=language,
            patient=patient,
            clinic=clinic,
            from_audio=from_audio,
            external=bool(external),
            persist=persist,
        )

        logger.info(
            "Intake draft created",
            extra={
                "event": "ai_intake_draft",
                "draft_id": draft.pk,
                "provider": extraction.provider,
                "symptoms": len(extraction.matches),
                "unmapped": len(extraction.unmapped),
                "language": language,
                "simulated": extraction.simulated,
            },
        )
        return draft

    def _prepare(self, cleaned: str, *, patient, external: bool) -> tuple[str, bool]:
        """Redact if and only if the text is about to leave.

        Redacting on the local path would be pointless work that also degrades
        the result - the lexicon does better with the sentence intact, and
        nothing has left the building.
        """
        if not external:
            return cleaned, False

        abha = ""
        link = getattr(patient, "abha_link", None)
        if link is not None:
            abha = getattr(link, "abha_number", "") or ""

        result = redaction.redact(
            cleaned,
            name=getattr(patient, "name", "") or "",
            contact=getattr(patient, "contact", "") or "",
            abha=abha,
        )
        if result.changed:
            logger.info(
                "Redacted identifiers before external processing",
                # Prefixed, not splatted raw: the counts are keyed by "name" and
                # "id", both of which are reserved LogRecord attributes, and
                # logging raises rather than shadowing them. This only fires on
                # the external path, so unprefixed it would have sat here until
                # the first day someone configured a vendor.
                extra={
                    "event": "ai_redacted",
                    **{f"redacted_{kind}": n for kind, n in result.counts.items()},
                },
            )
        # True because redaction ran, not because it found something: the
        # question the log answers is whether the step happened.
        return result.text, True

    def _build_draft(
        self,
        *,
        cleaned,
        extraction,
        language,
        patient,
        clinic,
        from_audio,
        external,
        persist,
    ):
        from apps.ai.models import IntakeDraft

        fields = {
            "patient": patient,
            "clinic": clinic if clinic is not None else getattr(patient, "clinic", None),
            "language": language,
            # The patient's own words, unredacted: this row is theirs and
            # cascades with them. The redacted copy existed only for the wire.
            "source_text": cleaned,
            "from_audio": from_audio,
            "proposed_symptoms": [
                {
                    "symptom": m.symptom,
                    "evidence": m.evidence,
                    "confidence": round(m.confidence, 4),
                }
                for m in extraction.matches
            ],
            "unmapped_phrases": list(extraction.unmapped),
            "provider": extraction.provider,
            "model_version": extraction.model_version,
            "simulated": extraction.simulated,
            "externally_processed": external,
        }

        if not persist:
            return IntakeDraft(**fields)
        return self.drafts.create(**fields)

    # --- Duration -----------------------------------------------------------
    @staticmethod
    def duration_from(text: str) -> int | None:
        """Days the patient said it had been going on, if they said.

        Separate from extraction because it is not a symptom and must not be
        proposed as one, and because the intake form has a field for it - reading
        it here means the patient is not asked a question they already answered.
        """
        return lexicon.extract_duration(text)


def _clean(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        raise ValidationError(
            "Please describe the problem before continuing.", code="ai_empty_text"
        )
    if len(cleaned) > MAX_TEXT_CHARS:
        raise ValidationError(
            f"That description is too long. Please keep it under "
            f"{MAX_TEXT_CHARS} characters.",
            code="ai_text_too_long",
        )
    return cleaned


def _known_language(language: str) -> str:
    valid = {choice for choice, _ in InputLanguage.choices}
    return language if language in valid else InputLanguage.UNKNOWN


def _is_external(extractor) -> bool:
    """Whether this extractor sends data off the machine.

    Asked of the instance rather than read from settings, because the instance is
    what will actually run - a factory that fell back to the local path must not
    trigger a consent check the patient does not need.
    """
    return isinstance(extractor, providers.LlmExtractor)
