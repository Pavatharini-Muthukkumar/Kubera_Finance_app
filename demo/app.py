"""Kubera live demo: upload a statement, see how every booking is categorised.

Run locally:  streamlit run demo/app.py
Hosted:       Streamlit Community Cloud, with GEMINI_API_KEY in the app's secrets.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # the demo uses the repo's own kubera package

from kubera.categorize import gemini_generator  # noqa: E402
from kubera.config import Config  # noqa: E402
from kubera.pipeline import run  # noqa: E402

SAMPLE = ROOT / "examples" / "barclays_demo.xlsx"
MAX_NEW_TEXTS_PER_UPLOAD = 60   # Gemini texts one upload may use
DAILY_TEXT_LIMIT = 1500         # across all visitors, so a public demo cannot run up a bill
SOURCE_LABEL = {"rule": "🔧 rule", "own-account rule": "🔁 own account", "gemini": "✨ Gemini", "": "❔ needs review"}

st.set_page_config(page_title="Kubera demo", page_icon="💶", layout="wide")


@st.cache_resource
def _usage() -> dict:
    """Gemini texts used today, shared by every session on this server."""
    return {}


def _gemini_key() -> str | None:
    try:
        key = st.secrets.get("GEMINI_API_KEY")
    except Exception:  # no secrets file at all
        key = None
    return (key or os.getenv("GEMINI_API_KEY") or "").strip() or None


def _budget() -> int:
    used = _usage().get(dt.date.today().isoformat(), 0)
    return max(0, min(MAX_NEW_TEXTS_PER_UPLOAD, DAILY_TEXT_LIMIT - used))


def _spend(n: int) -> None:
    today = dt.date.today().isoformat()
    usage = _usage()
    usage[today] = usage.get(today, 0) + n


def analyse(name: str, data: bytes, use_gemini: bool, budget: int) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Run the real pipeline on one file, in a throw-away folder: nothing is kept on the server.

    The result is remembered only in this visitor's own session (see below), never in a
    server-wide cache that another visitor could hit.
    """
    with tempfile.TemporaryDirectory() as tmp:
        inbox = Path(tmp) / "in"
        inbox.mkdir()
        (inbox / Path(name).name).write_bytes(data)
        config = Config(state_dir=Path(tmp) / "state")  # per-upload cache, never shared between visitors
        generate = None
        if use_gemini and budget > 0:
            os.environ["GEMINI_API_KEY"] = _gemini_key()
            generate = gemini_generator(config.gemini_model)
        result = run(inbox, config, generate, max_new_texts=budget)
        notes = result.skipped + result.warnings
        if generate is not None:  # never echo the key, whatever an error message contains
            notes = [n.replace(_gemini_key(), "***") for n in notes]
        return result.transactions, result.accounts, notes


# ---------------------------------------------------------------------------- page

st.title("💶 Kubera: how each booking gets its category")
st.markdown(
    "Upload a bank statement and watch the pipeline work: it extracts every booking, "
    "then categorises it with a **hybrid** approach: well-known merchants by **rule** "
    "(free, instant, deterministic), everything else by **Gemini**. Each row shows which one decided."
)

key = _gemini_key()
if key:
    st.success(f"✨ Gemini is on: up to {MAX_NEW_TEXTS_PER_UPLOAD} new merchant texts per upload.", icon="✨")
else:
    st.info("Gemini is off on this server (no API key configured): only rules run, the rest is marked *needs review*.")

left, right = st.columns([3, 2])
with left:
    upload = st.file_uploader(
        "Statement: DKB, N26 or Deutsche Bank PDF, or a Barclays Excel export",
        type=["pdf", "xlsx"],
        help="Please upload a sample or anonymised statement. Files are processed in memory and deleted right after.",
    )
with right:
    st.write("No statement at hand?")
    use_sample = st.button("Try the sample statement", type="primary")
    st.download_button("Download the sample", SAMPLE.read_bytes(), file_name=SAMPLE.name)
    st.caption("The sample is synthetic: a made-up credit card with 15 bookings.")

if upload is not None:
    name, data = upload.name, upload.getvalue()
