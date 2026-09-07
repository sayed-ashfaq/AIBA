"""The metrics ledger: one CSV row per scored eval run, aggregate numbers only.

`metrics.csv` (git-tracked, lives next to this package) is the running history of every
eval. `build_row` turns a scored run directory into one flat dict; `upsert` writes it,
replacing the row for the same run if it is re-scored; `print_table` renders the ledger
for the console with a delta line against the previous run.

No per-question data, no SQL - just what you compare across runs.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

# Single source of truth for the column set and its order. Add a column here and the file
# migrates on the next write (old rows get an empty value for it).
COLUMNS = [
    # identity / provenance
    "run_id", "scored_at", "started_at", "dataset", "schema_mode", "label",
    "git_rev", "golden_built_at", "column_match", "n",
    # headline
    "exec_accuracy", "exec_match", "valid_sql_rate",
    # verdict counts
    "n_exec_mismatch", "n_no_sql", "n_agent_sql_error", "n_transport_error", "n_no_gold",
    # by difficulty
    "L1_acc", "L2_acc", "L3_acc", "L4_acc", "L1_n", "L2_n", "L3_n", "L4_n",
    # latency
    "latency_mean_s", "latency_p50_s", "latency_p95_s", "latency_max_s",
    # delegation / re-activation
    "sqlagent_calls_mean", "sqlagent_calls_max", "sqlagent_q_gt1", "sqlagent_redundant_total",
    # routing of the final turn
    "routed_sql_agent", "routed_python_agent", "routed_visualizer", "routed_other",
    # manual failure tags from review.csv (";"-joined "tag:count"), empty until tagged
    "failure_tags",
]

_KEY = ("run_id", "column_match")  # a re-score with a different column_match is its own row


def _fail_tag_histogram(review_csv: Path) -> str:
    if not review_csv.exists():
        return ""
    counts: dict[str, int] = {}
    with review_csv.open() as f:
        for r in csv.DictReader(f):
            tag = (r.get("failure_tag") or "").strip()
            if tag:
                counts[tag] = counts.get(tag, 0) + 1
    return ";".join(f"{t}:{c}" for t, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def build_row(run_dir: str | Path, golden_dir: str | Path | None = None) -> dict:
    """Read one scored run directory into a single ledger row (all values as strings)."""
    run_dir = Path(run_dir)
    scored = json.loads((run_dir / "scored.json").read_text())
    meta, summary, rows = scored["meta"], scored["summary"], scored["rows"]

    bd = summary.get("by_difficulty", {})
    lat = summary.get("latency_s", {})
    sic = summary.get("sql_agent_invocations", {})
    bv = summary.get("by_verdict", {})

    routed = {"sql_agent": 0, "python_agent": 0, "visualizer": 0, "other": 0}
    for r in rows:
        key = r.get("routed_to")
        routed[key if key in routed else "other"] += 1

    golden_built = ""
    if golden_dir is not None:
        gp = Path(golden_dir) / f"{meta['dataset']}.results.json"
        if gp.exists():
            golden_built = json.loads(gp.read_text()).get("built_at", "")

    row = {
        "run_id": run_dir.name,
        "scored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "started_at": meta.get("started_at", ""),
        "dataset": meta.get("dataset", ""),
        "schema_mode": meta.get("schema_mode", ""),
        "label": meta.get("label", ""),
        "git_rev": meta.get("git_rev", ""),
        "golden_built_at": golden_built,
        "column_match": summary.get("column_match", ""),
        "n": summary.get("n", len(rows)),
        "exec_accuracy": summary.get("exec_accuracy", ""),
        "exec_match": bv.get("exec_match", ""),
        "valid_sql_rate": summary.get("valid_sql_rate", ""),
        "n_exec_mismatch": bv.get("exec_mismatch", 0),
        "n_no_sql": bv.get("no_sql", 0),
        "n_agent_sql_error": bv.get("agent_sql_error", 0),
        "n_transport_error": bv.get("transport_error", 0),
        "n_no_gold": bv.get("no_gold", 0),
        "latency_mean_s": lat.get("mean", ""),
        "latency_p50_s": lat.get("p50", ""),
        "latency_p95_s": lat.get("p95", ""),
        "latency_max_s": lat.get("max", ""),
        "sqlagent_calls_mean": sic.get("mean", ""),
        "sqlagent_calls_max": sic.get("max", ""),
        "sqlagent_q_gt1": sic.get("questions_gt_1", ""),
        "sqlagent_redundant_total": sic.get("total_redundant", ""),
        "routed_sql_agent": routed["sql_agent"],
        "routed_python_agent": routed["python_agent"],
        "routed_visualizer": routed["visualizer"],
        "routed_other": routed["other"],
        "failure_tags": _fail_tag_histogram(run_dir / "review.csv"),
    }
    for tier in ("L1", "L2", "L3", "L4"):
        row[f"{tier}_acc"] = bd.get(tier, {}).get("accuracy", "")
        row[f"{tier}_n"] = bd.get(tier, {}).get("n", "")
    return {c: row.get(c, "") for c in COLUMNS}


def upsert(csv_path: str | Path, row: dict) -> int:
    """Insert `row`, or replace the existing row with the same (run_id, column_match).
    Rewrites the whole file so a column added to COLUMNS migrates cleanly. Returns the
    1-based position of the row in the file."""
    csv_path = Path(csv_path)
    existing: list[dict] = []
    if csv_path.exists():
        with csv_path.open() as f:
            existing = list(csv.DictReader(f))

    row = {c: row.get(c, "") for c in COLUMNS}
    key = tuple(str(row[k]) for k in _KEY)
    out, replaced = [], False
    for r in existing:
        if tuple(str(r.get(k, "")) for k in _KEY) == key:
            out.append(row)
            replaced = True
        else:
            out.append({c: r.get(c, "") for c in COLUMNS})
    if not replaced:
        out.append(row)

    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(out)
    return next(i for i, r in enumerate(out, 1) if r is row)


# --- console rendering --------------------------------------------------------------------

_VIEW = [
    ("run_id", 42), ("schema_mode", 6), ("git", 9), ("n", 3),
    ("exec_accuracy", 9), ("valid_sql_rate", 6),
    ("L1_acc", 5), ("L2_acc", 5), ("L3_acc", 5), ("L4_acc", 5),
    ("lat_p95", 7), ("redund", 6),
]

# view column -> ledger column (when they differ)
_VIEW_SRC = {"git": "git_rev", "lat_p95": "latency_p95_s", "redund": "sqlagent_redundant_total"}


def _view_value(row: dict, view_col: str):
    v = row.get(_VIEW_SRC.get(view_col, view_col), "")
    if view_col == "git" and isinstance(v, str) and v:
        return v[:7] + ("*" if v.endswith("-dirty") else "")
    return v


def _f(v, width, *, num=False):
    s = "" if v in ("", None) else (f"{float(v):.3f}" if num and _isnum(v) else str(v))
    if len(s) > width:
        s = s[: width - 1] + "…"
    return s.ljust(width)


def _isnum(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _delta_line(prev: dict, cur: dict) -> str:
    def d(col, scale=1.0, sign=True):
        if not (_isnum(prev.get(col)) and _isnum(cur.get(col))):
            return "  -"
        x = (float(cur[col]) - float(prev[col])) * scale
        return f"{x:+.3f}" if sign else f"{x:.3f}"

    return (
        f"   Δ ({cur['schema_mode']} vs prev {prev['schema_mode']}):  "
        f"{d('exec_accuracy')} exec_acc   "
        f"L1 {d('L1_acc')}  L2 {d('L2_acc')}  L3 {d('L3_acc')}  L4 {d('L4_acc')}   "
        f"lat_p95 {d('latency_p95_s')}   redund {d('sqlagent_redundant_total')}"
    )


def print_table(csv_path: str | Path, dataset: str | None = None, last: int | None = None) -> None:
    csv_path = Path(csv_path)
    if not csv_path.exists():
        print(f"no ledger yet at {csv_path}")
        return
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    if dataset:
        rows = [r for r in rows if r.get("dataset") == dataset]
    rows.sort(key=lambda r: r.get("started_at") or r.get("scored_at") or "")
    if last:
        rows = rows[-last:]
    if not rows:
        print("no matching runs")
        return

    title = f"{dataset or 'all datasets'} — {len(rows)} run(s)"
    print(f"\n{title}\n")
    header = "  ".join(name.rjust(w) if name != "run_id" else name.ljust(w) for name, w in _VIEW)
    print(header)
    print("─" * len(header))
    for r in rows:
        cells = []
        for name, w in _VIEW:
            num = name.endswith("_acc") or name in ("exec_accuracy", "valid_sql_rate")
            cell = _f(_view_value(r, name), w, num=num)
            cells.append(cell if name == "run_id" else cell.rjust(w))
        print("  ".join(cells))
    if len(rows) >= 2:
        print(_delta_line(rows[-2], rows[-1]))
    print()
