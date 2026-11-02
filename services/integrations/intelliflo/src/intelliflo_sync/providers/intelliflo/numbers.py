"""Numbers as Intelliflo reads them in a JSON string field."""

from decimal import Decimal, InvalidOperation


def plain(value) -> str:
    """A number as plain decimal text, never scientific notation.

    `str(Decimal("0E-10"))` is "0E-10", which Intelliflo cannot parse (it answers with a
    JsonSerializationException on the amount); a zero or tiny amount from the lake is exactly that.
    Anything that is not a number is returned as `str(value)` unchanged.
    """
    try:
        return format(Decimal(str(value)), "f")
    except (InvalidOperation, ValueError):
        return str(value)
