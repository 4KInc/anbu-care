"""Rupees, written the way the people reading them write rupees.

The dashboard renders `toLocaleString("en-IN")` and shows 2,70,720. Every
message built in Python used the comma format and showed 270,720. Same figure,
two shapes, for a family in India reading both — which is small, and is exactly
the kind of small that makes somebody start re-checking the arithmetic instead
of trusting it.

Indian grouping puts the last three digits together and then groups the rest in
twos: 1,00,000 rather than 100,000, and 12,34,56,789 rather than 123,456,789.
"""

from __future__ import annotations


def group(amount: int) -> str:
    """1234567 -> "12,34,567". No symbol, no sign handling beyond a minus."""
    negative = amount < 0
    digits = str(abs(int(amount)))

    if len(digits) <= 3:
        grouped = digits
    else:
        last_three, rest = digits[-3:], digits[:-3]
        # The remainder groups in twos, from the right.
        pairs = []
        while len(rest) > 2:
            pairs.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            pairs.insert(0, rest)
        grouped = ",".join([*pairs, last_three])

    return f"-{grouped}" if negative else grouped


def inr(amount: int) -> str:
    """"INR 2,70,720". The form every message uses."""
    return f"INR {group(amount)}"
