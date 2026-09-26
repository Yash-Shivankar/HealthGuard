"""Turning a triage assessment into a FHIR R4 document (AUDIT 4.2 M2).

``utils/fhir`` knows FHIR and nothing about this project. This module is the
mapping, and it lives here so the dependency runs one way - which the
architecture test enforces.

## The mapping is where a clinical claim could get upgraded, so it does not

A triage result is a machine-generated suggestion. Three things stop it
arriving somewhere else looking like a diagnosis:

* the Condition's ``verificationStatus`` is ``unconfirmed`` unless a clinician
  has signed off, and ``confirmed`` only then;
* the symptoms travel as their own Observation, so a receiving clinician can
  work from the evidence without trusting our model at all;
* the narrative says in words that it is automated and, where applicable, that
  nobody has reviewed it - because a receiving system may not surface a status
  code, and the sentence survives being rendered as plain text.

## Abstention produces a document too

An assessment where the model declined to name a condition still has clinical
value: the symptoms are real and the abstention itself is information. That
bundle carries the Observation and no Condition, which says exactly what
happened - rather than being suppressed, which would look like the visit never
occurred.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from utils.fhir import document_bundle, validate_document_bundle
from utils.fhir.resources import (
    composition_resource,
    condition_resource,
    encounter_resource,
    new_id,
    observation_symptoms,
    organization_resource,
    patient_resource,
    practitioner_resource,
    section,
)

logger = logging.getLogger(__name__)


class FhirService:
    def build_assessment_document(
        self,
        *,
        assessment,
        abha_link=None,
        practitioner=None,
    ) -> dict[str, Any]:
        """A FHIR document bundle for one triage assessment.

        ``abha_link`` and ``practitioner`` are optional because both genuinely
        can be absent: a patient may have no ABHA, and an unreviewed assessment
        has no clinician attached. Neither is fabricated to fill a field.
        """
        patient = assessment.patient
        if patient is None:
            # An anonymous check has no subject, and FHIR requires one. Refusing
            # is right: inventing a placeholder patient would create a record
            # about nobody.
            raise ValueError(
                "An anonymous assessment cannot be shared: a FHIR document "
                "needs a subject."
            )

        clinic = assessment.clinic or patient.clinic
        recorded_at = assessment.created_at.isoformat()

        fhir_patient = patient_resource(
            name=patient.name,
            abha_number=abha_link.abha_number if abha_link else "",
            abha_address=abha_link.abha_address if abha_link else "",
            age_years=patient.age,
        )
        fhir_org = organization_resource(
            name=clinic.name if clinic else "HealthGuard",
            hfr_id=clinic.hfr_facility_id if clinic else "",
        )
        fhir_practitioner = (
            practitioner_resource(
                name=practitioner.get_full_name() or practitioner.get_username(),
                hpr_id=_hpr_id_for(practitioner, clinic),
            )
            if practitioner is not None
            else None
        )
        fhir_encounter = encounter_resource(
            patient=fhir_patient, started_at=recorded_at, organization=fhir_org
        )

        observation = observation_symptoms(
            patient=fhir_patient,
            encounter=fhir_encounter,
            symptoms=[_humanise(s) for s in assessment.symptoms],
            days=assessment.days,
            recorded_at=recorded_at,
        )

        reviewed = assessment.review_state != "unreviewed"
        conditions = self._conditions(
            assessment=assessment,
            patient=fhir_patient,
            encounter=fhir_encounter,
            recorded_at=recorded_at,
            reviewed=reviewed,
        )

        sections = [
            section(
                title="Reported symptoms",
                entries=[observation],
                text=_escape(
                    f"{len(assessment.symptoms)} symptom(s) reported over "
                    f"{assessment.days} day(s)."
                ),
            )
        ]
        if conditions:
            sections.append(
                section(
                    title="Possible conditions",
                    entries=conditions,
                    text=_escape(
                        "Ranked by symptom match. Not a diagnosis."
                        + ("" if reviewed else " Not reviewed by a clinician.")
                    ),
                )
            )
        else:
            sections.append(
                section(
                    title="Possible conditions",
                    entries=[],
                    text=_escape(
                        "The symptom checker did not have enough information to "
                        "suggest a condition."
                    ),
                )
            )

        composition = composition_resource(
            patient=fhir_patient,
            organization=fhir_org,
            practitioner=fhir_practitioner,
            encounter=fhir_encounter,
            created_at=recorded_at,
            title="Pre-consultation triage summary",
            sections=sections,
        )

        resources = [
            fhir_patient,
            fhir_org,
            fhir_encounter,
            observation,
            *conditions,
        ]
        if fhir_practitioner is not None:
            resources.insert(2, fhir_practitioner)

        bundle = document_bundle(
            composition=composition,
            resources=resources,
            timestamp=recorded_at,
            identifier=f"urn:uuid:{new_id()}",
        )

        # Validated on the way out, not on the way in. A malformed document
        # reaching a national network is far worse than one failing here, and
        # the cost of checking is a few dictionary walks.
        validate_document_bundle(bundle).raise_for_errors()

        logger.info(
            "FHIR document built",
            extra={
                "event": "fhir_document_built",
                "assessment_id": assessment.pk,
                "entries": len(bundle["entry"]),
                "reviewed": reviewed,
            },
        )
        return bundle

    @staticmethod
    def _conditions(*, assessment, patient, encounter, recorded_at, reviewed):
        if assessment.abstained or not assessment.differential:
            return []

        return [
            condition_resource(
                patient=patient,
                encounter=encounter,
                display=str(candidate.get("disease", "")),
                recorded_at=recorded_at,
                reviewed=reviewed,
                match_percent=round(float(candidate.get("confidence", 0)) * 100),
            )
            for candidate in assessment.differential
            if candidate.get("disease")
        ]


def canonical_json(bundle: dict[str, Any]) -> str:
    """Stable serialisation, so a hash of it is reproducible.

    Sorted keys and no incidental whitespace: the same bundle must hash the
    same way on two machines, or the transfer record proves nothing.
    """
    return json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def bundle_sha256(bundle: dict[str, Any]) -> str:
    """Hash of the canonical form.

    This is what the transfer record stores instead of the bundle: it answers
    "was this exact document sent?" without being a second copy of the clinical
    data outside the erasure path.
    """
    return hashlib.sha256(canonical_json(bundle).encode("utf-8")).hexdigest()


def _hpr_id_for(practitioner, clinic) -> str:
    if practitioner is None or clinic is None:
        return ""
    registration = practitioner.hpr_registrations.filter(clinic=clinic).first()
    return registration.hpr_id if registration else ""


def _humanise(symptom: str) -> str:
    cleaned = symptom.replace("_", " ").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else cleaned


def _escape(text: str) -> str:
    """FHIR narrative is XHTML, so text going into it must be escaped."""
    from html import escape

    return escape(text)
