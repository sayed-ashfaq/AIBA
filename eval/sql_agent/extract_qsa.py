"""Pull (question, final_sql, answer) triples out of a captured+scored run.

    python extract_qsa.py --run runs/<run_dir>

question/agent_sql/reply already live in raw.json per question (run_eval.py's own
capture); this just joins in the verdict from scored.json (if present) and writes a
flat CSV + JSON next to the run's other artifacts — no new eval machinery.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    args = ap.parse_args()

    run_dir = Path(args.run)
    if not run_dir.is_absolute():
        run_dir = (config.HERE / run_dir).resolve()

    raw = json.loads((run_dir / "raw.json").read_text())
    verdicts: dict[str, dict] = {}
    scored_path = run_dir / "scored.json"
    if scored_path.exists():
        scored = json.loads(scored_path.read_text())
        verdicts = {row["id"]: row for row in scored["rows"]}

    out_rows = []
    for r in raw["results"]:
        v = verdicts.get(r["id"], {})
        # raw.json's agent_sql comes only from the agent restating SQL in its final reply;
        # scored.json's is the same string plus, when that came back empty, whatever
        # logparse recovered from the backend log (sql_source: "log") — prefer that.
        final_sql = v.get("agent_sql") or r.get("agent_sql") or ""
        out_rows.append(
            {
                "id": r["id"],
                "difficulty": r.get("difficulty", ""),
                "question": r.get("question", ""),
                "final_sql": final_sql,
                "sql_source": v.get("sql_source", ""),
                "answer": r.get("reply") or "",
                "verdict": v.get("verdict", ""),
                "reason": v.get("reason", ""),
                "http_status": r.get("http_status", ""),
            }
        )

    json_path = run_dir / "qsa.json"
    csv_path = run_dir / "qsa.csv"
    json_path.write_text(json.dumps(out_rows, indent=2))
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
