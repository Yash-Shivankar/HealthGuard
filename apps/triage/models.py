"""Record of a symptom check.

The audit provisions a ``triage`` app for "symptom intake + prediction"
(AUDIT 7.1) and the Phase 3 admin needs a prediction log to show (8.4, 8.5).
This is that record.

Two decisions worth stating, because both are about restraint:

**Every row is marked as AI-generated and unreviewed.**  Under the Telemedicine
Practice Guidelines 2020 an AI output needs clinician sign-off before it
enters a record, and AUDIT 8.3 asks for that state to be visually distinct
wherever it appears.  A field that defaults to "unreviewed" is how that gets
enforced rather than remembered.

**Linking to a patient is optional.**  A check can be run without giving any
details, and it should stay that way: collecting identity for a function that
does not need it is exactly what the DPDP notice obligations are aimed at. When
a patient *is* linked, erasing them cascades to their assessments, because a
symptom history tied to a person is health data about that person.
"""

from __future__ import annotations

from django.db import models
from simple_history.models import HistoricalRecords


class ReviewState(models.TextChoices):
    UNREVIEWED = "unreviewed", "AI draft - needs review"
    REVIEWED = "reviewed", "Reviewed by clinician"
    CORRECTED = "corrected", "Corrected by clinician"


class UrgencyLevel(models.TextChoices):
    UNKNOWN = "unknown", "No symptoms selected"
    ROUTINE = "routine", "Self-care, with monitoring"
    CONSULT = "consult", "Book a consultation"
    URGENT = "urgent", "See a doctor today"


class TriageAssessment(models.Model):
    # Denormalised from the patient so a scoped query does not have to join,
    # and so an anonymous check run inside a clinic can still be attributed to
    # it. Kept in step by AssessmentService, which is the only writer.
    clinic = models.ForeignKey(
        "accounts.Clinic",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assessments",
    )

    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="assessments",
        help_text="Null for an anonymous check.",
    )

    # JSONField rather than a join table: this is an immutable snapshot of what
    # was submitted, not a live relationship. It must keep reading correctly
    # even after the symptom vocabulary changes underneath it.
    symptoms = models.JSONField(default=list)
    days = models.PositiveSmallIntegerField()

    abstained = models.BooleanField(
        default=False,
        help_text="True when confidence fell below the threshold and no "
        "condition was named.",
    )
    # Snapshot of the ranked differential: [{"disease": ..., "confidence": ...}]
    differential = models.JSONField(default=list, blank=True)

    urgency_level = models.CharField(
        max_length=16, choices=UrgencyLevel.choices, default=UrgencyLevel.UNKNOWN
    )
    urgency_score = models.FloatField(default=0.0)

    # Provenance. MDSW guidance expects every output to be traceable to the
    # model version that produced it (AUDIT 6, guardrails).
    model_fingerprint = models.CharField(max_length=64, blank=True)
    model_trained_at = models.CharField(max_length=32, blank=True)
    abstain_threshold = models.FloatField(null=True, blank=True)

    review_state = models.CharField(
        max_length=16, choices=ReviewState.choices, default=ReviewState.UNREVIEWED
    )

    created_at = models.DateTimeField(auto_now_add=True)

    # Records the review_state transitions, which is the clinician sign-off
    # trail TPG 2020 expects (AUDIT 8.9). Cascades with the patient for the
    # same reason the patient's own history does.
    history = HistoricalRecords(cascade_delete_history=True)

    class Meta:
        verbose_name = "Triage assessment"
        verbose_name_plural = "Triage assessments"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["review_state", "-created_at"]),
            models.Index(fields=["urgency_level", "-created_at"]),
            models.Index(fields=["clinic", "-created_at"]),
            models.Index(fields=["clinic", "review_state"]),
        ]

    def __str__(self) -> str:
        return f"{self.top_disease or 'No condition named'} ({self.created_at:%Y-%m-%d})"

    @property
    def top_disease(self) -> str | None:
        if self.abstained or not self.differential:
            return None
        return self.differential[0].get("disease")

    @property
    def top_confidence(self) -> float | None:
        if self.abstained or not self.differential:
            return None
        return self.differential[0].get("confidence")

    @property
    def needs_review(self) -> bool:
        return self.review_state == ReviewState.UNREVIEWED

    @property
    def is_anonymous(self) -> bool:
        return self.patient_id is None
