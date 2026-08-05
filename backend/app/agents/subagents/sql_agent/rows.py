"""Result rows, from driver-native values to something that can cross the API.

Until now rows only ever reached a language model, interpolated into a prompt string — so their
types never mattered, because repr() is legible enough for an LLM. Now they are serialised to JSON
and rendered in the browser, and psycopg2/pymysql hand back plenty that json refuses outright:
Decimal, date, datetime, timedelta, UUID, memoryview, and Postgres arrays and JSONB.
"""

import math
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Iterable


def to_jsonable(value: Any) -> Any:
    """One driver-native value, as something json.dumps and a chart library both accept."""
    if value is None or isinstance(value, (str, bool, int)):
        return value

    if isinstance(value, float):
        # NaN and ±Infinity are ordinary Python floats and ordinary postgres doubles, but they are
        # not JSON — serialised literally they make JSON.parse throw in the browser
        return value if math.isfinite(value) else None

    if isinstance(value, Decimal):
        # float rather than str so charts can plot it without the frontend parsing numbers back out
        # of strings. Precision goes past 2^53, which is fine for the money and counts that
        # aggregate queries actually return.
        return float(value) if value.is_finite() else None

    # datetime before date — it is a subclass, and would otherwise lose its time component
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()

    if isinstance(value, timedelta):
        # "1:30:00" rather than 5400.0, so a duration still reads as a duration — the synthesizer is
        # already asked to report time as hh:mm:ss
        return str(value)

    if isinstance(value, uuid.UUID):
        return str(value)

    if isinstance(value, (bytes, bytearray, memoryview)):
        # base64 of a blob is unreadable in a table and unplottable in a chart, and a few thousand
        # rows of it would dwarf every other column on the wire
        return f"<{len(bytes(value))} bytes>"

    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(item) for item in value]

    return str(value)


def unique_columns(keys: Iterable[Any]) -> list[str]:
    """Column labels, de-duplicated positionally.

    `SELECT c.id, o.id FROM ...` returns two columns both labelled "id", and zipping those straight
    into a dict silently drops the first — invisible while rows were only ever prose, wrong the
    moment a column becomes a chart axis.
    """
    seen: set[str] = set()
    names: list[str] = []

    for key in keys:
        base = str(key)
        name = base
        suffix = 1
        while name in seen:
            suffix += 1
            name = f"{base}_{suffix}"
        seen.add(name)
        names.append(name)

    return names
