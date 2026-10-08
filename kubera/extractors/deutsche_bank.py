"""Deutsche Bank PDF account statements (English layout).

A booking spans two lines: the first carries the dates, the booking text and
the amount; the second starts with the year and holds the counterparty.
Amounts use the English convention, e.g. ``-1,234.56``.
"""

from __future__ import annotations

import re
from pathlib import Path

from kubera.amounts import find_ibans, parse_amount
from kubera.config import Config
from kubera.extractors.common import ExtractResult, read_pdf_lines, to_frame, transaction_type

BANK = "Deutsche Bank"
# The amount is the last token of the line: '-1,234.56' or '45.00'
_TRAILING_AMOUNT = re.compile(r"([+-]?\s?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})\s*$")
_DATES = re.compile(r"(\d{2})-(\d{2})-\s*(\d{2})-(\d{2})-")  # booking dd-mm-, value dd-mm-
_YEAR_PAYEE = re.compile(r"(\d{4})\s+\d{4}\s+(.+)")
_BALANCE = re.compile(r"^EUR\s*([+-]?[\d.,]+)$")


def parse_lines(lines: list[str], source: str, config: Config) -> ExtractResult:
    header = find_ibans("\n".join(lines[:60]))
    iban = header[0] if header else ""

    balance = None
    for line in reversed(lines):
        m = _BALANCE.match(line.strip())
        if m:
            balance = parse_amount(m.group(1))
            if balance is not None:
                break

    rows, warnings = [], []
    for i, line in enumerate(lines[:-1]):
        if "SEPA" not in line:
            continue
        amount_m = _TRAILING_AMOUNT.search(line)
        if not amount_m:
            continue
        nxt = lines[i + 1]
        year_payee = _YEAR_PAYEE.match(nxt)
        dates = _DATES.search(line)
        booking_date = ""
        if dates and year_payee:
            day, month = dates.group(3), dates.group(4)  # value date
            booking_date = f"{year_payee.group(1)}-{month}-{day}"
        else:
            warnings.append(f"no date for booking on line {i + 1}: {line.strip()[:60]}")
        payee = (year_payee.group(2) if year_payee else nxt).strip()
        counterparty = ""
        for follow in lines[i + 2 : i + 7]:
            found = find_ibans(follow, exclude=iban)
            if found:
                counterparty = found[0]
                break
        rows.append({
            "Booking Date": booking_date,
            "Amount (€)": parse_amount(amount_m.group(1)),
            "Payee": payee,
            "IBAN": counterparty,
            "Purpose": line.strip(),
            "Transaction Type": transaction_type(line),
        })

    df = to_frame(rows, iban=iban, account_name=config.account_name(iban, BANK), source=source)
    last_date = df["Booking Date"].replace("", None).max() if len(df) else None
    return ExtractResult(BANK, iban, df, balance, last_date, warnings)


def extract(path, config: Config) -> ExtractResult:
    return parse_lines(read_pdf_lines(path), source=Path(path).name, config=config)
