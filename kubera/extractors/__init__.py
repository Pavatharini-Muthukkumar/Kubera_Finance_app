"""Pick the right extractor for a statement file."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from kubera.config import Config
from kubera.extractors import barclays, deutsche_bank, dkb, n26
from kubera.extractors.common import ExtractResult, read_pdf_lines

EXTRACTORS = {
    dkb.BANK: dkb.extract,
    n26.BANK: n26.extract,
    deutsche_bank.BANK: deutsche_bank.extract,
    barclays.BANK: barclays.extract,
}


def detect_bank(path: str | Path, first_lines: list[str] | None = None) -> str | None:
    """Bank name from the statement's own header text, falling back to the filename.

    Content wins over the filename: a DKB transfer can mention 'N26' in its
    purpose, but only N26 prints its BIC in the statement header.
    """
    path = Path(path)
    name = path.name.lower()
    suffix = path.suffix.lower()

    if suffix in (".xlsx", ".xls"):
        try:
            head = pd.read_excel(path, header=None, nrows=15).astype(str)
            if head.apply(lambda col: col.str.contains("barclays", case=False)).any().any():
                return barclays.BANK
            if (head.iloc[:, 0] == "Referenznummer").any():
                return barclays.BANK
        except Exception:
            pass
        return barclays.BANK if "barclays" in name else None

    if suffix == ".pdf":
        lines = first_lines if first_lines is not None else read_pdf_lines(path, max_pages=2)
        header = " ".join(lines[:40]).lower()
        if n26.N26_BIC.lower() in header or "n26 bank" in header:
            return n26.BANK
        if "deutsche kreditbank" in header or "dkb ag" in header:
            return dkb.BANK
        if "deutsche bank" in header:
            return deutsche_bank.BANK
        for key, bank in (("n26", n26.BANK), ("dkb", dkb.BANK), ("kontoauszug", dkb.BANK),
                          ("deutsche", deutsche_bank.BANK), ("account_statement", deutsche_bank.BANK)):
            if key in name:
                return bank
        if name.startswith("db"):
            return deutsche_bank.BANK
        if "dkb" in header:  # weakest signal last: DKB sometimes prints only its short name
            return dkb.BANK
    return None


def extract_file(path: str | Path, config: Config) -> ExtractResult | None:
    bank = detect_bank(path)
    if bank is None:
        return None
    return EXTRACTORS[bank](path, config)