elif use_sample or st.session_state.get("sample") or st.query_params.get("sample") == "1":
    st.session_state["sample"] = True
    name, data = SAMPLE.name, SAMPLE.read_bytes()
else:
    st.stop()

budget = _budget() if key else 0
if key and budget == 0:
    st.warning("Today's Gemini budget for this demo is used up: showing rules only. Try again tomorrow.")
memo_key = (name, hashlib.sha256(data).hexdigest())
if st.session_state.get("memo_key") != memo_key:
    with st.spinner("Reading the statement and categorising…"):
        st.session_state["result"] = analyse(name, data, bool(key), budget)
    st.session_state["memo_key"] = memo_key
    if key:
        _spend(int((st.session_state["result"][0]["Categorised By"] == "gemini").sum()))
tx, accounts, notes = st.session_state["result"]

for note in notes:
    st.warning(note)
if tx.empty:
    st.error("No bookings found. Is this a DKB, N26, Deutsche Bank or Barclays statement?")
    st.stop()

# ---- summary
decided = tx["Categorised By"].fillna("")
spend = tx[(tx["Amount (€)"] < 0) & ~tx["Excluded from Disposable Income"]]
c = st.columns(5)
c[0].metric("Bookings", len(tx))
c[1].metric("🔧 By rule", int((decided == "rule").sum()))
c[2].metric("✨ By Gemini", int((decided == "gemini").sum()))
c[3].metric("❔ Needs review", int(tx["needs_manual_input"].sum()))
c[4].metric("Spending", f"{-spend['Amount (€)'].sum():,.2f} €")

# ---- the table that answers "how was this categorised?"
st.subheader("Every booking and who categorised it")
table = tx.assign(**{"Decided by": decided.map(SOURCE_LABEL)})[
    ["Booking Date", "Payee", "Amount (€)", "Main Category", "Subcategory", "Decided by", "Contract Frequency"]
].rename(columns={"Contract Frequency": "Recurring"})
st.dataframe(
    table, hide_index=True, use_container_width=True,
    column_config={"Amount (€)": st.column_config.NumberColumn(format="%.2f €")},
)

chart_col, contract_col = st.columns([3, 2])
with chart_col:
    st.subheader("Spending by category")
    by_cat = (
        spend.assign(cat=spend["Main Category"].replace("", "Uncategorised"))
        .groupby("cat")["Amount (€)"].sum().abs().sort_values(ascending=False)
    )
    st.bar_chart(by_cat, horizontal=True, x_label="€", y_label="")
with contract_col:
    st.subheader("Recurring payments found")
    contracts = tx[tx["Contract"]].groupby("Contract ID").agg(
        Payee=("Payee", "first"), Frequency=("Contract Frequency", "first"),
        Amount=("Amount (€)", lambda a: f"{abs(a.median()):.2f} €"), Times=("Payee", "size"),
    )
    if contracts.empty:
        st.caption("None in this statement (a payment needs at least 3 regular occurrences).")
    else:
        st.dataframe(contracts, hide_index=True, use_container_width=True)

st.download_button(
    "Download the result as CSV", tx.to_csv(index=False).encode(), file_name="kubera_transactions.csv"
)

with st.expander("How does the hybrid decide?"):
    st.markdown(
        """
1. **Own-account rule.** Money moved between the owner's own accounts is a self transfer and excluded
   from spending. (Configured per user, so it is off in this demo.)
2. **Merchant rules.** About 30 patterns for unmistakable German merchants (REWE, dm, Deutsche Bahn,
   Netflix …). Free, instant, and the same answer every time.
3. **Gemini.** Every other text, ~40 per request, with the answer checked against a fixed table of
   14 categories and 58 subcategories. An answer outside the table is rejected and the booking is
   marked *needs review*, never guessed.

The rules are audited against Gemini with `kubera audit-rules`, which reports how often both agree.
Source: [github.com/Pavatharini-Muthukkumar/Kubera_Finance_app](https://github.com/Pavatharini-Muthukkumar/Kubera_Finance_app)
"""
    )
