"""Step 3 - score a captured run against the golden result sets.

Re-executes each agent SQL against the database and compares its result set to the gold
result set (order-insensitive unless the gold query is ordered; small numeric tolerance).
Writes ``scored.json`` and ``review.csv`` inside the run directory.

    python score.py --run runs/dvdrental__plain__20260907T101500Z

Cheap and repeatable: fix the golden set or lib/compare.py and re-run this without
touching the agent again. Your manual ``failure_tag`` / ``failure_note`` edits in
review.csv are preserved across re-scores (merged back by id).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from lib import compare, dbio  # noqa: E402

# Verdicts, worst-to-best. exec_mismatch and agent_sql_error are the ones to eyeball.
VERDICTS = [
    "transport_error",  # /chat itself failed or timed out
    "no_gold",          # question id has no entry in the golden file
    "no_sql",           # agent produced no SQL for a question that needs data
    "agent_sql_error",  # agent SQL does not execute
    "exec_mismatch",    # runs, but wrong result set
    "exec_match",       # runs, correct result set
]

# Fill these into review.csv (one per mismatch). Kept here so the vocabulary stays stable.
FAILURE_TAGS = [
    "wrong_table", "missing_join", "wrong_join_key", "missing_filter", "wrong_filter",
    "wrong_aggregation", "wrong_grouping", "hallucinated_column", "schema_linking_miss",
    "ambiguity_misread", "wrong_order_or_limit", "date_logic", "dedupe", "gold_wrong",
]


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, math.ceil(q / 100 * len(s)) - 1))
    return round(s[k], 2)


def _load_prior_review(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open() as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="path to a run directory produced by run_eval.py")
    args = ap.parse_args()

    run_dir = Path(args.run)
    if not run_dir.is_absolute():
        run_dir = (config.HERE / run_dir).resolve()
    meta = json.loads((run_dir / "meta.json").read_text())
    raw = json.loads((run_dir / "raw.json").read_text())["results"]

    dataset_name = meta["dataset"]
    golden_path = config.GOLDEN_DIR / f"{dataset_name}.results.json"
    if not golden_path.exists():
        raise SystemExit(f"missing {golden_path} - run build_golden.py --dataset {dataset_name}")
    golden = json.loads(golden_path.read_text())["items"]

    conn = dbio.connect(config.DATASETS[dataset_name]["dsn"])
    rows: list[dict] = []

    for r in raw:
        gid = str(r["id"])
        gold = golden.get(gid)
        log = r.get("log") or {}
        base = {
            "id": r["id"],
            "question": r["question"],
            "difficulty": r.get("difficulty") or (gold or {}).get("difficulty", "?"),
            "routed_to": r.get("routed_to"),
            "elapsed_s": r.get("elapsed_s"),
            "sql_agent_invocations": log.get("sql_agent_invocations", 0),
            "redundant_sql_agent_calls": log.get("redundant_sql_agent_calls", 0),
            "other_delegations": log.get("other_delegations", {}),
            "agent_reported_rows": r.get("agent_row_count"),
            "agent_sql": (r.get("agent_sql") or "").strip(),
        }

        if r.get("error") or r.get("http_status") not in (200, None):
            rows.append({**base, "verdict": "transport_error", "reason": r.get("error", "non-200"),
                         "gold_rows": None, "agent_reexec_rows": None, "column_perm": None})
            continue
        if gold is None:
            rows.append({**base, "verdict": "no_gold", "reason": "no golden entry for this id",
                         "gold_rows": None, "agent_reexec_rows": None, "column_perm": None})
            continue
        if not base["agent_sql"]:
            rows.append({**base, "verdict": "no_sql",
                         "reason": f"agent produced no SQL (routed_to={base['routed_to']})",
                         "gold_rows": gold["row_count"], "agent_reexec_rows": None, "column_perm": None})
            continue

        try:
            got_cols, got_rows = dbio.run_sql(conn, base["agent_sql"], timeout_ms=config.SQL_TIMEOUT_MS)
        except Exception as exc:
            rows.append({**base, "verdict": "agent_sql_error",
                         "reason": str(exc).strip().splitlines()[0],
                         "gold_rows": gold["row_count"], "agent_reexec_rows": None, "column_perm": None})
            continue

        cmp = compare.compare_results(
            (gold["columns"], gold["rows"]),
            (got_cols, got_rows),
            ordered=gold["ordered"],
            float_tol=gold["float_tol"],
        )
        rows.append({
            **base,
            "verdict": "exec_match" if cmp.equal else "exec_mismatch",
            "reason": cmp.reason,
            "gold_rows": cmp.gold_row_count,
            "agent_reexec_rows": cmp.got_row_count,
            "column_perm": cmp.column_perm,
            "missing_sample": cmp.missing_sample,
            "extra_sample": cmp.extra_sample,
        })

    conn.close()

    # --- summary ---
    n = len(rows)
    by_verdict = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    exec_match = by_verdict["exec_match"]
    invalid = by_verdict["no_sql"] + by_verdict["agent_sql_error"]
    latencies = [r["elapsed_s"] for r in rows if isinstance(r.get("elapsed_s"), (int, float))]
    invs = [r["sql_agent_invocations"] for r in rows]

    by_diff: dict[str, dict] = {}
    for r in rows:
        d = by_diff.setdefault(r["difficulty"], {"n": 0, "match": 0})
        d["n"] += 1
        d["match"] += r["verdict"] == "exec_match"
    for d in by_diff.values():
        d["accuracy"] = round(d["match"] / d["n"], 3) if d["n"] else None

    summary = {
        "n": n,
        "by_verdict": by_verdict,
        "exec_accuracy": round(exec_match / n, 3) if n else None,
        "valid_sql_rate": round((n - invalid) / n, 3) if n else None,
        "by_difficulty": by_diff,
        "latency_s": {
            "mean": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "p50": _pct(latencies, 50),
            "p95": _pct(latencies, 95),
            "max": max(latencies) if latencies else None,
        },
        "sql_agent_invocations": {
            "mean": round(sum(invs) / len(invs), 2) if invs else None,
            "max": max(invs) if invs else None,
            "questions_gt_1": sum(1 for x in invs if x > 1),
            "total_redundant": sum(r["redundant_sql_agent_calls"] for r in rows),
        },
    }

    (run_dir / "scored.json").write_text(
        json.dumps({"meta": meta, "summary": summary, "rows": rows}, indent=2, default=str)
    )

    # --- review.csv (merge prior manual tags back in) ---
    prior = _load_prior_review(run_dir / "review.csv")
    with (run_dir / "review.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "difficulty", "verdict", "reason", "failure_tag", "failure_note"])
        for r in rows:
            p = prior.get(str(r["id"]), {})
            w.writerow([
                r["id"], r["difficulty"], r["verdict"], r["reason"],
                p.get("failure_tag", ""), p.get("failure_note", ""),
            ])

    # --- console ---
    print(f"run: {run_dir.name}   mode={meta['schema_mode']}   git={meta.get('git_rev')}\n")
    print(f"  exec accuracy    {summary['exec_accuracy']}   ({exec_match}/{n})")
    print(f"  valid SQL rate   {summary['valid_sql_rate']}")
    print(f"  verdicts         " + ", ".join(f"{k}={v}" for k, v in by_verdict.items() if v))
    print(f"  latency (s)      mean {summary['latency_s']['mean']}  p50 {summary['latency_s']['p50']}  "
          f"p95 {summary['latency_s']['p95']}  max {summary['latency_s']['max']}")
    print(f"  sql_agent calls  mean {summary['sql_agent_invocations']['mean']}  "
          f"max {summary['sql_agent_invocations']['max']}  "
          f">1 on {summary['sql_agent_invocations']['questions_gt_1']} question(s)  "
          f"({summary['sql_agent_invocations']['total_redundant']} redundant total)")
    print("\n  by difficulty:")
    for d in sorted(by_diff):
        b = by_diff[d]
        print(f"    {d}  {b['match']:>2}/{b['n']:<2}  acc={b['accuracy']}")

    print("\n  per question:")
    for r in rows:
        flag = "" if r["verdict"] == "exec_match" else f"   <- {r['reason'][:90]}"
        print(f"    Q{r['id']:<3} {r['difficulty']:<3} {r['verdict']:<16} "
              f"{str(r['elapsed_s']):>6}s  sqlx{r['sql_agent_invocations']}{flag}")

    print(f"\nwrote {run_dir / 'scored.json'} and {run_dir / 'review.csv'}")
    print(f"tag the mismatches in review.csv (vocabulary: {', '.join(FAILURE_TAGS)})")
    print(f"then:  python report.py --runs {run_dir.relative_to(config.HERE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
