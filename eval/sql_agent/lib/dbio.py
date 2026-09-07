"""Run read-only SQL against a target database and hand back plain Python values.

Used in two places:
  - build_golden.py runs your reference queries to capture the expected result sets
  - score.py re-runs the agent's SQL so it can be compared to those result sets

The connection is opened READ ONLY (same posture as the app), so a stray write in a
reference query fails loudly instead of mutating your sample database.
"""

from __future__ import annotations

import contextlib

import psycopg2

from .compare import normalize_cell


def connect(dsn: str):
    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True, autocommit=False)
    return conn


@contextlib.contextmanager
def _cursor(conn, timeout_ms: int):
    cur = conn.cursor()
    try:
        cur.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
        yield cur
    finally:
        cur.close()
        conn.rollback()  # read-only session; just drop the transaction


def run_sql(conn, sql: str, timeout_ms: int = 30000) -> tuple[list[str], list[tuple]]:
    """Execute ``sql`` and return ``(columns, rows)`` with cells already normalised
    (Decimal -> float, date/time -> iso string, bytes -> hex). Raises ``psycopg2.Error``
    on any database error; the caller decides whether that's an agent bug or a gold bug.
    """
    with _cursor(conn, timeout_ms) as cur:
        cur.execute(sql)
        if cur.description is None:  # not a SELECT / returned no result set
            return [], []
        columns = [d[0] for d in cur.description]
        rows = [tuple(normalize_cell(c) for c in row) for row in cur.fetchall()]
        return columns, rows
