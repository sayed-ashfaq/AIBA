"""What a result set looks like, small enough to put in a prompt.

Chart choice is decided by shape, not by content: whether a column holds dates, measurements or
labels, how many distinct values it has, how many rows there are. Sample rows can't answer that —
five rows of a "region" column read identically whether there are four regions or four thousand,
and that difference is the whole distance between a readable bar chart and an unreadable one.

So the model is shown this instead, and a handful of rows only for flavour.
"""

import re
from dataclasses import dataclass, replace
from typing import Any, Literal, Optional

from app.agents.sql_agent.db import QueryResult

Role = Literal["temporal", "numeric", "categorical"]

# a whole date, a month bucket, or a timestamp — "2026", on its own, is left out on purpose, since
# a four-digit string is just as likely to be a product code
_ISO_RE = re.compile(
    r"^\d{4}-\d{2}(-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?)?$"
)

# integers that are really calendar positions: EXTRACT(YEAR ...) and DATE_PART return plain numbers,
# and plotted as measures they produce a chart of the year 2026 rising steadily
_TEMPORAL_NAME_RE = re.compile(r"(?:^|_)(year|month|week|quarter|day|date|time|period|dt)s?(?:$|_)", re.I)

# identifiers are numbers that mean nothing when summed or averaged. Left as categorical they fall
# out of the running naturally: a key has one distinct value per row, which no axis rule accepts.
_KEY_NAME_RE = re.compile(r"(?:^|_)(id|uuid|guid)$", re.I)


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    role: Role
    distinct: int
    nulls: int
    # numeric and temporal columns only — the range is what tells a model whether a "month" column
    # spans two years or two days
    minimum: Optional[Any] = None
    maximum: Optional[Any] = None
    # an identifier rather than something to read. Charts avoid these on sight: a bar chart of
    # region_id says nothing that a bar chart of region_name doesn't say better.
    is_key: bool = False
    # other columns holding the same dimension under a different name — a join that selects both
    # region_id and region_name has two columns that vary together exactly. Picking one as the axis
    # and the other as the series splits each bar into a legend of one.
    duplicates: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResultProfile:
    row_count: int
    truncated: bool
    columns: list[ColumnProfile]

    def by_role(self, role: Role) -> list[ColumnProfile]:
        return [column for column in self.columns if column.role == role]

    def describe(self) -> str:
        """The prompt's view of the result. Two hundred tokens for a shape that would take
        thousands of rows to convey, and convey worse."""
        header = f"{self.row_count}{'+' if self.truncated else ''} rows, {len(self.columns)} columns."
        lines = [header]
        for column in self.columns:
            detail = f"{column.distinct} distinct"
            if column.nulls:
                detail += f", {column.nulls} null"
            if column.minimum is not None:
                detail += f", range {column.minimum} .. {column.maximum}"
            lines.append(f"  {column.name} ({column.role}): {detail}")
        return "\n".join(lines)


def _is_number(value: Any) -> bool:
    # bool is an int in Python, and a column of flags is a label, not a measurement
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _distinct(values: list[Any]) -> int:
    try:
        return len(set(values))
    except TypeError:
        # JSONB and array columns arrive as dicts and lists, which aren't hashable
        return len({repr(value) for value in values})


def _role_of(name: str, values: list[Any]) -> Role:
    if not values or _KEY_NAME_RE.search(name):
        return "categorical"

    if all(_is_number(value) for value in values):
        return "temporal" if _TEMPORAL_NAME_RE.search(name) else "numeric"

    if all(isinstance(value, str) for value in values) and all(_ISO_RE.match(value) for value in values):
        return "temporal"

    return "categorical"


def _range(role: Role, values: list[Any]) -> tuple[Optional[Any], Optional[Any]]:
    if role == "categorical" or not values:
        return None, None
    try:
        # ISO strings sort chronologically, which is the whole reason to_jsonable emits them
        return min(values), max(values)
    except TypeError:
        return None, None


def _one_to_one(rows: list[dict], left: str, right: str, distinct: int) -> bool:
    """Whether two columns vary together exactly, so that each value of one implies a value of the
    other. Only ever asked of columns that already share a distinct count, which is what keeps this
    from being a comparison of every pair."""
    try:
        pairs = {(row.get(left), row.get(right)) for row in rows}
    except TypeError:
        return False
    return len(pairs) == distinct


def _find_duplicates(rows: list[dict], columns: list[ColumnProfile]) -> dict[str, tuple[str, ...]]:
    labels = [c for c in columns if c.role != "numeric" and c.distinct > 1]
    found: dict[str, list[str]] = {c.name: [] for c in labels}

    for i, left in enumerate(labels):
        for right in labels[i + 1 :]:
            if left.distinct == right.distinct and _one_to_one(rows, left.name, right.name, left.distinct):
                found[left.name].append(right.name)
                found[right.name].append(left.name)

    return {name: tuple(others) for name, others in found.items() if others}


def profile_result(result: QueryResult) -> ResultProfile:
    columns = []

    for name in result.columns:
        values = [row.get(name) for row in result.rows]
        present = [value for value in values if value is not None]
        role = _role_of(name, present)
        minimum, maximum = _range(role, present)
        columns.append(
            ColumnProfile(
                name=name,
                role=role,
                distinct=_distinct(present),
                nulls=len(values) - len(present),
                minimum=minimum,
                maximum=maximum,
                is_key=bool(_KEY_NAME_RE.search(name)),
            )
        )

    duplicates = _find_duplicates(result.rows, columns)
    if duplicates:
        columns = [
            replace(column, duplicates=duplicates.get(column.name, ())) for column in columns
        ]

    return ResultProfile(row_count=result.row_count, truncated=result.truncated, columns=columns)
