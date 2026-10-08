"""Recurring payments (rent, subscriptions, insurance) from the booking history."""

from __future__ import annotations

import hashlib

import pandas as pd

# frequency -> (typical gap in days, allowed deviation in days)
FREQUENCIES = {
    "Weekly": (7, 2),
    "Monthly": (30.4, 5),
    "Quarterly": (91, 10),
    "Yearly": (365, 20),
}
MIN_OCCURRENCES = 3
MIN_REGULAR_SHARE = 0.75   # share of gaps that must sit inside the band
MAX_AMOUNT_SPREAD = 0.25   # amounts may vary by 25% around the median (e.g. electricity)


def _frequency(days: list[float]) -> str:
    """Which schedule the payment days follow, if any.

    Each payment is compared with the schedule's grid (every 30.4 days, ...),
    not with the previous payment: one late payment then counts as ONE miss,
    instead of spoiling two gaps (one too long, the next too short).
    """
    gaps = pd.Series(days).diff().dropna()
    for name, (period, tol) in FREQUENCIES.items():
        if abs(gaps.median() - period) > max(tol, 0.25 * period):
            continue
        offsets = [d % period for d in days]

        def misses(anchor):
            return sum(min(abs(o - anchor), period - abs(o - anchor)) > tol for o in offsets)

        best = min(misses(a) for a in offsets)  # the grid that fits most payments
        if 1 - best / len(days) >= MIN_REGULAR_SHARE:
            return name
    return ""


def detect_contracts(df: pd.DataFrame) -> pd.DataFrame:
    """Mark groups of same-account, same-counterparty, same-direction payments
    that repeat at a steady interval with a steady amount."""
    df = df.copy()
    df["Contract"] = False
    df["Contract Frequency"] = ""
    df["Contract ID"] = ""
    dates = pd.to_datetime(df["Booking Date"], errors="coerce")
    counterparty = df["IBAN"].where(df["IBAN"] != "", df["Payee"].str.lower().str.strip())
    keys = pd.DataFrame({
        "account": df["Reference Account"],
        "party": counterparty,
        "sign": df["Amount (€)"] > 0,
    })
    for key, group in df.groupby([keys["account"], keys["party"], keys["sign"]]):
        if len(group) < MIN_OCCURRENCES or not key[1]:
            continue
        order = dates[group.index].dropna().sort_values()
        if len(order) < MIN_OCCURRENCES:
            continue
        freq = _frequency([(d - order.iloc[0]).days for d in order])
        amounts = group["Amount (€)"].abs()
        if not freq or (amounts - amounts.median()).abs().max() > MAX_AMOUNT_SPREAD * amounts.median():
            continue
        cid = hashlib.sha1("|".join(map(str, key)).encode()).hexdigest()[:10]
        df.loc[group.index, ["Contract", "Contract Frequency", "Contract ID"]] = [True, freq, cid]
    return df
