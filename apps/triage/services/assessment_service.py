"""Orchestrates a symptom check.

This is the logic that used to sit inside the ``predict`` view: run the model,
decide urgency, look up the clinical content, persist the result.  Having it
here means the HTML page and the JSON API run *identical* logic rather than two
copies that drift (AUDIT 7.1, 2.14).

No HTTP, no request object, no rendering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from apps.triage.models import ReviewState, TriageAssessment
from apps.triage.repositories.assessment_repository import AssessmentRepository
from apps.triage.services import content, predictor, urgency as urgency_rules

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssessmentResult:
    """Everything a caller needs to render or serialise a completed check."""

    assessment: TriageAssessment
    differential: predictor.Differential
    urgency: urgency_rules.Urgency
    description: str
    precautions: list[str]
    remedies: list[str]

    @property
    def abstained(self) -> bool:
        return self.differential.abstained

    @property
    def top(self) -> predictor.Candidate | None:
        return self.differential.top


class AssessmentService:
    def __init__(self, repository: AssessmentRepository | None = None):
        self.repository = repository or AssessmentRepository()

    def assess(
        self,
        *,
        symptoms: list[str],
        days: int,
        patient=None,
        clinic=None,
        persist: bool = True,
    ) -> AssessmentResult:
        """Run a check and, by default, record it.

        ``persist=False`` exists for callers that must not write - a preview,
        a test, or a future path where consent has not been given. It is an
        explicit opt-out rather than a silent default, because a prediction log
        that is missing entries is worse than not having one.
        """
        differential = predictor.predict(symptoms)

        if differential.abstained:
            # Declining to answer is not the same as reassuring: the abstention
            # path still points the person at a clinician (AUDIT 6, Tier 0.7).
            assessed_urgency = urgency_rules.abstention_urgency()
            description, precautions, remedies = "", [], []
        else:
            assessed_urgency = urgency_rules.assess(symptoms, days)
            disease = differential.top.disease
            description = content.description_for(disease)
            precautions = content.precautions_for(disease)
            remedies = content.remedies_for(disease)

        assessment = self._record(
            symptoms=symptoms,
            days=days,
            patient=patient,
            # Denormalised from the patient when not given explicitly, so a
            # scoped query never has to join to find out whose check this was.
            clinic=clinic if clinic is not None else getattr(patient, "clinic", None),
            differential=differential,
            assessed_urgency=assessed_urgency,
            persist=persist,
        )

        # Using the service is an interaction, so it stops any retention clock
        # that was already running against this person (AUDIT 4.1).
        if persist and patient is not None:
            self._cancel_retention(patient)

        return AssessmentResult(
            assessment=assessment,
            differential=differential,
            urgency=assessed_urgency,
            description=description,
            precautions=precautions,
            remedies=remedies,
        )

    @staticmethod
    def _cancel_retention(patient) -> None:
        # Imported here rather than at module scope: apps.compliance depends on
        # apps.patients, and a top-level import would close the cycle.
        from apps.compliance.services.retention_service import RetentionService

        try:
            RetentionService().cancel_for_patient(patient)
        except Exception:  # noqa: BLE001
            # A triage result must not fail because a retention bookkeeping
            # write did. The notice stays open and the next pass re-checks it.
            logger.exception("Could not cancel retention notice after interaction")

    def _record(
        self, *, symptoms, days, patient, clinic, differential, assessed_urgency, persist
    ) -> TriageAssessment:
        artefact = predictor.artefact()
        fields = {
            "patient": patient,
            "clinic": clinic,
            "symptoms": list(symptoms),
            "days": days,
            "abstained": differential.abstained,
            "differential": [
                {"disease": c.disease, "confidence": round(c.confidence, 4)}
                for c in differential.candidates
            ],
            "urgency_level": assessed_urgency.level,
            "urgency_score": round(assessed_urgency.score, 4),
            "model_fingerprint": artefact.data_fingerprint,
            "model_trained_at": artefact.trained_at,
            "abstain_threshold": differential.threshold,
            # Always starts unreviewed. TPG 2020 requires clinician sign-off
            # before an AI output enters a record (AUDIT 4.3).
            "review_state": ReviewState.UNREVIEWED,
        }

        if not persist:
            # Unsaved instance: the caller gets the same shape without a row.
            return TriageAssessment(**fields)

        assessment = self.repository.create(**fields)
        logger.info(
            "Triage assessment recorded",
            extra={
                "assessment_id": assessment.pk,
                "abstained": differential.abstained,
                "urgency": assessed_urgency.level,
                "symptom_count": len(symptoms),
                "model_fingerprint": artefact.data_fingerprint,
            },
        )
        return assessment

    def available_symptoms(self) -> list[dict[str, str]]:
        """Symptoms the model accepts, with display labels.

        Sourced from the artefact rather than a CSV, so the form can never
        offer something the model does not know.
        """
        return [
            {"value": name, "label": humanise(name)}
            for name in predictor.available_symptoms()
        ]


def humanise(symptom: str) -> str:
    """``foul_smell_of urine`` -> ``Foul smell of urine``.

    The raw feature names carry the source data's stray underscores and
    spaces; they should not be what a patient reads.
    """
    return symptom.replace("_", " ").strip().capitalize()
