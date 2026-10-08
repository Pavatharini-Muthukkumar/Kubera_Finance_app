"""extract -> harmonize -> categorize -> contracts, as one call."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from kubera.categorize import Generate, categorize
from kubera.config import Config
from kubera.contracts import detect_contracts
from kubera.extractors import ExtractResult, extract_file
from kubera.transform import harmonize

log = logging.getLogger(__name__)
STATEMENT_SUFFIXES = {".pdf", ".xlsx", ".xls"}


@dataclass
class RunResult:
    transactions: pd.DataFrame
    accounts: pd.DataFrame
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duplicates_dropped: int = 0


def accounts_table(results: list[ExtractResult]) -> pd.DataFrame:
    """Latest known balance per account, from the newest statement of each."""
    rows = [
        {
            "iban": r.account_iban,
            "name": r.transactions["Reference Account Name"].iloc[0] if len(r.transactions) else r.bank,
            "bank": r.bank,
            "balance": r.balance,
            "balance_date": r.balance_date,
        }
        for r in results
        if r.account_iban and r.balance is not None
    ]
    if not rows:
        return pd.DataFrame(columns=["iban", "name", "bank", "balance", "balance_date"])
    df = pd.DataFrame(rows).sort_values("balance_date", na_position="first")
    return df.drop_duplicates("iban", keep="last").reset_index(drop=True)


def run(
    input_dir: Path,
    config: Config,
    generate: Generate | None = None,
    max_new_texts: int | None = None,
) -> RunResult:
    files = sorted(p for p in Path(input_dir).iterdir() if p.suffix.lower() in STATEMENT_SUFFIXES)
    results, skipped, warnings = [], [], []
    for path in files:
        try:
            result = extract_file(path, config)
        except Exception as e:
            skipped.append(f"{path.name}: {e}")
            log.exception("failed to extract %s", path.name)
            continue
        if result is None:
            skipped.append(f"{path.name}: bank not recognised")
            continue
        log.info("%s: %s, %d bookings", path.name, result.bank, len(result.transactions))
        results.append(result)
        warnings += result.warnings

    tx = harmonize([r.transactions for r in results], config)
    dropped = tx.attrs.get("duplicates_dropped", 0)
    tx = categorize(tx, config, generate, max_new_texts)
    warnings += tx.attrs.get("gemini_errors", [])
    if tx.attrs.get("gemini_rejected"):
        warnings.append(
            f"Gemini gave {len(tx.attrs['gemini_rejected'])} answer(s) outside the category table "
            "(left for review): " + "; ".join(tx.attrs["gemini_rejected"][:5])
        )
    tx = detect_contracts(tx)
    return RunResult(tx, accounts_table(results), skipped, warnings, dropped)
