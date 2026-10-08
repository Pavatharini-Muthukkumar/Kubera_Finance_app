"""Supabase upload. Upserts, so re-running never creates duplicate rows."""

from __future__ import annotations

import logging
import math
import os

import pandas as pd

log = logging.getLogger(__name__)
CHUNK = 500


def _records(df: pd.DataFrame) -> list[dict]:
    """JSON-safe dicts: NaN/NaT -> None, numpy scalars -> Python."""
    def clean(v):
        if v is None or v is pd.NA or v is pd.NaT or (isinstance(v, float) and math.isnan(v)):
            return None
        return v.item() if hasattr(v, "item") else v  # numpy scalar -> Python

    return [{k: clean(v) for k, v in row.items()} for row in df.astype(object).to_dict(orient="records")]


def typed(tx: pd.DataFrame) -> pd.DataFrame:
    """Restore column types after a CSV round trip (booleans and numbers come back as text)."""
    from kubera.schema import BOOL_COLUMNS

    tx = tx.copy()
    for col in BOOL_COLUMNS:
        tx[col] = tx[col].astype(str).str.lower().isin(["true", "1", "yes"])
    for col in ("Amount (€)", "Balance (€)"):
        tx[col] = pd.to_numeric(tx[col], errors="coerce")
    tx["Year"] = pd.to_numeric(tx["Year"], errors="coerce").astype("Int64")
    return tx


def client():
    from supabase import create_client

    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_KEY in .env to upload.")
    return create_client(url, key)


def upload(transactions: pd.DataFrame, accounts: pd.DataFrame, sb=None) -> None:
    sb = sb or client()
    tx = typed(transactions)
    tx["Booking Date"] = tx["Booking Date"].replace("", None)
    rows = _records(tx)
    for start in range(0, len(rows), CHUNK):
        sb.table("transactions").upsert(rows[start : start + CHUNK], on_conflict="tx_id").execute()
    if len(accounts):
        sb.table("accounts").upsert(_records(accounts), on_conflict="iban").execute()
    log.info("uploaded %d transactions and %d accounts", len(rows), len(accounts))
