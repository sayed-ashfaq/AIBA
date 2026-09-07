"""Step 1 - build and sanity-check the golden result sets.

Runs every item's ``gold_sql`` against the real database, saves the result set as the
canonical expected answer (``golden/<dataset>.results.json``), and prints a verification
report so a corrupted "gold" is caught here instead of silently failing every agent run.

    python build_golden.py --dataset dvdrental
    python build_golden.py --dataset dvdrental --check-only    # verify, don't write
    python build_golden.py --dataset dvdrental --ids 3,4,5     # only these

Exit code is non-zero if any item is flagged FAIL (SQL error), so it is safe to gate CI on.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from lib import dataset, dbio  # noqa: E402

LEVEL_ORDER = {"FAIL": 0, "WARN": 1, "INFO": 2}


def _fingerprint(dsn: str) -> str:
    parts = dict(p.split("=", 1) for p in dsn.split() if "=" in p)
    return f"{parts.get('dbname', '?')}@{parts.get('host', '?')}:{parts.get('port', '?')}"


def _heuristics(item, columns, rows, error) -> list[tuple[str, str]]:
    """Cheap checks that a gold query is probably wrong. (level, message) pairs."""
    if error:
        return [("FAIL", f"gold_sql raised: {error}")]

    low = " ".join(item.gold_sql.lower().split())
    n = len(rows)
    flags: list[tuple[str, str]] = []

    if n == 0:
        # a documented empty result (notes/assumption explains it) is a known quantity, not a smell
        lvl = "INFO" if item.notes else "WARN"
        flags.append((lvl, "0 rows returned - confirm that is expected, else the SQL is wrong"))
    if n == 1 and len(columns) == 1:
        flags.append(("INFO", f"scalar result = {rows[0][0]!r}"))
    if n > 1000:
        flags.append(("WARN", f"{n} rows - large gold set; a narrower question compares more meaningfully"))
    if "limit" in low and not item.ordered:
        flags.append(("WARN", "LIMIT without a total-order ORDER BY -> 'top N' is non-deterministic"))
    if "order by" in low and item.ordered and "limit" in low:
        # ordered + limited: make sure the ORDER BY breaks ties, else two rows can swap
        flags.append(("INFO", "ordered + LIMIT: make sure ORDER BY has a unique tie-breaker column"))
    if "select *" in low:
        flags.append(("WARN", "SELECT * in gold -> column drift will break comparison; list columns"))
    if len(set(columns)) != len(columns):
        flags.append(("WARN", f"duplicate column names {columns} - comparison pairs columns by position"))
    if item.expects_chart and len(columns) < 2:
        flags.append(("INFO", "expects_chart but gold has <2 columns - a chart needs a label + a measure"))
    if item.ordered_source == "inferred" and item.ordered:
        flags.append(("INFO", "row order will be enforced (inferred from a top-level ORDER BY)"))

    return flags


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, choices=sorted(config.DATASETS))
    ap.add_argument("--check-only", action="store_true", help="run the checks but do not write the results file")
    ap.add_argument("--ids", help="comma-separated subset of item ids")
    args = ap.parse_args()

    cfg = config.DATASETS[args.dataset]
    items = dataset.load(cfg["path"], cfg.get("ordered_default", "auto"))
    if args.ids:
        wanted = {x.strip() for x in args.ids.split(",")}
        items = [it for it in items if str(it.id) in wanted]

    if not items:
        print(f"No questions in {cfg['path']}.")
        print("Add some (see the README, 'Building the ground truth'), then re-run.")
        return 0

    conn = dbio.connect(cfg["dsn"])
    fingerprint = _fingerprint(cfg["dsn"])
    print(f"dataset={args.dataset}  db={fingerprint}  items={len(items)}\n")

    results: dict[str, dict] = {}
    report: list[tuple[int, str, int, str, list[tuple[str, str]]]] = []
    any_fail = False

    for it in items:
        error = None
        columns: list[str] = []
        rows: list[tuple] = []
        t0 = time.monotonic()
        try:
            columns, rows = dbio.run_sql(conn, it.gold_sql, timeout_ms=config.SQL_TIMEOUT_MS)
        except Exception as exc:  # psycopg2.Error and friends
            error = str(exc).strip().splitlines()[0]
        elapsed = round(time.monotonic() - t0, 3)

        flags = _heuristics(it, columns, rows, error)
        worst = min((LEVEL_ORDER[l] for l, _ in flags), default=LEVEL_ORDER["INFO"])
        status = {0: "FAIL", 1: "WARN", 2: "OK"}[worst]
        any_fail = any_fail or status == "FAIL"
        report.append((it.id, it.difficulty, len(rows), status, flags))

        if not error:
            results[str(it.id)] = {
                "question": it.question,
                "gold_sql": it.gold_sql,
                "difficulty": it.difficulty,
                "tags": it.tags,
                "notes": it.notes,
                "ordered": it.ordered,
                "ordered_source": it.ordered_source,
                "float_tol": it.float_tol,
                "expects_chart": it.expects_chart,
                "columns": columns,
                "rows": [list(r) for r in rows],
                "row_count": len(rows),
                "elapsed_s": elapsed,
            }

    conn.close()

    # --- report ---
    print(f"{'id':>4}  {'diff':<4}  {'rows':>6}  {'status':<6}  notes")
    print("-" * 72)
    for _id, diff, nrows, status, flags in report:
        head = f"{_id:>4}  {diff:<4}  {nrows:>6}  {status:<6}  "
        if not flags:
            print(head + "-")
        else:
            for i, (lvl, msg) in enumerate(sorted(flags, key=lambda f: LEVEL_ORDER[f[0]])):
                print((head if i == 0 else " " * len(head)) + f"[{lvl}] {msg}")

    fails = sum(1 for *_x, s, _f in report if s == "FAIL")
    warns = sum(1 for *_x, s, _f in report if s == "WARN")
    print("-" * 72)
    print(f"{len(report)} items - {fails} FAIL, {warns} WARN")

    if args.check_only:
        print("\n--check-only: results file not written")
        return 1 if any_fail else 0

    if any_fail:
        print("\nNot writing results file while items are FAIL. Fix the SQL and re-run.")
        return 1

    config.GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.GOLDEN_DIR / f"{args.dataset}.results.json"
    out_path.write_text(
        json.dumps(
            {
                "dataset": args.dataset,
                "db": fingerprint,
                "built_at": datetime.now(timezone.utc).isoformat(),
                "item_count": len(results),
                "items": results,
            },
            indent=2,
            default=str,
        )
    )
    print(f"\nwrote {out_path}  ({len(results)} items)")
    print("commit this file - a diff in the numbers is how you notice a gold query changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
