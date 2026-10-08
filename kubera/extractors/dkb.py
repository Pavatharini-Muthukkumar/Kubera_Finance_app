"""DKB (Deutsche Kreditbank) PDF account statements ("Kontoauszug")."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from kubera.amounts import EU_AMOUNT, find_ibans, parse_amount
from kubera.config import Config
from kubera.extractors.common import ExtractResult, read_pdf_lines, to_frame, transaction_type

BANK = "DKB"
_DATE = re.compile(r"^(\d{2}\.\d{2}\.\d{4})")
_STOP_WORDS = ("Kd.", "Kunden", "RG-N", "Rechnung", "Gläubiger-ID:", "KM-", "EUR", "r.F", "IBAN")


def _payee(block_text: str) -> str:
    """The words right after the amount, up to the first reference-looking token."""
    m = EU_AMOUNT.search(block_text)
    if not m:
        return ""
    words = block_text[m.end():].split()
    out = []
    for w in words:
        if w.startswith(_STOP_WORDS) or re.match(r"\d{5,}", w):
            break
        out.append(w)
    if out:
        return " ".join(out)
    return next((w for w in words if not w.startswith(_STOP_WORDS)), "")


def parse_lines(lines: list[str], source: str, config: Config) -> ExtractResult:
    header = find_ibans("\n".join(lines[:50]))
    iban = header[0] if header else ""

    balance, balance_date = None, None
    for line in reversed(lines):
        if "Kontostand am" in line:
            amounts = EU_AMOUNT.findall(line)
            balance = parse_amount(amounts[-1]) if amounts else None
            d = re.search(r"(\d{2}\.\d{2}\.\d{4})", line)
            if d:
                balance_date = datetime.strptime(d.group(1), "%d.%m.%Y").strftime("%Y-%m-%d")
            break

    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if _DATE.match(line):
            current = [line]
            blocks.append(current)
        elif "Kontostand" in line:
            current = None  # the balance footer is not part of the last booking
        elif current is not None:
            current.append(line)

    rows = []
    for block in blocks:
        first = block[0]
        amounts = EU_AMOUNT.findall(first)
        if not amounts:
            continue  # a dated line that is not a booking (e.g. the balance line)
        text = " ".join([first[10:].strip(), *(ln.strip() for ln in block[1:])]).strip()
        ibans = find_ibans(text, exclude=iban)
        rows.append({
            "Booking Date": datetime.strptime(_DATE.match(first).group(1), "%d.%m.%Y").strftime("%Y-%m-%d"),
            "Amount (€)": parse_amount(amounts[-1]),
            "Payee": _payee(text),
            "IBAN": ibans[-1] if ibans else "",
            "Purpose": text,
            "Transaction Type": transaction_type(text),
        })

    df = to_frame(rows, iban=iban, account_name=config.account_name(iban, BANK), source=source)
    if balance is not None and len(df):
        # Statements print the closing balance only; walk it back through the bookings.
        df = df.sort_values("Booking Date", kind="stable").reset_index(drop=True)
        running, out = balance, []
        for amount in reversed(df["Amount (€)"].tolist()):
            out.append(round(running, 2))
            running -= amount
        df["Balance (€)"] = out[::-1]
    return ExtractResult(BANK, iban, df, balance, balance_date)


def extract(path, config: Config) -> ExtractResult:
    return parse_lines(read_pdf_lines(path), source=Path(path).name, config=config)
