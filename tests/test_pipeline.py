import json

import pandas as pd

from kubera.categorize import CategoryCache, categorize
from kubera.contracts import detect_contracts
from kubera.extractors.common import to_frame
from kubera.pipeline import run
from kubera.transform import harmonize
from kubera.upload import typed, upload

OWN = "DE89370400440532013000"


def frame(rows, source="a.pdf", iban=OWN):
    return to_frame(rows, iban=iban, account_name="Main account", source=source)


def tx(date, amount, payee, iban="", purpose=""):
    return {"Booking Date": date, "Amount (€)": amount, "Payee": payee, "IBAN": iban, "Purpose": purpose}


# ---- de-duplication -------------------------------------------------------

def test_overlapping_statements_are_not_double_counted(config):
    a = frame([tx("2025-05-01", -9.99, "Spotify"), tx("2025-05-03", -40.00, "REWE")], "april.pdf")
    b = frame([tx("2025-05-03", -40.00, "REWE"), tx("2025-05-20", -12.00, "dm")], "may.pdf")
    df = harmonize([a, b], config)
    assert sorted(df["Payee"]) == ["REWE", "Spotify", "dm"]
    assert df.attrs["duplicates_dropped"] == 1


def test_identical_purchases_in_one_statement_are_both_kept(config):
    coffee = tx("2025-05-03", -3.50, "Cafe Mocca")
    df = harmonize([frame([coffee, coffee])], config)
    assert len(df) == 2  # the old drop_duplicates() merged these into one


def test_derived_fields(config):
    df = harmonize([frame([tx("2025-01-02", -10.0, "REWE"), tx("2025-12-29", 2500.0, "ACME GmbH")])], config)
    first, last = df.iloc[0], df.iloc[1]
    assert (first["Month"], first["Quarter"], first["Week"], first["Year"]) == ("2025-01", "2025-Q1", "2025-01", 2025)
    assert last["Week"] == "2026-01"  # ISO week of 29 Dec 2025 belongs to 2026
    assert (first["Analyzed Amount"], last["Analyzed Amount"]) == ("Expenses", "Income")
    assert last["payer"] == "ACME GmbH"


# ---- self transfers -------------------------------------------------------

def test_self_transfers_by_iban_and_by_name_are_excluded(config):
    df = harmonize([frame([
        tx("2025-05-01", -500.0, "Savings", iban="DE02120300000000202051"),
        tx("2025-05-02", 200.0, "DOE, JANE"),
        tx("2025-05-03", -40.0, "REWE"),
    ])], config)
    by_payee = df.set_index("Payee")
    for payee in ("Savings", "DOE, JANE"):
        assert by_payee.loc[payee, "Subcategory"] == "Self Transfer"
        assert by_payee.loc[payee, "Excluded from Disposable Income"]
    assert not by_payee.loc["REWE", "Excluded from Disposable Income"]
    assert by_payee.loc["REWE", "needs_manual_input"]


# ---- categorisation -------------------------------------------------------

def test_categorize_batches_validates_and_caches(config):
    df = harmonize([frame([
        tx("2025-05-01", -40.0, "REWE Markt"),
        tx("2025-05-02", -40.0, "REWE Markt"),          # same text: asked once
        tx("2025-05-03", -9.99, "Spotify AB"),
        tx("2025-05-04", -15.0, "Mystery Shop"),
    ])], config)
    prompts = []

    def fake(prompt):
        prompts.append(prompt)
        items = [line.split(": ", 1) for line in prompt.split("Transactions (id: text):\n")[1].split("\n\n")[0].splitlines()]
        answer = {
            "REWE Markt": ("Groceries", "Supermarket"),
            "Spotify AB": ("Leisure", "Subscription"),
            "Mystery Shop": ("Shopping", "Spaceships"),     # not an allowed pair -> rejected
        }
        return json.dumps([{"id": int(i), "main_category": answer[t][0], "subcategory": answer[t][1]} for i, t in items])

    out = categorize(df, config, fake).set_index("Payee")
    assert len(prompts) == 1  # three distinct texts, one batch
    assert out.loc["Spotify AB", "Main Category"] == "Leisure"
    assert (out.loc["REWE Markt", "Subcategory"] == "Supermarket").all()
    assert out.loc["Mystery Shop", "Main Category"] == ""
    assert out.loc["Mystery Shop", "needs_manual_input"]

    # second run: everything comes from the cache, no model call
    again = categorize(df.assign(**{"Main Category": "", "Subcategory": ""}), config, lambda p: 1 / 0)
    assert (again.set_index("Payee").loc["REWE Markt", "Subcategory"] == "Supermarket").all()
    assert CategoryCache(config.state_dir / "category_cache.json").get("Spotify AB") == ["Leisure", "Subscription"]


