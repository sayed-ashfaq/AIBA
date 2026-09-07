"""Result-set equivalence for NL2SQL scoring — "execution accuracy".

There is never one correct SQL string, so we never compare SQL text. We run both queries
and ask: do they return the same data? That question has three wrinkles this module handles:

  1. Row order. Only meaningful when the gold query has a top-level ORDER BY. Otherwise two
     result sets with the same rows in a different order are equal. ``infer_ordered`` decides.
  2. Numbers. 3.3333333 vs 3.33333330001 should be equal. Cell comparison uses a tolerance.
  3. Column order. The agent may SELECT the same columns in a different order (or alias them
     differently). We compare by position, but if that fails and there are few columns we
     try every column permutation before declaring a mismatch — a cosmetic reordering
     should not read as a wrong answer.

Run this file directly (``python lib/compare.py``) to see the self-tests, which double as
documentation of exactly what counts as equal.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import permutations
from typing import Any, Sequence

try:  # optional; only used to detect a top-level ORDER BY
    import sqlglot
except ImportError:  # pragma: no cover
    sqlglot = None

DEFAULT_FLOAT_TOL = 1e-6
_MAX_PERM_COLS = 7  # 7! = 5040 permutations; beyond this, positional only
_DIFF_SAMPLE = 5  # differing rows to include in a mismatch reason


# --- ordered vs unordered --------------------------------------------------------------------


def infer_ordered(gold_sql: str) -> bool:
    """True when row order is part of the answer, i.e. the OUTERMOST query has an ORDER BY.

    An ORDER BY inside a CTE or subquery does not count — only an ordering on the final
    SELECT that the caller actually sees. Falls back to a dumb substring check if sqlglot
    is unavailable or cannot parse the statement.
    """
    if sqlglot is not None:
        try:
            expr = sqlglot.parse_one(gold_sql, read="postgres")
            if expr is not None:
                final = expr.expression if expr.key == "with" else expr
                return final is not None and final.args.get("order") is not None
        except Exception:
            pass
    return "order by" in gold_sql.lower()


# --- cell normalisation --------------------------------------------------------------------


def normalize_cell(v: Any) -> Any:
    """Collapse driver-specific types to something JSON-serialisable and comparable."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    return v


def _as_number(v: Any):
    if isinstance(v, bool):  # do not treat True/False as 1/0
        return None
    if isinstance(v, (int, float, Decimal)):
        try:
            return float(v)
        except Exception:
            return None
    return None


def _cells_equal(a: Any, b: Any, tol: float) -> bool:
    a, b = normalize_cell(a), normalize_cell(b)
    if a is None or b is None:
        return a is None and b is None
    na, nb = _as_number(a), _as_number(b)
    if na is not None and nb is not None:
        if math.isnan(na) or math.isnan(nb):
            return math.isnan(na) and math.isnan(nb)
        return abs(na - nb) <= tol + tol * max(abs(na), abs(nb))
    return a == b or str(a) == str(b)


def _rows_equal(r1: Sequence, r2: Sequence, tol: float) -> bool:
    return len(r1) == len(r2) and all(_cells_equal(x, y, tol) for x, y in zip(r1, r2))


def _cell_sort_key(c: Any):
    c = normalize_cell(c)
    n = _as_number(c)
    if c is None:
        return (0, 0.0, "")
    if n is not None:
        return (1, n, "")
    return (2, 0.0, str(c))


def _row_sort_key(row: Sequence):
    return tuple(_cell_sort_key(c) for c in row)


def _fmt(row: Sequence, width: int = 160) -> str:
    s = "(" + ", ".join(repr(normalize_cell(c)) for c in row) + ")"
    return s if len(s) <= width else s[: width - 3] + "..."


# --- comparison --------------------------------------------------------------------


@dataclass
class Comparison:
    equal: bool
    reason: str
    gold_row_count: int = 0
    got_row_count: int = 0
    column_perm: list[int] | None = None  # set when a column reorder was needed to match
    missing_sample: list = field(default_factory=list)  # rows in gold but not in agent output
    extra_sample: list = field(default_factory=list)  # rows in agent output but not in gold


def _diff_sample(gold_rows, got_rows, tol):
    used = [False] * len(got_rows)
    missing = []
    for g in gold_rows:
        hit = next(
            (i for i, a in enumerate(got_rows) if not used[i] and _rows_equal(g, a, tol)),
            None,
        )
        if hit is None:
            missing.append(g)
        else:
            used[hit] = True
    extra = [a for i, a in enumerate(got_rows) if not used[i]]
    return (
        [_fmt(r) for r in missing[:_DIFF_SAMPLE]],
        [_fmt(r) for r in extra[:_DIFF_SAMPLE]],
    )


