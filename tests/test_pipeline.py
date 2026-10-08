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

def _fake_gemini(answers, prompts):
    """A stand-in for Gemini that answers from a dict and records each prompt."""
    def generate(prompt):
        prompts.append(prompt)
        body = prompt.split("Transactions (id: text):\n")[1].split("\n\n")[0]
        items = [line.split(": ", 1) for line in body.splitlines()]
        return json.dumps([
            {"id": int(i), "main_category": answers[t][0], "subcategory": answers[t][1]} for i, t in items
        ])
    return generate


def test_hybrid_rules_first_then_gemini_and_every_row_says_who_decided(config):
    df = harmonize([frame([
        tx("2025-05-01", -40.0, "REWE Markt"),               # rule
        tx("2025-05-02", -9.99, "Spotify AB"),                # rule
        tx("2025-05-03", -35.0, "Fahrradladen Meier"),        # Gemini
        tx("2025-05-04", -35.0, "Fahrradladen Meier"),        # same text: asked once
        tx("2025-05-05", -15.0, "Mystery Shop"),              # Gemini answers outside the table
        tx("2025-05-06", -500.0, "Savings", iban="DE02120300000000202051"),  # own account
    ])], config)
    prompts = []
    fake = _fake_gemini({
        "Fahrradladen Meier": ("Mobility", "Bicycle"),
        "Mystery Shop": ("Shopping", "Spaceships"),
    }, prompts)

    out = categorize(df, config, fake)
    by = out.drop_duplicates("Payee").set_index("Payee")
    assert by["Categorised By"].to_dict() == {
        "REWE Markt": "rule",
        "Spotify AB": "rule",
        "Fahrradladen Meier": "gemini",
        "Mystery Shop": "",
        "Savings": "own-account rule",
    }
    assert by.loc["REWE Markt", "Subcategory"] == "Supermarket"
    assert by.loc["Fahrradladen Meier", "Subcategory"] == "Bicycle"
    assert by.loc["Mystery Shop", "needs_manual_input"]           # invented pair rejected
    assert len(prompts) == 1                                       # one batch
    assert "REWE" not in prompts[0] and "Spotify" not in prompts[0]  # rules never reach Gemini

    # second run: Gemini's answers come from the cache, no call at all
    again = categorize(df, config, lambda p: 1 / 0)
    assert (again.set_index("Payee").loc["Fahrradladen Meier", "Categorised By"] == "gemini").all()


def test_demo_cap_limits_new_gemini_texts(config):
    df = harmonize([frame([tx("2025-05-0%d" % i, -1.0, f"Unknown {i}") for i in range(1, 6)])], config)
    prompts = []
    fake = _fake_gemini({f"Unknown {i}": ("Shopping", "Other Shopping") for i in range(1, 6)}, prompts)
    out = categorize(df, config, fake, max_new_texts=2)
    assert (out["Categorised By"] == "gemini").sum() == 2
    assert out["needs_manual_input"].sum() == 3


def test_failed_batches_are_not_cached(config, monkeypatch):
    monkeypatch.setattr("kubera.categorize.time.sleep", lambda s: None)
    df = harmonize([frame([tx("2025-05-01", -40.0, "Fahrradladen Meier")])], config)
    out = categorize(df, config, lambda p: "not json")
    assert out.loc[0, "needs_manual_input"]
    assert CategoryCache(config.state_dir / "category_cache.json").get("Fahrradladen Meier") is None


def test_rule_agreement_reports_where_rules_and_gemini_differ(config):
    from kubera.categorize import rule_agreement

    df = categorize(harmonize([frame([
        tx("2025-05-01", -40.0, "REWE Markt"),
        tx("2025-05-02", -9.99, "Spotify AB"),
    ])], config), config)
    fake = _fake_gemini({
        "REWE Markt": ("Groceries", "Supermarket"),
        "Spotify AB": ("Leisure", "Games"),           # disagrees with the rule
    }, [])
    report = rule_agreement(df, config, fake).set_index("text")
    assert report["agree"].to_dict() == {"REWE Markt": True, "Spotify AB": False}
    assert report.loc["Spotify AB", "gemini"] == "Leisure → Games"


def test_every_rule_points_at_an_allowed_category():
    from kubera import rules
    from kubera.schema import is_valid_category

    assert all(is_valid_category(m, s) for _, m, s in rules.RULES)
    assert rules.match("REWE Markt Erlangen") == ("Groceries", "Supermarket")
    assert rules.match("Lastschrift Stadtwerke Erlangen") == ("Housing", "Electricity")
    assert rules.match("Fahrradladen Meier") is None


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
    # no model: the two well-known merchants are still decided, by rule
    assert result.transactions["Categorised By"].tolist() == ["rule", "rule"]
    assert result.transactions["Subcategory"].tolist() == ["Online Shopping", "Subscription"]


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
