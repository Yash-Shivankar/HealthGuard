"""FHIR R4 resource builders (AUDIT 4.2 M2, 7.1 ``utils/fhir/``).

## What is verified here and what is not

**FHIR R4 itself is a published HL7 standard**, and the resource shapes below
follow it: required elements, cardinality, reference syntax, the coding
structure. That part is verifiable and is verified, by
``utils/fhir/validation.py`` and its tests.

**The ABDM / NDHM Implementation Guide adds profile constraints on top** -
specific identifier systems, required slices, mandated code systems. Those are
*not* fully reflected here, because they are exactly the kind of detail that
differs between IG versions and that AUDIT 11.3's unanswered sandbox question
would settle. Where a value is an ABDM-specific guess it is marked
``# ABDM-SPECIFIC: verify`` so the set is greppable.

So: a bundle produced here is structurally valid FHIR R4. Whether it satisfies
the NDHM profile is unconfirmed, and is the first thing to check against the
sandbox.

## No clinical claim is upgraded on the way out

A triage result is a machine-generated suggestion that has not been reviewed.
The FHIR mapping must not launder that into something stronger: an unreviewed
assessment becomes a ``Condition`` with ``verificationStatus = unconfirmed``
and a note saying it is AI-generated, never ``confirmed``. TPG 2020 requires
clinician sign-off before an AI output enters a record, and a bundle is a
record leaving the building.
"""

from __future__ import annotations

import uuid
from typing import Any

# --- Systems ----------------------------------------------------------------
# Base FHIR systems. These are stable and published.
SYSTEM_CONDITION_CLINICAL = "http://terminology.hl7.org/CodeSystem/condition-clinical"
SYSTEM_CONDITION_VER_STATUS = "http://terminology.hl7.org/CodeSystem/condition-ver-status"
SYSTEM_LOINC = "http://loinc.org"
SYSTEM_SNOMED = "http://snomed.info/sct"

# ABDM-SPECIFIC: verify. The identifier systems ABDM expects for ABHA, HFR and
# HPR are part of the NDHM IG, and the exact URIs differ between versions.
SYSTEM_ABHA = "https://healthid.ndhm.gov.in"
SYSTEM_HFR = "https://facility.ndhm.gov.in"
SYSTEM_HPR = "https://hpr.abdm.gov.in"

#: LOINC code for a clinical note document. Used as the Composition type.
#: 11506-3 is "Progress note"; a triage summary is closest to that.
COMPOSITION_TYPE = {
    "coding": [{"system": SYSTEM_LOINC, "code": "11506-3", "display": "Progress note"}],
    "text": "Pre-consultation triage summary",
}


def new_id() -> str:
    """A fullUrl-safe identifier for a bundle-local resource."""
    return str(uuid.uuid4())


def reference(resource: dict[str, Any]) -> dict[str, str]:
    """A reference to another resource in the same bundle.

    ``urn:uuid:`` form, which is what a document bundle uses for resources that
    have no server-assigned identity yet.
    """
    return {"reference": f"urn:uuid:{resource['id']}"}


def patient_resource(
    *,
    resource_id: str | None = None,
    name: str,
    abha_number: str = "",
    abha_address: str = "",
    gender: str | None = None,
    age_years: int | None = None,
) -> dict[str, Any]:
    """A FHIR Patient.

    **The mobile number is deliberately absent.** FHIR has ``telecom`` and it
    would be easy to fill, but a bundle crosses a network boundary and the
    receiving system has no need for it. Sending the minimum that serves the
    purpose is the whole point of purpose limitation, and a contact number adds
    nothing to a clinical summary.

    ``birthDate`` is likewise omitted: the intake collects age, not a date of
    birth, and deriving one would be inventing precision the record does not
    have.
    """
    resource: dict[str, Any] = {
        "resourceType": "Patient",
        "id": resource_id or new_id(),
        "name": [{"text": name}],
    }

    identifiers = []
    if abha_number:
        identifiers.append(
            {
                # ABDM-SPECIFIC: verify the system URI and the type coding.
                "system": SYSTEM_ABHA,
                "value": "".join(ch for ch in abha_number if ch.isdigit()),
            }
        )
    if abha_address:
        identifiers.append({"system": SYSTEM_ABHA, "value": abha_address})
    if identifiers:
        resource["identifier"] = identifiers

    if gender:
        resource["gender"] = gender

    if age_years is not None:
        # Age as an extension rather than a fabricated birthDate. FHIR has no
        # core age element on Patient, and guessing a date from an age would
        # make the record look more precise than it is.
        resource["extension"] = [
            {
                "url": "http://hl7.org/fhir/StructureDefinition/patient-age",
                "valueAge": {"value": age_years, "unit": "a", "system": "http://unitsofmeasure.org", "code": "a"},
            }
        ]

    return resource


def organization_resource(
    *, resource_id: str | None = None, name: str, hfr_id: str = ""
) -> dict[str, Any]:
    """The clinic, as a FHIR Organization."""
    resource: dict[str, Any] = {
        "resourceType": "Organization",
        "id": resource_id or new_id(),
        "name": name,
    }
    if hfr_id:
        # ABDM-SPECIFIC: verify.
        resource["identifier"] = [{"system": SYSTEM_HFR, "value": hfr_id}]
    return resource


