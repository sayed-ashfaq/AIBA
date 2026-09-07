"""Step 4 - turn one or more scored runs into a report (markdown + csv).

    python report.py --runs runs/dvdrental__plain__...  runs/dvdrental__graph__...

With exactly two runs of the same dataset in different schema modes it also emits the
plain-vs-graph diff: which questions flipped verdict, and the deltas in latency and in
sql_agent delegation count.

Writes report__<stamp>.md and report__<stamp>.csv into eval/sql_agent/runs/.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402


def _resolve(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (config.HERE / path).resolve()


def _load_run(run_dir: Path) -> dict:
    scored = json.loads((run_dir / "scored.json").read_text())
    review: dict[str, dict] = {}
    csv_path = run_dir / "review.csv"
    if csv_path.exists():
        with csv_path.open() as f:
            review = {r["id"]: r for r in csv.DictReader(f)}
    for row in scored["rows"]:
        tag = review.get(str(row["id"]), {})
        row["failure_tag"] = tag.get("failure_tag", "")
        row["failure_note"] = tag.get("failure_note", "")
    scored["name"] = run_dir.name
    return scored


def _md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", nargs="+", required=True)
    args = ap.parse_args()

    runs = [_load_run(_resolve(r)) for r in args.runs]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines: list[str] = [f"# SQL agent eval - {stamp}", ""]

    # --- overview ---
    lines += ["## Overview", ""]
    ov_rows = []
    for run in runs:
        s, m = run["summary"], run["meta"]
        ov_rows.append([
            run["name"],
            m["schema_mode"],
            m.get("git_rev", "?"),
            s["n"],
            s["exec_accuracy"],
            s["valid_sql_rate"],
            s["latency_s"]["mean"],
            s["latency_s"]["p95"],
            s["sql_agent_invocations"]["mean"],
            s["sql_agent_invocations"]["questions_gt_1"],
        ])
    lines += [
        _md_table(
            ["run", "mode", "git", "n", "exec_acc", "valid_sql", "lat_mean", "lat_p95",
             "sqlagent_mean", "sqlagent>1"],
            ov_rows,
        ),
        "",
    ]

    # --- by difficulty ---
    lines += ["## Accuracy by difficulty", ""]
    diffs = sorted({d for run in runs for d in run["summary"]["by_difficulty"]})
    dg_rows = []
    for run in runs:
        bd = run["summary"]["by_difficulty"]
        dg_rows.append(
            [run["meta"]["schema_mode"]]
            + [f"{bd[d]['match']}/{bd[d]['n']} ({bd[d]['accuracy']})" if d in bd else "-" for d in diffs]
        )
    lines += [_md_table(["mode"] + diffs, dg_rows), ""]

    # --- verdict breakdown ---
    lines += ["## Verdicts", ""]
    all_verdicts = sorted({v for run in runs for v in run["summary"]["by_verdict"] if run["summary"]["by_verdict"][v]})
    vr_rows = [
        [run["meta"]["schema_mode"]] + [run["summary"]["by_verdict"].get(v, 0) for v in all_verdicts]
        for run in runs
    ]
    lines += [_md_table(["mode"] + all_verdicts, vr_rows), ""]

    # --- failure tags ---
    tag_hist: dict[str, int] = {}
    for run in runs:
        for row in run["rows"]:
            if row.get("failure_tag"):
                tag_hist[row["failure_tag"]] = tag_hist.get(row["failure_tag"], 0) + 1
    if tag_hist:
        lines += ["## Failure tags (from review.csv)", ""]
        lines += [_md_table(["tag", "count"], sorted(tag_hist.items(), key=lambda x: -x[1])), ""]
    else:
        lines += ["## Failure tags", "", "_none tagged yet - fill `failure_tag` in each run's review.csv_", ""]

    # --- plain vs graph diff ---
    if (
        len(runs) == 2
        and runs[0]["meta"]["dataset"] == runs[1]["meta"]["dataset"]
        and runs[0]["meta"]["schema_mode"] != runs[1]["meta"]["schema_mode"]
    ):
        a, b = runs
        lines += [f"## {a['meta']['schema_mode']} vs {b['meta']['schema_mode']}", ""]
        idx_a = {r["id"]: r for r in a["rows"]}
        idx_b = {r["id"]: r for r in b["rows"]}
        flips = []
        for _id in sorted(set(idx_a) & set(idx_b)):
            ra, rb = idx_a[_id], idx_b[_id]
            if ra["verdict"] != rb["verdict"]:
                flips.append([
                    _id, ra["difficulty"], ra["question"][:60],
                    f"{a['meta']['schema_mode']}: {ra['verdict']}",
                    f"{b['meta']['schema_mode']}: {rb['verdict']}",
                ])
        if flips:
            lines += ["### Verdict flips", "", _md_table(["id", "diff", "question", "", ""], flips), ""]
        else:
            lines += ["_no question changed verdict between modes_", ""]

        def _delta(field: str) -> float | None:
            pairs = [
                (idx_b[i].get(field), idx_a[i].get(field))
                for i in set(idx_a) & set(idx_b)
                if isinstance(idx_a[i].get(field), (int, float)) and isinstance(idx_b[i].get(field), (int, float))
            ]
            return round(sum(x - y for x, y in pairs) / len(pairs), 2) if pairs else None

        lines += [
            _md_table(
                ["metric", f"mean delta ({b['meta']['schema_mode']} - {a['meta']['schema_mode']})"],
                [
                    ["latency_s", _delta("elapsed_s")],
                    ["sql_agent_invocations", _delta("sql_agent_invocations")],
                ],
            ),
            "",
        ]

    # --- per-question appendix ---
    lines += ["## Per question", ""]
    by_id: dict[int, dict] = {}
    for run in runs:
        for row in run["rows"]:
            entry = by_id.setdefault(row["id"], {"question": row["question"], "difficulty": row["difficulty"]})
            entry[run["meta"]["schema_mode"]] = row
    modes = [run["meta"]["schema_mode"] for run in runs]
    pq_rows = []
    for _id in sorted(by_id):
        e = by_id[_id]
        cells = [_id, e["difficulty"], e["question"][:70]]
        for mode in modes:
            row = e.get(mode)
            if not row:
                cells += ["-", "-"]
            else:
                cells += [row["verdict"], f"{row['elapsed_s']}s x{row['sql_agent_invocations']}"]
        pq_rows.append(cells)
    headers = ["id", "diff", "question"] + [h for mode in modes for h in (f"{mode}", f"{mode} lat/sqlx")]
    lines += [_md_table(headers, pq_rows), ""]

    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    md_path = config.RUNS_DIR / f"report__{stamp}.md"
    md_path.write_text("\n".join(lines))

    csv_path = config.RUNS_DIR / f"report__{stamp}.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run", "mode", "git", "id", "difficulty", "verdict", "reason",
                    "elapsed_s", "sql_agent_invocations", "redundant", "failure_tag", "failure_note"])
        for run in runs:
            for row in run["rows"]:
                w.writerow([
                    run["name"], run["meta"]["schema_mode"], run["meta"].get("git_rev", "?"),
                    row["id"], row["difficulty"], row["verdict"], row["reason"],
                    row["elapsed_s"], row["sql_agent_invocations"], row["redundant_sql_agent_calls"],
                    row.get("failure_tag", ""), row.get("failure_note", ""),
                ])

    print("\n".join(lines))
    print(f"\nwrote {md_path}\nwrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
