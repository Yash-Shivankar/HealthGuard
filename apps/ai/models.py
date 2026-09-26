"""AI-assisted intake and documentation (AUDIT 6, Tier 1).

Three capabilities: a patient describes their problem in Hindi, Marathi or
English by voice or by typing; the text is normalised into the project's
canonical symptom vocabulary; and a consultation note is drafted for the
clinician. All three produce **drafts**, and a draft is not a record.

## The separation this app exists to protect

    The LLM normalises language. The deterministic model classifies.

Nothing in this app names a condition. Extraction turns free text into symptom
names that already exist in the model's 132-term vocabulary and stops there;
the ranked differential still comes from ``apps.triage.services.predictor``,
against a versioned artefact, reproducibly. That boundary is what keeps the
system auditable and cheap, and it is enforced by an architecture test rather
than by good intentions (AUDIT 6, Tier 1).

## Nothing here enters a record without a clinician

TPG 2020 requires clinician sign-off before an AI output enters a record, and
the ambient-scribe literature the audit cites reports hallucinations in under
5% of notes, with accuracy degrading on background noise and cross-talk. So:

- every draft starts in :attr:`DraftState.DRAFT`
- confirming one requires a named actor, and the transition is recorded
- a **simulated** draft can never be confirmed at all

That last rule is the one that matters most in this repo's current state. With
no speech vendor contracted (AUDIT 11.5 is unanswered) the default transcriber
returns a deterministic canned transcript so the pipeline can be built and
tested end to end. A canned transcript is fabricated clinical content. It is
useful for development and it must never be signed into a patient's record, so
``simulated`` is a hard gate rather than a label.

## Provenance, without a second copy of the record

MDSW guidance expects every output traceable to the version that produced it.
Two places, deliberately:

- the **draft** holds the text and the extraction, and cascades when the
  patient is erased, because it is health data about that person
- :class:`AiCall` holds the provider, the model version, timings and *hashes*
  of the input and output, and is append-only

So "which model version processed this, and did the input change?" survives an
erasure, while the content does not. Same resolution as the access log and the
ABDM gateway log: keep the shape, never the body.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords


class InputLanguage(models.TextChoices):
    """The language the patient actually used.

    Recorded on every draft for a reason beyond display. AUDIT 6 requires
    fairness measured across age, sex and language, and notes that with 304
    unique rows and no demographic labels the project **cannot** do it today.
    Language is the one of the three this app can start capturing honestly, at
    the moment of capture, without asking anyone an extra question. It does not
    make fairness measurable now; it is what makes it measurable later.
    """

    ENGLISH = "en", "English"
    HINDI = "hi", "Hindi"
    MARATHI = "mr", "Marathi"
    UNKNOWN = "unknown", "Not recorded"


class DraftState(models.TextChoices):
    DRAFT = "draft", "AI draft - needs clinician review"
    CONFIRMED = "confirmed", "Confirmed by clinician"
    DISCARDED = "discarded", "Discarded by clinician"


class AiCapability(models.TextChoices):
    TRANSCRIPTION = "transcription", "Speech to text"
    EXTRACTION = "extraction", "Free text to canonical symptoms"
    SUMMARISATION = "summarisation", "Consultation note draft"


class AiDraft(models.Model):
    """Fields every draft carries, so the review gate is written once.

    Abstract: an intake draft and a consultation note are different content
    with identical governance, and duplicating the governance is how the two
    drift apart.
    """

    clinic = models.ForeignKey(
        "accounts.Clinic",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
        help_text="Null for a draft captured before the patient was identified.",
    )

    state = models.CharField(
        max_length=16, choices=DraftState.choices, default=DraftState.DRAFT
    )

    # --- Provenance -------------------------------------------------------
    provider = models.CharField(
        max_length=64,
        blank=True,
        help_text="Which implementation produced this - 'lexicon', 'template' or a vendor name.",
    )
    model_version = models.CharField(max_length=64, blank=True)

    #: True when a stub produced this rather than a real provider. A simulated
    #: draft is fabricated content and confirming one is refused outright.
    simulated = models.BooleanField(default=True)

    #: True when any part of this left our infrastructure for a third-party
    #: processor. Recorded per draft rather than inferred from settings,
    #: because settings change and a DPDP record of processing should not.
    externally_processed = models.BooleanField(default=False)

    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    @property
    def needs_review(self) -> bool:
        return self.state == DraftState.DRAFT

    @property
    def is_confirmed(self) -> bool:
        return self.state == DraftState.CONFIRMED

    @property
    def can_be_confirmed(self) -> bool:
        """Whether a clinician is allowed to sign this off.

        Read by the UI so a button can be disabled, and re-checked in the
        service so a caller that ignores the UI is still refused.
        """
        return self.state == DraftState.DRAFT and not self.simulated


class IntakeDraft(AiDraft):
    """What the patient said, and the canonical symptoms it maps to.

    ``proposed_symptoms`` is a **proposal**. The clinician's confirmed list is
    what reaches the model. They are separate fields rather than one mutable
    list so a review is visible as a diff: what the machine suggested against
    what a person accepted, which is the record TPG 2020's sign-off requirement
    is asking for.
    """

    language = models.CharField(
        max_length=16, choices=InputLanguage.choices, default=InputLanguage.UNKNOWN
    )

    #: What the patient typed, or what the transcriber produced. Health data
    #: about the patient, so it cascades with them.
    source_text = models.TextField(
        blank=True, help_text="The patient's own words, typed or transcribed."
    )
    from_audio = models.BooleanField(default=False)

    #: Entries of {"symptom", "evidence", "confidence"}. ``symptom`` is always
    #: a name the model knows - anything else is dropped during extraction
    #: rather than stored and filtered later.
    proposed_symptoms = models.JSONField(default=list, blank=True)

    #: Phrases the extractor could not map. Kept because they are the backlog
    #: for the lexicon: the honest list of what this app does not understand yet.
    unmapped_phrases = models.JSONField(default=list, blank=True)

    #: What the clinician actually accepted. Empty until confirmation.
    confirmed_symptoms = models.JSONField(default=list, blank=True)

    #: The assessment this draft was turned into, if any. SET_NULL because an
    #: erased patient's assessment goes and the draft's own cascade handles the
    #: draft; this field must not be the thing that blocks either.
    assessment = models.ForeignKey(
        "triage.TriageAssessment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="intake_drafts",
    )

    # The confirm/discard transitions, which are the sign-off trail. Cascades
    # with the patient for the same reason the assessment's history does.
    history = HistoricalRecords(cascade_delete_history=True)

    class Meta:
        verbose_name = "Intake draft"
        verbose_name_plural = "Intake drafts"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["state", "-created_at"]),
            models.Index(fields=["clinic", "state"]),
            models.Index(fields=["language"]),
        ]

    def __str__(self) -> str:
        count = len(self.proposed_symptoms)
        return f"Intake draft: {count} symptom(s) proposed ({self.get_state_display()})"

    @property
    def proposed_symptom_names(self) -> list[str]:
        return [
            entry["symptom"] for entry in self.proposed_symptoms if entry.get("symptom")
        ]

    @property
    def understood_nothing(self) -> bool:
        """No symptom mapped.

        Its own property because it is a distinct outcome from "the patient has
        no symptoms", and the UI must not present the first as the second.
        """
        return not self.proposed_symptoms


class ConsultationNote(AiDraft):
    """An ambient consultation summary, awaiting clinician sign-off.

    The audit's own caveat on this feature is the design constraint: reported
    savings of 30 minutes to 2+ hours per clinician per day, against
    hallucinations in under 5% of notes and accuracy that degrades with
    background noise, cross-talk and specialty vocabulary. Physician review
    before finalisation is mandatory both clinically and under TPG 2020.

    **No medication, no dose, ever.** TPG 2020 is explicit that this system must
    not prescribe. A summariser that mentioned a drug would be doing exactly
    that in the one place a clinician is most likely to accept it unread, so the
    service refuses such a draft rather than storing and flagging it - see
    ``summary_service.py``.
    """

    assessment = models.ForeignKey(
        "triage.TriageAssessment",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="consultation_notes",
    )

    #: Keys from :attr:`SECTION_ORDER` to text. A deliberately fixed set, with
    #: no medication section for it to be written into.
    sections = models.JSONField(default=dict, blank=True)

    #: Free-text edits a clinician made before signing off. Stored separately
    #: from ``sections`` so the AI draft and the human version stay
    #: distinguishable after the fact.
    clinician_text = models.TextField(blank=True)

    history = HistoricalRecords(cascade_delete_history=True)

    #: Fixed, and it does not include medication. See the class docstring.
    SECTION_ORDER = ("presenting_complaint", "history", "assessment", "plan")

    SECTION_LABELS = {
        "presenting_complaint": "Presenting complaint",
        "history": "History",
        "assessment": "Assessment",
        "plan": "Suggested next step",
    }

    class Meta:
        verbose_name = "Consultation note"
        verbose_name_plural = "Consultation notes"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["state", "-created_at"]),
            models.Index(fields=["clinic", "state"]),
        ]

    def __str__(self) -> str:
        return f"Consultation note ({self.get_state_display()})"

    def ordered_sections(self) -> list[tuple[str, str]]:
        """Sections in a fixed reading order, skipping the empty ones."""
        return [
            (self.SECTION_LABELS[key], self.sections[key])
            for key in self.SECTION_ORDER
            if self.sections.get(key)
        ]


class AiCall(models.Model):
    """One invocation of an AI capability. Append-only.

    Exists for the MDSW traceability the audit asks for: every model version,
    every input, every output. Resolved the same way as the access log and the
    ABDM gateway log, because the naive reading of that requirement conflicts
    with the DPDP right to erasure.

    **No text is stored here - only hashes.** The input and the output live on
    the draft, which cascades when the patient is erased. What survives is
    proof of *which version processed what*: the hashes still match content
    someone else holds, and they identify nobody on their own.

    ``external`` is the field a DPDP audit will actually ask about: did this
    person's data leave our infrastructure, and was it redacted first.
    """

    capability = models.CharField(max_length=32, choices=AiCapability.choices)
    provider = models.CharField(max_length=64)
    model_version = models.CharField(max_length=64, blank=True)

    clinic = models.ForeignKey(
        "accounts.Clinic",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_calls",
    )

    succeeded = models.BooleanField(default=False)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    # Message only, never a payload.
    error_message = models.CharField(max_length=300, blank=True)

    input_sha256 = models.CharField(max_length=64, blank=True)
    output_sha256 = models.CharField(max_length=64, blank=True)
    input_chars = models.PositiveIntegerField(default=0)

    #: True when a stub produced the result. Without this a development record
    #: is indistinguishable from a real one.
    simulated = models.BooleanField(default=True)

    #: True when the input left our infrastructure for a third-party processor.
    external = models.BooleanField(default=False)
    #: True when identifiers were stripped before it left. Meaningless unless
    #: ``external``, and recorded anyway so the pair is always readable.
    redacted = models.BooleanField(default=False)

    correlation_id = models.CharField(
        max_length=64,
        blank=True,
        help_text="Our own request id, from the logging middleware.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "AI call"
        verbose_name_plural = "AI calls"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["capability", "-created_at"]),
            models.Index(fields=["external", "-created_at"]),
        ]

    def __str__(self) -> str:
        outcome = "ok" if self.succeeded else "failed"
        return f"{self.capability} via {self.provider} ({outcome})"

    def save(self, *args, **kwargs):
        """Append-only, like the access log: a call record is evidence."""
        if self.pk is not None:
            raise ValueError("AiCall is append-only and cannot be modified.")
        return super().save(*args, **kwargs)


def stamp_confirmation(draft, *, actor) -> None:
    """Stamp a confirmation onto a draft. Does not save.

    A module function rather than a model method because the state rules that
    guard it live in a service, and a method named ``confirm`` on the model
    invites a caller to skip them.
    """
    draft.state = DraftState.CONFIRMED
    draft.confirmed_by = actor
    draft.confirmed_at = timezone.now()
