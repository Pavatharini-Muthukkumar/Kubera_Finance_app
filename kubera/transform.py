"""Cleaning, derived fields, self-transfer rules and de-duplication."""

from __future__ import annotations

import hashlib
import re

import pandas as pd

from kubera.config import Config
from kubera.schema import BOOL_COLUMNS, COLUMNS, EXCLUDED_PAIRS

# Generic noise that says nothing about what was bought.
BASE_NOISE = [
    r"(?i)\bissuer\b",
    r"(?i)visa\s+debitkartenumsatz",
    r"(?i)kartenabrechnung\S*",
    r"(?i)kundennummer\s*:?\s*\S+",
    r"(?i)rechnung(?:snummer|s-?nr\.?)?\s*:?\s*\S*\d\S*",
    r"(?i)www\.\S+",
    r"[+-]?\d{1,3}(?:[.,]\d{3})*[.,]\d{2}\s*(?:EUR|€)?",  # amounts
    r"\bDE\d{20}\b",  # IBANs
]


def clean_text(text, patterns: list[str]) -> str:
    if not isinstance(text, str):
        return ""
    for p in patterns:
        text = re.sub(p, " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def add_fingerprints(df: pd.DataFrame) -> pd.DataFrame:
    """Give every booking a stable id: same booking in two statements -> same id.

    The id hashes account, date, amount, counterparty and text. Two genuinely
    identical bookings in ONE statement (two 3,50 € coffees on the same day)
    get occurrence numbers 0 and 1, so both survive; the same pair seen again
    in an overlapping statement maps onto the same two ids and is dropped.
    """
    base = df.apply(
        lambda r: hashlib.sha1(
            "|".join([
                _norm(r["Reference Account"]),
                str(r["Booking Date"])[:10],
                f"{float(r['Amount (€)']):.2f}",
                _norm(r["IBAN"]),
                _norm(r["Payee"]),
                _norm(r["Purpose"])[:80],
            ]).encode()
        ).hexdigest()[:16],
        axis=1,
    )
    occurrence = base.groupby([base, df["Source File"]]).cumcount()
    out = df.copy()
    out["tx_id"] = base + "-" + occurrence.astype(str)
    return out


def harmonize(frames: list[pd.DataFrame], config: Config) -> pd.DataFrame:
    """Extracted frames -> one de-duplicated table with every schema column."""
    frames = [f for f in frames if f is not None and len(f)]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    df = df[df["Amount (€)"].notna()].copy()
    df["Booking Date"] = pd.to_datetime(df["Booking Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for col in ("Payee", "IBAN", "Purpose", "Reference Account", "Reference Account Name"):
        df[col] = df[col].fillna("").astype(str).str.strip()

    df = add_fingerprints(df)
    before = len(df)
    df = df.drop_duplicates("tx_id", keep="first")
    df.attrs["duplicates_dropped"] = before - len(df)

    dates = pd.to_datetime(df["Booking Date"], errors="coerce")
    iso = dates.dt.isocalendar()
    df["Week"] = iso["year"].astype("string") + "-" + iso["week"].astype("string").str.zfill(2)
    df["Month"] = dates.dt.strftime("%Y-%m")
    df["Quarter"] = dates.dt.year.astype("string") + "-Q" + dates.dt.quarter.astype("string")
    df["Year"] = dates.dt.year.astype("Int64")
    df["Analyzed Amount"] = df["Amount (€)"].map(lambda a: "Income" if a > 0 else "Expenses")

    patterns = BASE_NOISE + config.noise_patterns
    payee = df["Payee"].map(lambda t: clean_text(t, patterns))
    purpose = df["Purpose"].map(lambda t: clean_text(t, patterns))
    df["text"] = (payee + " " + purpose.where(purpose != payee, "")).str.strip()
    df["payer"] = df["Payee"].where(df["Amount (€)"] > 0, "")

    for col in COLUMNS:
        if col not in df.columns:
            df[col] = False if col in BOOL_COLUMNS else ""
    return apply_self_transfers(df[COLUMNS].sort_values(["Booking Date", "tx_id"]).reset_index(drop=True), config)


def apply_self_transfers(df: pd.DataFrame, config: Config) -> pd.DataFrame:
    """Money moved between the owner's own accounts is not income or spending.

    Decided by rule, before any model call: the counterparty IBAN is one of the
    owner's accounts, or the payee is the owner's own name.
    """
    own = config.owner_ibans | set(df["Reference Account"].unique()) - {""}
    owner = config.owner_regex()
    is_self = df["IBAN"].isin(own)
    if owner is not None:
        is_self |= df["Payee"].str.contains(owner, na=False)
    df.loc[is_self, ["Main Category", "Subcategory", "Categorised By"]] = ["Banking", "Self Transfer", "own-account rule"]
    df.loc[is_self, "Internal Transfer"] = True
    return mark_excluded(df)


def mark_excluded(df: pd.DataFrame) -> pd.DataFrame:
    pairs = list(zip(df["Main Category"], df["Subcategory"]))
    df["Excluded from Disposable Income"] = [p in EXCLUDED_PAIRS for p in pairs]
    df["needs_manual_input"] = (df["Main Category"].fillna("") == "") | (df["Subcategory"].fillna("") == "")
    return df
