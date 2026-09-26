"""ABDM endpoint definitions.

╔══════════════════════════════════════════════════════════════════════════╗
║  EVERYTHING IN THIS FILE IS UNVERIFIED.                                  ║
║                                                                          ║
║  These paths and payload field names are written from the published      ║
║  ABDM specification. They have NOT been round-tripped against a live     ║
║  sandbox, because AUDIT 11.3 - do we have a sandbox account? - is still  ║
║  unanswered.                                                             ║
║                                                                          ║
║  ABDM has shipped more than one major revision of these APIs. Before     ║
║  enabling ABDM_ENABLED, check every constant below against the current   ║
║  NHA documentation and the sandbox you were issued.                      ║
╚══════════════════════════════════════════════════════════════════════════╝

This file exists so that correction is a single, contained edit. Nothing above
it in the stack knows a URL or a field name: the services speak in operations
("link an ABHA for this patient"), the transport speaks in HTTP, and this is the
only translation between them.

The payload builders are functions rather than templates so that a field that
turns out to be named differently, or nested, changes in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Endpoint:
    operation: str
    method: str
    path: str


# --- Session ----------------------------------------------------------------
SESSION = Endpoint("session", "POST", "/gateway/v0.5/sessions")

# --- M1: ABHA ---------------------------------------------------------------
ABHA_CREATE = Endpoint("abha.create", "POST", "/v1/registration/mobile/createHealthId")
ABHA_VERIFY = Endpoint("abha.verify", "POST", "/v1/search/searchByHealthId")

# --- M1: registries ---------------------------------------------------------
HFR_REGISTER = Endpoint("hfr.register", "POST", "/v1/facility/register")
HPR_REGISTER = Endpoint("hpr.register", "POST", "/v1/practitioner/register")


def abha_create_payload(*, name: str, mobile: str, year_of_birth: int | None = None) -> dict[str, Any]:
    """Create an ABHA.

    Note what is **not** here: no symptom, no clinical content, nothing beyond
    the identity fields ABDM needs. A payload that carried more would put health
    data on a wire that does not require it.
    """
    payload: dict[str, Any] = {"name": name, "mobile": mobile}
    if year_of_birth is not None:
        payload["yearOfBirth"] = year_of_birth
    return payload


def abha_verify_payload(*, abha_number: str) -> dict[str, Any]:
    """Verify an ABHA the person already has.

    Digits only: people write the number with hyphens or spaces and the gateway
    is unlikely to want them.
    """
    return {"abhaNumber": "".join(ch for ch in abha_number if ch.isdigit())}


def hfr_register_payload(*, name: str, contact_email: str = "", contact_phone: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "contactEmail": contact_email,
        "contactPhone": contact_phone,
    }


def hpr_register_payload(*, name: str, email: str = "") -> dict[str, Any]:
    return {"name": name, "email": email}
