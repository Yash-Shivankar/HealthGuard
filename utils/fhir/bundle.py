"""FHIR document bundle assembly (AUDIT 4.2 M2).

A ``document`` bundle, not a ``collection``: a document is an immutable,
attested snapshot with a Composition as its first entry, which is what "emit
notes as FHIR R4 bundles" means. A collection would be a bag of resources with
no statement about what it is or who stands behind it.

Two rules a document bundle must satisfy, both enforced by
``utils/fhir/validation.py``:

* the **first entry is the Composition**, and
* every ``urn:uuid:`` reference **resolves inside the bundle** - a document that
  points outside itself is not self-contained, and the receiving system has no
  way to follow the pointer.
"""

from __future__ import annotations

from typing import Any

from utils.fhir.resources import new_id


def document_bundle(
    *,
    composition: dict[str, Any],
    resources: list[dict[str, Any]],
    timestamp: str,
    identifier: str | None = None,
) -> dict[str, Any]:
    """Assemble a FHIR R4 document bundle.

    ``resources`` is everything the Composition references. The Composition is
    placed first regardless of where it appears in that list, because the order
    is part of the document contract rather than a convention.

    Identity is compared with ``is`` rather than ``==``: two resources can be
    equal by value and still be separate entries.
    """
    tail = [r for r in resources if r is not composition]

    return {
        "resourceType": "Bundle",
        "id": new_id(),
        "type": "document",
        # A stable business identifier, so a receiver can recognise a resend
        # rather than storing the same document twice.
        "identifier": {
            "system": "urn:ietf:rfc:3986",
            "value": identifier or f"urn:uuid:{new_id()}",
        },
        "timestamp": timestamp,
        "entry": [
            {"fullUrl": f"urn:uuid:{resource['id']}", "resource": resource}
            for resource in [composition, *tail]
        ],
    }


def iter_resources(bundle: dict[str, Any]):
    """Every resource in a bundle, in order."""
    for entry in bundle.get("entry", []):
        resource = entry.get("resource")
        if resource is not None:
            yield resource


def find(bundle: dict[str, Any], resource_type: str) -> list[dict[str, Any]]:
    """Every resource of one type."""
    return [r for r in iter_resources(bundle) if r.get("resourceType") == resource_type]
