"""Explicit contract selection for the local operator."""

from datetime import date
import re

from .events import Instrument

MONTHS = "FGHJKMNQUVXZ"


def gc_contract(symbol: str, expiry: str) -> Instrument:
    symbol = symbol.upper()
    match = re.fullmatch(r"GC([FGHJKMNQUVXZ])(\d{1,2})", symbol)
    if match is None:
        raise ValueError("Specify a listed COMEX GC contract such as GCZ6")
    date_value = date.fromisoformat(expiry)
    month, year = match.groups()
    if date_value.month != MONTHS.index(month) + 1 or not str(date_value.year).endswith(year):
        raise ValueError("Contract month/year and configured expiry disagree")
    return Instrument("COMEX", symbol, date_value.isoformat())
