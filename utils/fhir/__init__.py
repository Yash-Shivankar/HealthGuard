"""FHIR R4 support (AUDIT 7.1, ``utils/fhir/``).

Generic: this package knows how to build FHIR resources and bundles and nothing
about HealthGuard's models. The mapping from domain objects to FHIR lives in
``apps/abdm/services/fhir_service.py``, so this stays reusable and the
dependency runs one way - which the architecture test enforces.
"""

from utils.fhir.bundle import document_bundle, find, iter_resources
from utils.fhir.validation import ValidationResult, validate_document_bundle

__all__ = [
    "ValidationResult",
    "document_bundle",
    "find",
    "iter_resources",
    "validate_document_bundle",
]
