"""
Validation for Norwegian organisasjonsnummer (organization numbers).

A valid orgnr is exactly 9 digits, where the 9th digit is a check digit
computed with a mod-11 checksum over the first 8 digits. Validating locally
before ever calling the API means we never waste a request on a malformed
number, and we never let a typo silently resolve to some *other* real
company from a fuzzy lookup -- a key defense against "wrong company"
publication, which is an instant fail in this challenge.
"""

from __future__ import annotations

_WEIGHTS = [3, 2, 7, 6, 5, 4, 3, 2]


class InvalidOrgNumber(ValueError):
    """Raised when a string is not a syntactically valid Norwegian orgnr."""


def normalize(raw: str) -> str:
    """Strip whitespace/formatting (e.g. '923 609 016') down to digits."""
    return "".join(ch for ch in str(raw).strip() if ch.isdigit())


def compute_check_digit(first_eight: str) -> int:
    if len(first_eight) != 8 or not first_eight.isdigit():
        raise InvalidOrgNumber(f"expected 8 digits, got {first_eight!r}")
    total = sum(int(d) * w for d, w in zip(first_eight, _WEIGHTS))
    remainder = total % 11
    control = 0 if remainder == 0 else 11 - remainder
    if control == 10:
        raise InvalidOrgNumber("checksum yields 10, which is not a valid orgnr")
    return control


def is_valid(raw: str) -> bool:
    try:
        validate(raw)
        return True
    except InvalidOrgNumber:
        return False


def validate(raw: str) -> str:
    """Return the normalized, checksum-valid 9-digit orgnr, or raise."""
    digits = normalize(raw)
    if len(digits) != 9:
        raise InvalidOrgNumber(
            f"orgnr must be 9 digits after normalization, got {digits!r} "
            f"(len={len(digits)}) from input {raw!r}"
        )
    expected = compute_check_digit(digits[:8])
    actual = int(digits[8])
    if expected != actual:
        raise InvalidOrgNumber(
            f"checksum mismatch for {digits}: expected check digit {expected}, "
            f"found {actual}"
        )
    return digits
