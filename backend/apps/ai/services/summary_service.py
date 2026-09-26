"""Ambient consultation summarisation (AUDIT 6, Tier 1).

Drafts a consultation note from a completed assessment so the clinician edits
rather than types. The audit's cited savings are 30 minutes to 2+ hours per
clinician per day, and its cited caveat is hallucination in under 5% of notes with
accuracy degrading on background noise, cross-talk and specialty vocabulary.
Physician review before finalisation is mandatory, clinically and under TPG 2020.

## The medication gate

TPG 2020 is explicit that this system must not prescribe or dose. A note is the
single most dangerous place to break that: it is the artefact a busy clinician is
most likely to sign without reading closely, and a drug name inside one would
carry the authority of their signature.

So :func:`assert_no_medication` runs over every draft before it is stored, and a
draft that mentions a medication or a dose is **refused** rather than stored with
a warning flag. Refusing is the stricter reading and the right one: a flag relies
on someone noticing it, and the whole premise of ambient documentation is that
nobody is reading carefully.

The check runs on the deterministic template's output too, where it cannot fail
by construction. A guard that only runs on the paths you expect to fail is not a
guard - and the template is one prompt change away from not being deterministic.

## What it does not do

It does not listen to a consultation. Real ambient capture means a microphone in
a room with two people talking, which is a different consent conversation, a
different retention question and a different vendor - and AUDIT 11.5 is
unanswered. What this does is summarise the structured record of an encounter,
which is the useful half that needs no vendor. The voice path in
``voice_service`` covers patient-side capture.
"""

from __future__ import annotations

import logging
import re

from apps.ai.models import (
    AiCapability,
    ConsultationNote,
    DraftState,
    stamp_confirmation,
)
from apps.ai.repositories.ai_repository import ConsultationNoteRepository
from apps.ai.services import providers
from apps.ai.services.recorder import AiRecorder, sha256_text
from apps.audit.models import AuditAction
from apps.audit.services.audit_service import AuditService
from utils.exceptions.domain import DomainError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The medication gate
# ---------------------------------------------------------------------------
# CLINICAL-REVIEW-NEEDED (AUDIT 11.6): a pharmacist or clinician should extend
# this list. It is deliberately broad - a false refusal costs a clinician one
# retry, and a false pass puts a drug name in a signed clinical note.

DOSE_PATTERNS = (
    # 500mg, 5 ml, 2.5mcg, 10 units
    re.compile(r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|µg|ug|g|ml|cc|iu|units?)\b", re.IGNORECASE),
    # bd, tds, qid, od, prn, q8h - prescription frequency shorthand
    re.compile(r"\b(?:bd|tds|tid|qid|qds|od|hs|sos|prn|q\d+h)\b", re.IGNORECASE),
    # "twice daily", "three times a day"
    re.compile(
        r"\b(?:once|twice|thrice|\d+\s?times?)\s+(?:a\s+|per\s+)?(?:day|daily|week)\b",
        re.IGNORECASE,
    ),
    # "take two tablets"
    re.compile(r"\b(?:tablets?|capsules?|syrup|injection|drops?)\b", re.IGNORECASE),
)

DRUG_WORDS = frozenset(
    {
        # Verbs that only appear when something is being prescribed.
        "prescribe", "prescribed", "prescription", "dose", "dosage", "dosing",
        # The over-the-counter names most likely to be volunteered.
        "paracetamol", "acetaminophen", "ibuprofen", "aspirin", "diclofenac",
        "amoxicillin", "azithromycin", "ciprofloxacin", "metronidazole",
        "doxycycline", "cetirizine", "levocetirizine", "loratadine",
        "omeprazole", "pantoprazole", "ranitidine", "metformin", "insulin",
        "amlodipine", "atenolol", "losartan", "atorvastatin", "prednisolone",
        "dexamethasone", "salbutamol", "montelukast", "ondansetron",
        "domperidone", "tramadol", "codeine", "morphine", "antibiotic",
        "antibiotics", "crocin", "dolo", "combiflam", "azee", "augmentin",
    }
)


class MedicationContentError(DomainError):
    """A draft note mentioned a medication or a dose.

    Its own type so a caller cannot accidentally treat it as an ordinary
    validation failure and retry with the same input.
    """


def assert_no_medication(text: str) -> None:
    """Raise :class:`MedicationContentError` if ``text`` prescribes anything.

    Word-boundary matching on a curated list, plus dose-shaped patterns. It will
    not catch every drug name in existence, which is why the note also carries an
    explicit statement that it contains no treatment advice, and why a clinician
    signs it.
    """
    if not text:
        return

    haystack = str(text).lower()

    for word in DRUG_WORDS:
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", haystack):
            raise MedicationContentError(
                "The drafted note mentioned a medication or a prescription, which "
                "this system must never produce. The draft was discarded.",
                code="ai_note_mentions_medication",
            )

    for pattern in DOSE_PATTERNS:
        if pattern.search(haystack):
            raise MedicationContentError(
                "The drafted note contained dosing information, which this system "
                "must never produce. The draft was discarded.",
                code="ai_note_mentions_dose",
            )


