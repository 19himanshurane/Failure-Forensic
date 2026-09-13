"""Money parsing: all three orderings, and honest handling of locale ambiguity."""

from decimal import Decimal

import pytest

from forensics.money import AmbiguousAmount, find_amounts, parse_amount


@pytest.mark.parametrize("text, value, currency", [
    ("$4,500.00", Decimal("4500.00"), "USD"),   # symbol then number
    ("$ 300", Decimal("300"), "USD"),
    ("€300", Decimal("300"), "EUR"),
    ("£1,250.75", Decimal("1250.75"), "GBP"),
    ("300 EUR", Decimal("300"), "EUR"),         # number then code
    ("1,000 GBP", Decimal("1000"), "GBP"),
    ("EUR 300", Decimal("300"), "EUR"),         # code then number -- the gap we fixed
    ("USD 4500", Decimal("4500"), "USD"),
    ("INR 90,000", Decimal("90000"), "INR"),
    ("USD1000", Decimal("1000"), "USD"),
    ("300 eur", Decimal("300"), "EUR"),         # case-insensitive
])
def test_all_three_orderings_are_found(text, value, currency):
    found = find_amounts(text)
    assert len(found) == 1
    _, got_value, got_currency, _ = found[0]
    assert got_value == value and got_currency == currency


def test_european_format_no_longer_silently_corrupts():
    """Regression: '4.500,00 EUR' used to parse as 50000."""
    (_, value, currency, ambiguous) = find_amounts("Total: 4.500,00 EUR")[0]
    assert value == Decimal("4500.00")
    assert currency == "EUR"
    assert not ambiguous


@pytest.mark.parametrize("raw, expected", [
    ("4,500.00", Decimal("4500.00")),   # US: comma groups, dot decimal
    ("4.500,00", Decimal("4500.00")),   # EU: dot groups, comma decimal
    ("1,000,000", Decimal("1000000")),
    ("1.000.000", Decimal("1000000")),
    ("300,50", Decimal("300.50")),      # EU decimal comma
    ("4500.00", Decimal("4500.00")),
    ("300", Decimal("300")),
])
def test_separator_conventions(raw, expected):
    value, _ = parse_amount(raw)
    assert value == expected


def test_genuinely_ambiguous_amount_is_flagged_not_guessed():
    """'4.500' is 4.5 or 4500 depending on locale. Nothing in the string decides."""
    value, ambiguous = parse_amount("4.500")
    assert ambiguous


def test_unambiguous_amounts_are_not_flagged():
    assert parse_amount("4,500.00")[1] is False
    assert parse_amount("300")[1] is False


def test_mixed_currencies_in_one_document():
    found = find_amounts("Fee of $4,500.00 plus EUR 300 handling and 50 GBP postage.")
    assert [(v, c) for _, v, c, _ in found] == [
        (Decimal("4500.00"), "USD"), (Decimal("300"), "EUR"), (Decimal("50"), "GBP"),
    ]


def test_garbage_is_rejected():
    with pytest.raises(AmbiguousAmount):
        parse_amount("not-a-number")
