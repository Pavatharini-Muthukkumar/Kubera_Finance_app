"""Command line: ``kubera run`` and ``kubera upload``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from kubera.config import load_config


def _run(args) -> int:
    from kubera.categorize import gemini_generator
    from kubera.pipeline import run

    config = load_config(args.config)
    generate = None if args.no_llm else gemini_generator(config.gemini_model)
    result = run(Path(args.input), config, generate)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tx = result.transactions
    tx.to_csv(out / "transactions.csv", index=False)
    result.accounts.to_csv(out / "accounts.csv", index=False)
    review = tx[tx["needs_manual_input"]]
    review.to_csv(out / "needs_review.csv", index=False)

    print(f"\n{len(tx)} transactions -> {out / 'transactions.csv'}")
    print(f"  duplicates dropped:   {result.duplicates_dropped}")
    print(f"  self transfers:       {int((tx['Subcategory'] == 'Self Transfer').sum())}")
    print(f"  recurring contracts:  {tx.loc[tx['Contract'], 'Contract ID'].nunique()}")
    print(f"  need manual category: {len(review)} -> {out / 'needs_review.csv'}")
    print(f"  accounts with balance: {len(result.accounts)} -> {out / 'accounts.csv'}")
    for msg in result.skipped + result.warnings:
        print(f"  ! {msg}")

    if args.upload:
        from kubera.upload import upload

        upload(tx, result.accounts)
        print("uploaded to Supabase")
    return 1 if result.skipped and not len(tx) else 0


def _upload(args) -> int:
    from kubera.upload import upload

    out = Path(args.out)
    tx = pd.read_csv(out / "transactions.csv", keep_default_na=False)
    accounts = pd.read_csv(out / "accounts.csv")
    upload(tx, accounts)
    print(f"uploaded {len(tx)} transactions")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="kubera", description="Bank statements -> categorised transactions")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="extract, clean, categorise and detect contracts")
    p.add_argument("--input", default="transactions", help="folder with PDF/XLSX statements")
    p.add_argument("--out", default="out")
    p.add_argument("--config", default=None, help="path to kubera.toml")
    p.add_argument("--no-llm", action="store_true", help="skip Gemini; use rules and the cache only")
    p.add_argument("--upload", action="store_true", help="upsert the result into Supabase")
    p.set_defaults(func=_run)

    u = sub.add_parser("upload", help="upsert an earlier run's output into Supabase")
    u.add_argument("--out", default="out")
    u.set_defaults(func=_upload)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
