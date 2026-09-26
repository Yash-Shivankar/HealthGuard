"""Stripping identifiers before text reaches a third-party processor.

AUDIT 6 guardrail: no patient-identifiable data to third-party LLM APIs without
a consent basis and a processor agreement. Consent and the agreement are checked
in ``providers.external_processing`` and in the services. This is the other half:
even with both in place, sending less is better than sending more, because
purpose limitation is not satisfied by having a lawful basis to over-share.

## Targeted, not general

Generic named-entity detection on Hinglish free text would be unreliable, and a
de-identifier that works most of the time is worse than one whose limits are
known. So this is **targeted**: we know exactly whose record this is, so the
patient's own name, phone number and ABHA are passed in and removed by value,
which cannot miss them. On top of that, pattern rules catch the identifiers that
have a shape - Indian mobile numbers, 14-digit ABHA numbers, ABHA addresses,
email addresses, long digit runs.

## What it does not do

It does not remove a third party's name mentioned in passing ("my brother Rahul
also had this"). Nothing short of real NER would, and claiming otherwise would
be the dangerous part. This is stated here, in :func:`redact`'s docstring, and
in the AI card, rather than implied by the function's existence.

That residual risk is one more reason the external path is off by default and
takes four separate conditions to open.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: What replaces a removed value. Kept readable rather than blanked, so a
#: clinician reading a redacted transcript can see that something was removed
#: rather than wondering why a sentence stops making sense.
NAME_TOKEN = "[patient]"
PHONE_TOKEN = "[phone]"
ID_TOKEN = "[id]"
EMAIL_TOKEN = "[email]"

# Indian mobile numbers: 10 digits starting 6-9, optionally +91 / 0 prefixed,
# and tolerant of the spaces and dashes people type.
PHONE_PATTERN = re.compile(r"(?:\+?91[\s-]?|0)?[6-9](?:[\s-]?\d){9}")

# ABHA number: 14 digits, usually written in 4-4-4-2 groups.
ABHA_NUMBER_PATTERN = re.compile(r"\b\d{2}(?:[\s-]?\d{4}){3}\b")

# ABHA address: handle@suffix, e.g. someone@sbx or someone@abdm.
ABHA_ADDRESS_PATTERN = re.compile(r"\b[\w.\-]{2,}@(?:sbx|abdm|ndhm)\b", re.IGNORECASE)

EMAIL_PATTERN = re.compile(r"\b[\w.\-+]+@[\w\-]+\.[\w.\-]+\b")

# Any remaining run of 7 or more digits. Deliberately last and deliberately
# blunt: a long number in a symptom description is not clinical content.
LONG_DIGITS_PATTERN = re.compile(r"\b\d{7,}\b")


@dataclass(frozen=True)
class Redaction:
    text: str
    #: How many substitutions were made, by kind. Recorded so a call log can
    #: say that redaction actually did something, rather than that it ran.
    counts: dict[str, int]

    @property
    def changed(self) -> bool:
        return any(self.counts.values())


def redact(
    text: str, *, name: str = "", contact: str = "", abha: str = ""
) -> Redaction:
    """Remove the identifiers we know about, then the ones with a shape.

    Pass the patient's own ``name``, ``contact`` and ``abha`` whenever the caller
    has them: removing a known value by exact match cannot miss, where a pattern
    can. The patterns then catch identifiers belonging to nobody we have on file.

    **This does not remove a third party's name** mentioned in the text. Only
    real named-entity recognition would, and pretending otherwise is the part
    that would actually cause harm. See the module docstring.
    """
    counts = {"name": 0, "phone": 0, "id": 0, "email": 0}
    result = str(text or "")

    # Known values first: an exact value is the only thing that is certain.
    for value in _name_variants(name):
        result, hits = _replace_literal(result, value, NAME_TOKEN)
        counts["name"] += hits

    if contact:
        result, hits = _replace_literal(result, contact, PHONE_TOKEN)
        counts["phone"] += hits
        # Also the bare 10 digits, in case the stored value carries a prefix.
        digits = re.sub(r"\D", "", contact)
        if len(digits) >= 10:
            result, hits = _replace_literal(result, digits[-10:], PHONE_TOKEN)
            counts["phone"] += hits

    if abha:
        result, hits = _replace_literal(result, abha, ID_TOKEN)
        counts["id"] += hits
        digits = re.sub(r"\D", "", abha)
        if digits:
            result, hits = _replace_literal(result, digits, ID_TOKEN)
            counts["id"] += hits

    # Then the shapes. Email before ABHA address, because an ABHA address has no
    # dot in its suffix and would otherwise be left behind by the email rule.
    result, hits = _replace_pattern(result, EMAIL_PATTERN, EMAIL_TOKEN)
    counts["email"] += hits
    result, hits = _replace_pattern(result, ABHA_ADDRESS_PATTERN, ID_TOKEN)
    counts["id"] += hits
    result, hits = _replace_pattern(result, ABHA_NUMBER_PATTERN, ID_TOKEN)
    counts["id"] += hits
    result, hits = _replace_pattern(result, PHONE_PATTERN, PHONE_TOKEN)
    counts["phone"] += hits
    result, hits = _replace_pattern(result, LONG_DIGITS_PATTERN, ID_TOKEN)
    counts["id"] += hits

    return Redaction(text=result, counts=counts)


def _name_variants(name: str) -> list[str]:
    """The full name and each of its parts, longest first.

    People are recorded as "Sunita Deshmukh" and refer to themselves as
    "Sunita". Single characters and very short fragments are skipped: removing
    every occurrence of a two-letter initial would mangle the text without
    protecting anyone.
    """
    cleaned = str(name or "").strip()
    if not cleaned:
        return []
    parts = [p for p in re.split(r"\s+", cleaned) if len(p) >= 3]
    variants = [cleaned] + parts if len(parts) > 1 else parts or [cleaned]
    return sorted({v for v in variants if len(v) >= 3}, key=len, reverse=True)


def _replace_literal(text: str, value: str, token: str) -> tuple[str, int]:
    if not value:
        return text, 0
    pattern = re.compile(rf"(?<!\w){re.escape(value)}(?!\w)", re.IGNORECASE)
    return _replace_pattern(text, pattern, token)


def _replace_pattern(text: str, pattern: re.Pattern[str], token: str) -> tuple[str, int]:
    result, count = pattern.subn(token, text)
    return result, count