def _compare_rows(gold_rows, got_rows, *, ordered: bool, tol: float) -> Comparison:
    if len(gold_rows) != len(got_rows):
        miss, extra = _diff_sample(gold_rows, got_rows, tol)
        return Comparison(
            False,
            f"row count differs: gold {len(gold_rows)}, agent {len(got_rows)}",
            missing_sample=miss,
            extra_sample=extra,
        )

    if ordered:
        for i, (g, a) in enumerate(zip(gold_rows, got_rows)):
            if not _rows_equal(g, a, tol):
                return Comparison(
                    False, f"ordered mismatch at row {i}: gold={_fmt(g)} agent={_fmt(a)}"
                )
        return Comparison(True, "match (ordered)")

    gs = sorted(gold_rows, key=_row_sort_key)
    as_ = sorted(got_rows, key=_row_sort_key)
    for i, (g, a) in enumerate(zip(gs, as_)):
        if not _rows_equal(g, a, tol):
            miss, extra = _diff_sample(gs, as_, tol)
            return Comparison(
                False,
                f"unordered mismatch (first divergence at sorted position {i}): "
                f"gold={_fmt(g)} agent={_fmt(a)}",
                missing_sample=miss,
                extra_sample=extra,
            )
    return Comparison(True, "match (unordered)")


def compare_results(
    gold: tuple[Sequence[str], Sequence[Sequence]],
    got: tuple[Sequence[str], Sequence[Sequence]],
    *,
    ordered: bool,
    float_tol: float = DEFAULT_FLOAT_TOL,
) -> Comparison:
    """Compare two ``(columns, rows)`` result sets. ``ordered`` comes from the gold query."""
    g_cols, g_rows = list(gold[0]), list(gold[1])
    a_cols, a_rows = list(got[0]), list(got[1])

    if len(g_cols) != len(a_cols):
        return Comparison(
            False,
            f"column count differs: gold {len(g_cols)} {g_cols}, agent {len(a_cols)} {a_cols}",
            len(g_rows),
            len(a_rows),
        )

    identity = tuple(range(len(g_cols)))
    perms = [identity]
    if len(g_cols) <= _MAX_PERM_COLS:
        perms += [p for p in permutations(range(len(g_cols))) if p != identity]

    first_failure: Comparison | None = None
    for perm in perms:
        reordered = [tuple(r[i] for i in perm) for r in a_rows]
        cmp = _compare_rows(g_rows, reordered, ordered=ordered, tol=float_tol)
        cmp.gold_row_count, cmp.got_row_count = len(g_rows), len(a_rows)
        if cmp.equal:
            if perm != identity:
                cmp.column_perm = list(perm)
                cmp.reason += f"; agent columns needed reordering {list(perm)}"
            return cmp
        if first_failure is None:
            first_failure = cmp
    return first_failure  # type: ignore[return-value]


# --- self-test --------------------------------------------------------------------

if __name__ == "__main__":
    D = Decimal

    def ok(cmp, note):
        assert cmp.equal, f"expected EQUAL for {note}: {cmp.reason}"
        print(f"  equal    - {note}")

    def no(cmp, note):
        assert not cmp.equal, f"expected MISMATCH for {note}"
        print(f"  mismatch - {note}  ({cmp.reason})")

    print("infer_ordered:")
    assert infer_ordered("SELECT a FROM t ORDER BY a")
    assert not infer_ordered("SELECT a FROM t")
    assert not infer_ordered(
        "WITH x AS (SELECT a FROM t ORDER BY a) SELECT count(*) FROM x"
    ), "ORDER BY inside a CTE must not count"
    assert infer_ordered("WITH x AS (SELECT a FROM t) SELECT a FROM x ORDER BY a")
    print("  ok")

    print("compare_results:")
    ok(
        compare_results((["n"], [(D("3.3333333"),)]), (["n"], [(3.33333330001,)]), ordered=False),
        "numeric tolerance",
    )
    ok(
        compare_results(
            (["a", "b"], [(1, 2), (3, 4)]),
            (["b", "a"], [(2, 1), (4, 3)]),
            ordered=False,
        ),
        "same data, columns swapped",
    )
    ok(
        compare_results(
            (["a"], [(1,), (2,), (3,)]),
            (["a"], [(3,), (1,), (2,)]),
            ordered=False,
        ),
        "unordered: row order ignored",
    )
    no(
        compare_results(
            (["a"], [(1,), (2,), (3,)]),
            (["a"], [(3,), (1,), (2,)]),
            ordered=True,
        ),
        "ordered: row order enforced",
    )
    ok(
        compare_results(
            (["a"], [(1,), (1,), (2,)]),
            (["a"], [(1,), (2,), (1,)]),
            ordered=False,
        ),
        "unordered multiset: duplicates kept",
    )
    no(
        compare_results(
            (["a"], [(1,), (1,), (2,)]),
            (["a"], [(1,), (2,), (2,)]),
            ordered=False,
        ),
        "unordered multiset: wrong duplicate count",
    )
    no(
        compare_results((["a"], [(1,), (2,)]), (["a"], [(1,)]), ordered=False),
        "row count differs",
    )
    ok(
        compare_results((["d"], [(_dt.date(2024, 1, 1),)]), (["d"], [("2024-01-01",)]), ordered=False),
        "date vs iso string",
    )
    ok(
        compare_results((["x"], [(None,)]), (["x"], [(None,)]), ordered=False),
        "null equals null",
    )
    no(
        compare_results((["x"], [(0,)]), (["x"], [(None,)]), ordered=False),
        "zero is not null",
    )
    print("\nall self-tests passed")
