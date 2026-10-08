import pytest

from kubera.amounts import find_ibans, parse_amount


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1.234,56", 1234.56),
        ("-1.234,56", -1234.56),
        ("+250,00€", 250.0),
        ("-1,234.56", -1234.56),   # English: Deutsche Bank
        ("12,345,678.90", 12345678.90),
        ("45.00", 45.0),
        ("- 45.00", -45.0),
        ("EUR 3,50", 3.5),
        ("12,30 EUR", 12.3),
        ("100", 100.0),
        ("", None),
        ("n/a", None),
        (None, None),
    ],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


def test_find_ibans_joins_spaced_groups_and_excludes_own():
    text = "IBAN DE89 3704 0044 0532 0130 00 an DE02120300000000202051"
    assert find_ibans(text) == ["DE89370400440532013000", "DE02120300000000202051"]
    assert find_ibans(text, exclude="DE89370400440532013000") == ["DE02120300000000202051"]
