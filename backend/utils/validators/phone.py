"""Indian mobile number handling.

Kept out of the form and serializer so both validate identically - the audit's
complaint in 2.7 was that client-side rules and server-side rules disagreed.
"""

from utils.exceptions.domain import ValidationError

COUNTRY_CODE = "91"
NATIONAL_LENGTH = 10
# TRAI mobile series currently in use.
VALID_FIRST_DIGITS = frozenset("6789")


def normalise_mobile(raw: str) -> str:
    """Accept what people type, return canonical 10 digits.

    Handles "+91 98765 43210", "098765-43210", "9876543210".
    """
    digits = "".join(ch for ch in str(raw) if ch.isdigit())

    if len(digits) == NATIONAL_LENGTH + len(COUNTRY_CODE) and digits.startswith(
        COUNTRY_CODE
    ):
        digits = digits[len(COUNTRY_CODE) :]
    elif len(digits) == NATIONAL_LENGTH + 1 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) != NATIONAL_LENGTH:
        raise ValidationError("Enter a 10-digit mobile number.")
    if digits[0] not in VALID_FIRST_DIGITS:
        raise ValidationError("An Indian mobile number starts with 6, 7, 8 or 9.")
    return digits


def mask_mobile(number: str) -> str:
    """Hide all but the last four digits (AUDIT 8.8)."""
    if not number:
        return ""
    if len(number) <= 4:
        return "•" * len(number)
    return f"{'•' * (len(number) - 4)} {number[-4:]}"
