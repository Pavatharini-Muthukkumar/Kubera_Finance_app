"""Money and IBAN parsing shared by every bank extractor."""

from __future__ import annotations

import re

IBAN_DE = re.compile(r"DE\d{20}")
# A money token in either convention: 1.234,56 (German) or 1,234.56 (English).
AMOUNT_TOKEN = re.compile(r"[+-]?\d{1,3}(?:[.,\s]\d{3})*[.,]\d{2}|[+-]?\d+[.,]\d{2}")
EU_AMOUNT = re.compile(r"[+-]?\d{1,3}(?:\.\d{3})*,\d{2}")


def parse_amount(text: str) -> float | None:
    """Parse '1.234,56', '-1,234.56', '+250,00€', 'EUR 12.30' into a float.

    The decimal separator is whichever of '.' or ',' comes last and is followed
    by exactly two digits; the other one is a thousands separator. Returns None
    when the text holds no amount.
    """
    if text is None:
        return None
    s = re.sub(r"[^\d,.\-+]", "", str(text))
    if not s or not re.search(r"\d", s):
        return None
    sign = -1.0 if s.startswith("-") or s.endswith("-") else 1.0
    s = s.strip("+-")
    m = re.search(r"[.,](\d{2})$", s)
    if m:
        whole = re.sub(r"[.,]", "", s[: m.start()])
        value = float(f"{whole or 0}.{m.group(1)}")
    else:
        value = float(re.sub(r"[.,]", "", s))
    return round(sign * value, 2)


def compact(text: str) -> str:
    """Text with all whitespace removed -- PDFs often split an IBAN into groups."""
    return re.sub(r"\s+", "", text or "")


def find_ibans(text: str, exclude: str | None = None) -> list[str]:
    return [i for i in IBAN_DE.findall(compact(text)) if i != exclude]
