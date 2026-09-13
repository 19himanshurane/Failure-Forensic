"""Finding and normalising monetary amounts in free text.

Shared by the mock extractor and the real extraction step: both have to turn a
human-written amount into an exact Decimal, and both have to be honest about
the cases where the text is genuinely ambiguous.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

CODES = "USD|EUR|GBP|INR|JPY|CAD|AUD|CHF|CNY"
SYMBOL_TO_CODE = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY"}

# A run of digits that starts and ends with a digit, so separators cannot dangle
# off either end ("300." never matches as a whole number).
_NUM = r"\d(?:[\d,.]*\d)?"

MONEY = re.compile(
    rf"""
      (?P<sym>[$€£₹¥])\s?(?P<n1>{_NUM})          # $4,500.00   € 300
    | \b(?P<c1>{CODES})\s?(?P<n2>{_NUM})         # EUR 300     USD4500
    | (?P<n3>{_NUM})\s?(?P<c2>{CODES})\b         # 300 EUR     1,000 GBP
    """,
    re.VERBOSE | re.IGNORECASE,
)


class AmbiguousAmount(ValueError):
    """The text cannot be resolved to one number without guessing."""


def parse_amount(raw: str) -> tuple[Decimal, bool]:
    """Normalise a human-written number.

    Returns (value, ambiguous). Separator conventions collide across locales:
    "4.500" is four-point-five to a US reader and four thousand five hundred to
    a German one, and nothing in the string itself settles it. Rather than pick
    silently, we resolve what we can and flag what we cannot, so the uncertainty
    reaches the trace instead of being buried in a wrong number.
    """
    s = raw.replace(" ", "").replace("\u00a0", "").strip()
    ambiguous = False

    if "." in s and "," in s:
        # Both present: whichever comes last is the decimal separator.
        if s.rfind(".") > s.rfind(","):
            s = s.replace(",", "")               # 4,500.00  -> 4500.00
        else:
            s = s.replace(".", "").replace(",", ".")   # 4.500,00 -> 4500.00
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) == 2:
            s = s.replace(",", ".")              # 300,50 -> 300.50 (European)
        elif all(len(p) == 3 for p in parts[1:]):
            s = s.replace(",", "")               # 1,000,000 -> 1000000
        else:
            s = s.replace(",", "")
            ambiguous = True
    elif "." in s:
        parts = s.split(".")
        if len(parts) > 2:
            s = s.replace(".", "")               # 1.000.000 -> 1000000
        elif len(parts) == 2 and len(parts[1]) == 3:
            ambiguous = True                     # 4.500 -> 4.5 or 4500? flagged

    try:
        return Decimal(s), ambiguous
    except InvalidOperation as exc:
        raise AmbiguousAmount(f"cannot parse {raw!r} as a number") from exc


def find_amounts(text: str) -> list[tuple[re.Match, Decimal, str, bool]]:
    """Yield (match, value, ISO currency code, ambiguous) for each amount found."""
    out = []
    for m in MONEY.finditer(text):
        num = m.group("n1") or m.group("n2") or m.group("n3")
        if m.group("sym"):
            currency = SYMBOL_TO_CODE[m.group("sym")]
        else:
            currency = (m.group("c1") or m.group("c2")).upper()
        try:
            value, ambiguous = parse_amount(num)
        except AmbiguousAmount:
            continue
        out.append((m, value, currency, ambiguous))
    return out
