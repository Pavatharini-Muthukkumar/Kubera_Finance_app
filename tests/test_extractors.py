"""Extractors on synthetic statement text (no real PDFs, no personal data)."""

import pandas as pd
import pytest

from kubera.extractors import barclays, deutsche_bank, detect_bank, dkb, n26

OWN = "DE89370400440532013000"


def test_dkb_bookings_payee_iban_and_running_balance(config):
    lines = [
        "Deutsche Kreditbank AG Kontoauszug",
        "IBAN DE89 3704 0044 0532 0130 00",
        "02.05.2025 Lastschrift -54,90 Stadtwerke Musterstadt Kd. 123",
        "Gläubiger-ID: DE98ZZZ09999999999 IBAN DE44500105175407324931",
        "05.05.2025 Gutschrift 2.500,00 ACME GmbH Gehalt Mai",
        "07.05.2025 Kartenzahlung -12,30 REWE Markt 4711",
        "Kontostand am 31.05.2025 3.432,80",
    ]
    r = dkb.parse_lines(lines, "dkb.pdf", config)
    df = r.transactions
    assert r.account_iban == OWN
    assert r.balance == 3432.80 and r.balance_date == "2025-05-31"
    assert df["Amount (€)"].tolist() == [-54.90, 2500.00, -12.30]
    assert df["Payee"].tolist() == ["Stadtwerke Musterstadt", "ACME GmbH Gehalt Mai", "REWE Markt 4711"]
    assert df.loc[0, "IBAN"] == "DE44500105175407324931"
    assert df["Transaction Type"].tolist() == ["SEPA Direct Debit", "Bank Transfer", "Card Payment"]
    # balance after the last booking is the closing balance; earlier ones walk back
    assert df["Balance (€)"].tolist() == [945.10, 3445.10, 3432.80]
    assert (df["Reference Account Name"] == "Main account").all()


def test_n26_counterparty_and_own_iban(config):
    lines = [
        "N26 Bank GmbH  BIC: NTSBDEB1XXX",
        "Spotify AB 03.06.2025 -9,99€",
        "IBAN: DE12 5001 0517 0000 0000 01",
        "Lastschrift Abo",
        "Jane Doe 05.06.2025 +250,00€",
        "IBAN: DE02 1203 0000 0000 2020 51",
        "Dein neuer Kontostand 1.240,01€",
        "IBAN: DE89 3704 0044 0532 0130 00 BIC: NTSBDEB1XXX",
    ]
    r = n26.parse_lines(lines, "n26.pdf", config)
    df = r.transactions
    assert r.account_iban == OWN
    assert r.balance == 1240.01
    assert df["Amount (€)"].tolist() == [-9.99, 250.00]
    assert df["IBAN"].tolist() == ["DE12500105170000000001", "DE02120300000000202051"]
    assert df.loc[0, "Purpose"] == "Lastschrift Abo"


def test_deutsche_bank_amounts_over_a_thousand_and_single_date_suffix(config):
    lines = [
        "Deutsche Bank Account statement IBAN DE89 3704 0044 0532 0130 00",
        "01-07- 01-07- SEPA Direct Debit Rent -1,234.56",
        "2025 1234 Hausverwaltung Muster",
        "IBAN DE44500105175407324931",
        "03-07- 03-07- SEPA Credit Transfer 45.00",
        "2025 5678 Friend Refund",
        "EUR +3,210.44",
    ]
    r = deutsche_bank.parse_lines(lines, "db.pdf", config)
    df = r.transactions
    assert df["Amount (€)"].tolist() == [-1234.56, 45.00]  # old code read -234.56
    assert df["Booking Date"].tolist() == ["2025-07-01", "2025-07-03"]  # no doubled ' 00:00:00'
    assert df["Payee"].tolist() == ["Hausverwaltung Muster", "Friend Refund"]
    assert df.loc[0, "IBAN"] == "DE44500105175407324931"
    assert r.balance == 3210.44


def test_barclays_sheet_never_uses_credit_limit_as_balance(config):
    raw = pd.DataFrame(
        [
            ["IBAN", "DE75512108001245126199"],
            ["Kontoname", "Barclays Visa"],
            ["Verfügungsrahmen", "3.000,00"],
            ["Referenznummer", "Buchungsdatum", "Beschreibung", "Betrag"],
            ["R1", "10.06.2025", "AMAZON EU", "-49,99 €"],
            ["R2", "12.06.2025", "Gutschrift Zahlung", "100,00 €"],
        ]
    )
    r = barclays.parse_sheet(raw, "barclays.xlsx", config)
    assert r.balance is None and r.warnings
    assert r.transactions["Amount (€)"].tolist() == [-49.99, 100.00]
    assert r.transactions["Booking Date"].tolist() == ["2025-06-10", "2025-06-12"]
    assert r.transactions["Transaction Type"].tolist() == ["Card Payment", "Bank Transfer"]


@pytest.mark.parametrize(
    "first_lines, name, bank",
    [
        (["Deutsche Kreditbank AG"], "statement.pdf", "DKB"),
        (["N26 Bank GmbH", "BIC NTSBDEB1XXX"], "statement.pdf", "N26"),
        (["Deutsche Bank AG"], "statement.pdf", "Deutsche Bank"),
        # purpose text naming another bank must not beat the header
        (["Deutsche Kreditbank AG", "Überweisung an N26 Konto"], "statement.pdf", "DKB"),
        (["Some header"], "Kontoauszug_05.pdf", "DKB"),
        (["Some header"], "unknown.pdf", None),
    ],
)
def test_detect_bank(first_lines, name, bank, tmp_path):
    assert detect_bank(tmp_path / name, first_lines=first_lines) == bank
