"""Barclays (Visa credit card) Excel exports.

The sheet starts with key/value rows (IBAN, Kontoname, ...) and the booking
table begins at the row whose first cell is 'Referenznummer'.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from kubera.amounts import parse_amount
from kubera.config import Config
from kubera.extractors.common import ExtractResult, to_frame, transaction_type

BANK = "Barclays"
_META_KEYS = {"IBAN", "Kontoname", "Kontonummer", "Stand", "Verfügungsrahmen", "Saldo"}


def parse_sheet(raw: pd.DataFrame, source: str, config: Config) -> ExtractResult:
    meta, header_row = {}, None
    for idx, row in raw.iterrows():
        key = str(row.iloc[0]).strip()
        if key in _META_KEYS and len(row) > 1:
            meta[key] = str(row.iloc[1]).strip()
        if key == "Referenznummer":
            header_row = idx
            break
    if header_row is None:
        raise ValueError(f"{source}: no 'Referenznummer' header row found")

    table = raw.iloc[header_row + 1 :].copy()
    table.columns = [str(c).strip() for c in raw.iloc[header_row]]
    table = table.dropna(how="all")

    iban = meta.get("IBAN", "").replace(" ", "").upper()
    dates = pd.to_datetime(table["Buchungsdatum"].astype(str).str.strip(), errors="coerce", dayfirst=True)
    descriptions = table["Beschreibung"].fillna("").astype(str).str.strip()
    rows = [
        {
            "Booking Date": d.strftime("%Y-%m-%d") if pd.notna(d) else "",
            "Amount (€)": parse_amount(a),
            "Payee": desc,
            "IBAN": "",  # card bookings have no counterparty IBAN
            "Purpose": desc,
            "Transaction Type": "Card Payment" if transaction_type(desc) == "Other" else transaction_type(desc),
        }
        for d, a, desc in zip(dates, table["Betrag"], descriptions)
        if parse_amount(a) is not None
    ]

    # 'Saldo' is the card's balance when the export has it. 'Verfügungsrahmen'
    # is the credit LIMIT, not a balance, so it is never used as one.
    balance = parse_amount(meta["Saldo"]) if "Saldo" in meta else None
    warnings = [] if balance is not None else [f"{source}: no 'Saldo' row, balance unknown"]

    name = meta.get("Kontoname") or config.account_name(iban, BANK)
    df = to_frame(rows, iban=iban, account_name=config.account_names.get(iban, name), source=source)
    last_date = df["Booking Date"].replace("", None).max() if len(df) else None
    return ExtractResult(BANK, iban, df, balance, last_date, warnings)


def extract(path, config: Config) -> ExtractResult:
    return parse_sheet(pd.read_excel(path, header=None), source=Path(path).name, config=config)