def practitioner_resource(
    *, resource_id: str | None = None, name: str, hpr_id: str = ""
) -> dict[str, Any]:
    """The clinician, as a FHIR Practitioner."""
    resource: dict[str, Any] = {
        "resourceType": "Practitioner",
        "id": resource_id or new_id(),
        "name": [{"text": name}],
    }
    if hpr_id:
        # ABDM-SPECIFIC: verify.
        resource["identifier"] = [{"system": SYSTEM_HPR, "value": hpr_id}]
    return resource


def encounter_resource(
    *,
    resource_id: str | None = None,
    patient: dict[str, Any],
    started_at: str,
    organization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The visit this summary belongs to.

    ``AMB`` (ambulatory) rather than anything more specific: a pre-consultation
    triage is an outpatient interaction, and claiming otherwise would be a
    coding error with billing consequences downstream.
    """
    resource: dict[str, Any] = {
        "resourceType": "Encounter",
        "id": resource_id or new_id(),
        "status": "finished",
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "AMB",
            "display": "ambulatory",
        },
        "subject": reference(patient),
        "period": {"start": started_at},
    }
    if organization is not None:
        resource["serviceProvider"] = reference(organization)
    return resource


def condition_resource(
    *,
    resource_id: str | None = None,
    patient: dict[str, Any],
    encounter: dict[str, Any],
    display: str,
    recorded_at: str,
    reviewed: bool,
    match_percent: int | None = None,
) -> dict[str, Any]:
    """A suggested condition.

    ``verificationStatus`` is the load-bearing field here:

    ``unconfirmed``  the default, for anything a clinician has not signed off.
    ``confirmed``    only after review.

    A triage suggestion arriving in another system as ``confirmed`` would be a
    machine diagnosis presented as a clinical finding, which is exactly what
    TPG 2020 prohibits and what the audit spent 4.3 warning about. The note
    below says so in words as well, because a receiving system may not surface
    the status field.
    """
    resource: dict[str, Any] = {
        "resourceType": "Condition",
        "id": resource_id or new_id(),
        "clinicalStatus": {
            "coding": [{"system": SYSTEM_CONDITION_CLINICAL, "code": "active"}]
        },
        "verificationStatus": {
            "coding": [
                {
                    "system": SYSTEM_CONDITION_VER_STATUS,
                    "code": "confirmed" if reviewed else "unconfirmed",
                }
            ]
        },
        # No SNOMED code: the source vocabulary is 41 free-text disease labels
        # from a public dataset, and mapping them to SNOMED without a
        # clinician's review would invent precision. AUDIT 10 lists that
        # mapping as a real piece of work, not a lookup.
        "code": {"text": display},
        "subject": reference(patient),
        "encounter": reference(encounter),
        "recordedDate": recorded_at,
    }

    note = (
        "Suggested by an automated symptom checker from patient-reported "
        "symptoms. Not a diagnosis."
    )
    if match_percent is not None:
        note += (
            f" Symptom match {match_percent}% - how closely the reported symptoms "
            "matched a known pattern, not the probability of having this condition."
        )
    if not reviewed:
        note += " NOT REVIEWED BY A CLINICIAN."
    resource["note"] = [{"text": note}]

    return resource


def observation_symptoms(
    *,
    resource_id: str | None = None,
    patient: dict[str, Any],
    encounter: dict[str, Any],
    symptoms: list[str],
    days: int,
    recorded_at: str,
) -> dict[str, Any]:
    """What the patient actually reported.

    Carried separately from the Condition on purpose: the symptoms are the
    *evidence*, and they are the part a receiving clinician can act on without
    trusting our model at all.
    """
    return {
        "resourceType": "Observation",
        "id": resource_id or new_id(),
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "survey",
                        "display": "Survey",
                    }
                ]
            }
        ],
        "code": {"text": "Patient-reported symptoms"},
        "subject": reference(patient),
        "encounter": reference(encounter),
        "effectiveDateTime": recorded_at,
        "valueString": ", ".join(symptoms),
        "note": [{"text": f"Reported duration: {days} day(s)."}],
    }


def composition_resource(
    *,
    resource_id: str | None = None,
    patient: dict[str, Any],
    organization: dict[str, Any],
    practitioner: dict[str, Any] | None,
    encounter: dict[str, Any],
    created_at: str,
    title: str,
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
    """The document's table of contents.

    A FHIR document bundle must have a Composition first, and its ``author``
    is mandatory. Where no clinician has been involved the author is the
    **Organization**, not a fabricated Practitioner - attributing a
    machine-generated summary to a named person who never saw it would be the
    worst kind of convenient default.
    """
    return {
        "resourceType": "Composition",
        "id": resource_id or new_id(),
        "status": "final",
        "type": COMPOSITION_TYPE,
        "subject": reference(patient),
        "encounter": reference(encounter),
        "date": created_at,
        "author": [reference(practitioner) if practitioner else reference(organization)],
        "title": title,
        "custodian": reference(organization),
        "section": sections,
    }


def section(*, title: str, entries: list[dict[str, Any]], text: str = "") -> dict[str, Any]:
    """One section of a Composition."""
    result: dict[str, Any] = {"title": title, "entry": [reference(e) for e in entries]}
    if text:
        result["text"] = {
            "status": "generated",
            # FHIR narrative must be XHTML.
            "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{text}</div>',
        }
    return result
