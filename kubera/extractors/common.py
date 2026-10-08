"""Pieces every bank extractor shares."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from kubera.schema import EXTRACTED_COLUMNS


@dataclass
class ExtractResult:
    """One statement's transactions plus the account it belongs to."""

    bank: str
    account_iban: str
    transactions: pd.DataFrame
    balance: float | None = None
    balance_date: str | None = None  # YYYY-MM-DD of the closing balance
    warnings: list[str] = field(default_factory=list)


def read_pdf_lines(path, max_pages: int | None = None) -> list[str]:
    import pdfplumber

    lines: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages[:max_pages]:
            text = page.extract_text()
            if text:
                lines.extend(text.split("\n"))
    return lines


# German booking words -> transaction type. First match wins, so the order matters.
_TYPE_KEYWORDS = [
    (("geldautomat", "bargeld"), "Cash Withdrawal"),
    (("dauerauftrag",), "Standing Order"),
    (("lastschrift",), "SEPA Direct Debit"),
    (("überweisung", "ueberweisung", "gutschrift", "lohn", "gehalt", "rente"), "Bank Transfer"),
    (("zins", "gebühr", "entgelt", "interest"), "Interest/Fee"),
    (("kartenzahlung", "debitk", "karte", "visa"), "Card Payment"),
]


def transaction_type(text: str) -> str:
    low = (text or "").lower()
    for words, kind in _TYPE_KEYWORDS:
        if any(w in low for w in words):
            return kind
    return "Other"


def to_frame(rows: list[dict], *, iban: str, account_name: str, source: str) -> pd.DataFrame:
    """Rows -> DataFrame with exactly EXTRACTED_COLUMNS, account fields filled in."""
    df = pd.DataFrame(rows)
    for col in EXTRACTED_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df["Reference Account"] = iban
    df["Reference Account Name"] = account_name
    df["Currency"] = df["Currency"].fillna("EUR")
    df["Source File"] = source
    if "Transaction Type" in df:
        df["Transaction Type"] = df["Transaction Type"].fillna(
            df["Purpose"].fillna("").map(transaction_type)
        )
    return df[EXTRACTED_COLUMNS]