def test_failed_batches_are_not_cached(config, monkeypatch):
    monkeypatch.setattr("kubera.categorize.time.sleep", lambda s: None)
    df = harmonize([frame([tx("2025-05-01", -40.0, "REWE Markt")])], config)
    out = categorize(df, config, lambda p: "not json")
    assert out.loc[0, "needs_manual_input"]
    assert CategoryCache(config.state_dir / "category_cache.json").get("REWE Markt") is None


# ---- contracts ------------------------------------------------------------

def test_monthly_contract_with_one_late_payment():
    dates = ["2025-01-01", "2025-02-01", "2025-03-09", "2025-04-01", "2025-05-01"]
    df = pd.DataFrame({
        "Booking Date": dates + ["2025-01-05", "2025-03-17", "2025-03-18"],
        "Reference Account": OWN,
        "IBAN": ["DE12500105170000000001"] * 5 + [""] * 3,
        "Payee": ["Spotify"] * 5 + ["REWE"] * 3,
        "Amount (€)": [-9.99] * 5 + [-40.0, -12.0, -77.0],
    })
    out = detect_contracts(df)
    assert out["Contract"].tolist() == [True] * 5 + [False] * 3
    assert set(out.loc[out["Contract"], "Contract Frequency"]) == {"Monthly"}
    assert out.loc[0, "Contract ID"] and out.loc[0, "Contract ID"] == out.loc[4, "Contract ID"]


# ---- upload ---------------------------------------------------------------

class FakeTable:
    def __init__(self, log, name):
        self.log, self.name = log, name

    def upsert(self, rows, on_conflict):
        json.dumps(rows)  # must be JSON-serialisable, like the real client
        self.log.append((self.name, len(rows), on_conflict))
        return self

    def execute(self):
        return None


class FakeSupabase:
    def __init__(self):
        self.log = []

    def table(self, name):
        return FakeTable(self.log, name)


def test_upload_upserts_json_safe_rows_after_csv_round_trip(config, tmp_path):
    df = harmonize([frame([tx("2025-05-01", -40.0, "REWE"), tx("", -1.0, "no date")])], config)
    path = tmp_path / "t.csv"
    df.to_csv(path, index=False)
    back = typed(pd.read_csv(path, keep_default_na=False))
    assert back["needs_manual_input"].dtype == bool
    sb = FakeSupabase()
    accounts = pd.DataFrame([{"iban": OWN, "name": "Main", "bank": "DKB", "balance": 10.5, "balance_date": "2025-05-31"}])
    upload(back, accounts, sb)
    assert sb.log == [("transactions", 2, "tx_id"), ("accounts", 1, "iban")]


# ---- end to end -----------------------------------------------------------

def test_run_on_a_barclays_export(config, tmp_path):
    inbox = tmp_path / "transactions"
    inbox.mkdir()
    pd.DataFrame([
        ["IBAN", "DE75512108001245126199"],
        ["Kontoname", "Barclays Visa"],
        ["Saldo", "-120,50"],
        ["Referenznummer", "Buchungsdatum", "Beschreibung", "Betrag"],
        ["R1", "10.06.2025", "AMAZON EU", "-49,99 €"],
        ["R2", "12.06.2025", "NETFLIX.COM", "-13,99 €"],
    ]).to_excel(inbox / "barclays_june.xlsx", header=False, index=False)
    (inbox / "notes.txt").write_text("ignored")

    result = run(inbox, config, generate=None)
    assert len(result.transactions) == 2
    assert result.accounts.to_dict("records")[0]["balance"] == -120.5
    assert result.transactions["needs_manual_input"].all()  # --no-llm and empty cache


def test_contract_frequencies_are_not_confused():
    from kubera.contracts import _frequency

    assert _frequency([0, 7, 14, 21, 28]) == "Weekly"
    assert _frequency([0, 91, 182, 273]) == "Quarterly"
    assert _frequency([0, 365, 730]) == "Yearly"
    assert _frequency([0, 31, 59, 90, 120, 151]) == "Monthly"  # real calendar months
    assert _frequency([0, 3, 40, 41, 100]) == ""                # irregular shopping


def test_source_file_is_a_name_not_a_local_path(config, tmp_path):
    inbox = tmp_path / "transactions"
    inbox.mkdir()
    pd.DataFrame([["IBAN", "DE75512108001245126199"], ["Referenznummer", "Buchungsdatum", "Beschreibung", "Betrag"],
                  ["R1", "10.06.2025", "AMAZON EU", "-49,99"]]).to_excel(inbox / "b.xlsx", header=False, index=False)
    assert run(inbox, config).transactions["Source File"].tolist() == ["b.xlsx"]