class SummaryService:
    def __init__(
        self,
        summariser: providers.Summariser | None = None,
        notes: ConsultationNoteRepository | None = None,
        recorder: AiRecorder | None = None,
        audit: AuditService | None = None,
    ):
        self.summariser = summariser or providers.build_summariser()
        self.notes = notes or ConsultationNoteRepository()
        self.recorder = recorder or AiRecorder()
        self.audit = audit or AuditService()

    def draft_for(self, *, assessment, intake_draft=None, persist: bool = True):
        """Draft a note for one assessment.

        ``intake_draft`` is optional and worth passing when it exists: the
        patient's own words are the most useful line in a consultation note, and
        they only exist on the draft.
        """
        facts = self._facts(assessment, intake_draft)

        with self.recorder.record(
            AiCapability.SUMMARISATION, clinic=assessment.clinic
        ) as outcome:
            outcome.provider = getattr(self.summariser, "name", "unknown")
            outcome.input_sha256 = sha256_text(repr(sorted(facts.items(), key=str)))
            outcome.input_chars = len(str(facts))
            outcome.external = False
            outcome.redacted = False

            note_draft = self.summariser.summarise(facts=facts)

            outcome.model_version = note_draft.model_version
            outcome.simulated = note_draft.simulated
            outcome.external = note_draft.external
            outcome.output_sha256 = sha256_text(
                "\n".join(note_draft.sections.get(k, "") for k in sorted(note_draft.sections))
            )

            # Inside the recording block on purpose: a refusal is an outcome
            # worth having a row for, and a provider that keeps producing drug
            # names should be visible as failures rather than as silence.
            for section in note_draft.sections.values():
                assert_no_medication(section)

        fields = {
            "patient": assessment.patient,
            "clinic": assessment.clinic,
            "assessment": assessment,
            "sections": dict(note_draft.sections),
            "provider": note_draft.provider,
            "model_version": note_draft.model_version,
            "simulated": note_draft.simulated,
            "externally_processed": note_draft.external,
        }

        if not persist:
            return ConsultationNote(**fields)

        note = self.notes.create(**fields)
        logger.info(
            "Consultation note drafted",
            extra={
                "event": "ai_note_drafted",
                "note_id": note.pk,
                "assessment_id": assessment.pk,
                "provider": note_draft.provider,
                "simulated": note_draft.simulated,
            },
        )
        return note

    def sign_off(self, *, note: ConsultationNote, actor=None, clinician_text: str = "", request=None):
        """Clinician sign-off, which is what turns the draft into a note.

        The clinician's own edits are checked too. That is not an accusation -
        it is the same rule applied consistently, and a clinician who genuinely
        needs to record a prescription should be doing it in a prescribing
        system that has the safeguards for it, not in a field this app owns.
        """
        if actor is None or not getattr(actor, "pk", None):
            raise DomainError(
                "A clinician must be signed in to sign off a consultation note.",
                code="ai_signoff_needs_actor",
            )

        if note.state != DraftState.DRAFT:
            raise DomainError(
                f"That note has already been {note.get_state_display().lower()}.",
                code="ai_note_already_reviewed",
            )

        if note.simulated:
            raise DomainError(
                "This note was drafted by a simulated provider and cannot be "
                "signed off into a record.",
                code="ai_simulated_cannot_be_confirmed",
            )

        if clinician_text:
            assert_no_medication(clinician_text)
            note.clinician_text = clinician_text

        stamp_confirmation(note, actor=actor)
        self.notes.save(
            note, ["clinician_text", "state", "confirmed_by", "confirmed_at"]
        )

        self.audit.record(
            action=AuditAction.UPDATE,
            actor=actor,
            obj=note,
            request=request,
            reason="signed off AI consultation note",
        )
        logger.info(
            "Consultation note signed off",
            extra={"event": "ai_note_signed", "note_id": note.pk},
        )
        return note

    @staticmethod
    def _facts(assessment, intake_draft) -> dict:
        """The structured record the summariser is allowed to see.

        An explicit whitelist rather than the model instance: what reaches a
        generator is what can appear in its output, and passing the ORM object
        would make that set whatever the model happens to have next month.
        """
        from apps.triage.services.assessment_service import humanise

        facts = {
            "symptoms": [humanise(s) for s in (assessment.symptoms or [])],
            "days": assessment.days,
            "abstained": assessment.abstained,
            "differential": list(assessment.differential or []),
            "urgency_level": assessment.urgency_level,
            "urgency_label": assessment.get_urgency_level_display(),
        }

        if intake_draft is not None:
            facts["source_text"] = intake_draft.source_text
            facts["from_audio"] = intake_draft.from_audio
            facts["language"] = intake_draft.language
            facts["language_label"] = intake_draft.get_language_display()

        return facts
