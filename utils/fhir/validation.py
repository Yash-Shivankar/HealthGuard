"""Structural validation of a FHIR bundle.

**Not a full FHIR validator.** That needs the published StructureDefinitions and
a terminology server, which is a dependency this project does not have and does
not need in order to produce correct documents.

What this checks is the set of mistakes that are actually easy to make when
assembling a bundle, and that a receiving system will reject:

* a document bundle whose first entry is not the Composition
* a reference pointing at something that is not in the bundle
* a resource with no ``id``, a duplicate ``id``, or a ``fullUrl`` that does not
  match its ``id``
* a missing required element on the resources this project emits

That is a narrower claim than "this is valid FHIR", and it is the claim this
module supports. Overstating it would be worse than saying nothing, because a
green check that means less than it appears to is how a malformed document
reaches a national network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from utils.fhir.bundle import iter_resources

#: Elements each resource type must carry. Not exhaustive FHIR cardinality -
#: the subset the builders in ``utils/fhir/resources.py`` are required to
#: populate, so one that silently drops a field is caught.
REQUIRED: dict[str, tuple[str, ...]] = {
    "Composition": ("status", "type", "subject", "date", "author", "title"),
    "Patient": ("name",),
    "Organization": ("name",),
    "Practitioner": ("name",),
    "Encounter": ("status", "class", "subject"),
    "Condition": ("clinicalStatus", "verificationStatus", "code", "subject"),
    "Observation": ("status", "code", "subject"),
}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def __bool__(self) -> bool:
        return self.ok

    def raise_for_errors(self) -> None:
        if self.errors:
            raise ValueError("Invalid FHIR bundle: " + "; ".join(self.errors))


def validate_document_bundle(bundle: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()

    if bundle.get("resourceType") != "Bundle":
        result.errors.append("resourceType must be Bundle")
        return result

    if bundle.get("type") != "document":
        result.errors.append(f"bundle type must be 'document', got {bundle.get('type')!r}")

    if not bundle.get("timestamp"):
        result.errors.append("document bundle needs a timestamp")

    entries = bundle.get("entry", [])
    if not entries:
        result.errors.append("bundle has no entries")
        return result

    first = entries[0].get("resource", {})
    if first.get("resourceType") != "Composition":
        result.errors.append(
            f"first entry must be the Composition, got {first.get('resourceType')!r}"
        )

    _check_ids(entries, result)
    _check_required(bundle, result)
    _check_references(bundle, result)

    return result


def _check_ids(entries: list[dict[str, Any]], result: ValidationResult) -> None:
    seen: set[str] = set()

    for entry in entries:
        resource = entry.get("resource", {})
        resource_id = resource.get("id")
        kind = resource.get("resourceType", "?")

        if not resource_id:
            result.errors.append(f"{kind} has no id")
            continue

        if resource_id in seen:
            result.errors.append(f"duplicate resource id {resource_id}")
        seen.add(resource_id)

        expected = f"urn:uuid:{resource_id}"
        if entry.get("fullUrl") != expected:
            result.errors.append(
                f"{kind} fullUrl {entry.get('fullUrl')!r} does not match its id"
            )


def _check_required(bundle: dict[str, Any], result: ValidationResult) -> None:
    for resource in iter_resources(bundle):
        kind = resource.get("resourceType", "")
        for element in REQUIRED.get(kind, ()):
            if not resource.get(element):
                result.errors.append(f"{kind} is missing required element {element!r}")


def _check_references(bundle: dict[str, Any], result: ValidationResult) -> None:
    """Every ``urn:uuid:`` reference must resolve inside the bundle."""
    present = {f"urn:uuid:{r['id']}" for r in iter_resources(bundle) if r.get("id")}

    for resource in iter_resources(bundle):
        for path, target in _walk_references(resource):
            if target.startswith("urn:uuid:") and target not in present:
                result.errors.append(
                    f"{resource.get('resourceType')}.{path} references {target}, "
                    "which is not in the bundle"
                )


def _walk_references(node: Any, path: str = ""):
    """Yield ``(path, reference)`` for every ``{"reference": ...}`` in a tree."""
    if isinstance(node, dict):
        target = node.get("reference")
        if isinstance(target, str):
            yield path or "reference", target
        for key, value in node.items():
            if key != "reference":
                yield from _walk_references(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _walk_references(item, f"{path}[{index}]")
