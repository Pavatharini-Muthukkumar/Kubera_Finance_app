"""N26 PDF statements."""

from __future__ import annotations

import re
from pathlib import Path
from datetime import datetime

from kubera.amounts import compact, parse_amount
from kubera.config import Config
from kubera.extractors.common import ExtractResult, read_pdf_lines, to_frame, transaction_type

BANK = "N26"
N26_BIC = "NTSBDEB1XXX"
# 'PAYEE 14.05.2025 +250,00€'
_TX = re.compile(r"(.+?)\s+(\d{2}\.\d{2}\.\d{4})\s+([+-]?\d{1,3}(?:\.\d{3})*,\d{2})\s?€")
_IBAN_LABEL = re.compile(r"IBAN:\s*(DE\d{2}(?:\s?\d{4}){4}\s?\d{2})")
_BALANCE = re.compile(r"Dein neuer Kontostand\s*([+-]?\d{1,3}(?:\.\d{3})*,\d{2})\s?€")


def _own_iban(lines: list[str]) -> str:
    # Not "any of the owner's IBANs on the page": a transfer to your own savings
    # account prints that IBAN too, so it would be ambiguous.
    # 1. the IBAN printed next to N26's BIC (the account holder's own block)
    for line in lines:
        m = _IBAN_LABEL.search(line)
        if m and N26_BIC in line:
            return compact(m.group(1))
    # 2. last IBAN on the statement (N26 prints the holder's IBAN in the footer)
    for line in reversed(lines):
        m = _IBAN_LABEL.search(line) or re.search(r"(DE\d{2}(?:\s?\d{4}){4}\s?\d{2})", line)
        if m:
            return compact(m.group(1))
    return ""


def parse_lines(lines: list[str], source: str, config: Config) -> ExtractResult:
    iban = _own_iban(lines)
    rows = []
    for i, line in enumerate(lines):
        m = _TX.match(line)
        if not m:
            continue
        payee, date, amount = m.groups()
        counterparty, purpose = "", []
        for nxt in lines[i + 1 : i + 5]:
            if _TX.match(nxt) or "Kontostand" in nxt:
                break
            m2 = _IBAN_LABEL.search(nxt)
            if m2 and not counterparty:
                counterparty = compact(m2.group(1))
            else:
                purpose.append(nxt.strip())
        rows.append({
            "Booking Date": datetime.strptime(date, "%d.%m.%Y").strftime("%Y-%m-%d"),
            "Amount (€)": parse_amount(amount),
            "Payee": payee.strip(),
            "IBAN": "" if counterparty == iban else counterparty,
            "Purpose": " ".join(p for p in purpose if p),
            "Transaction Type": transaction_type(" ".join([payee, *purpose])),
        })

    balance = None
    for line in lines:
        m = _BALANCE.search(line)
        if m:
            balance = parse_amount(m.group(1))
            break

    df = to_frame(rows, iban=iban, account_name=config.account_name(iban, BANK), source=source)
    last_date = df["Booking Date"].max() if len(df) else None
    return ExtractResult(BANK, iban, df, balance, last_date)


def extract(path, config: Config) -> ExtractResult:
    return parse_lines(read_pdf_lines(path), source=Path(path).name, config=config)
