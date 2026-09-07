"""Read or update the eval metrics ledger (eval/sql_agent/metrics.csv).

    python ledger.py                          # whole ledger as a table
    python ledger.py --dataset dvdrental      # filter, with a delta vs the previous run
    python ledger.py --last 6                 # only the 6 most recent runs
    python ledger.py --record runs/<dir> ...  # (re)build rows without re-scoring
                                              #   use after tagging review.csv, or to backfill

score.py appends a row automatically after every scoring pass; this is just for viewing
and for rebuilding rows outside of a score run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from lib import ledger  # noqa: E402

LEDGER = config.HERE / "metrics.csv"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", help="only show rows for this dataset")
    ap.add_argument("--last", type=int, help="only show the N most recent runs")
    ap.add_argument("--record", nargs="+", metavar="RUN_DIR",
                    help="(re)build ledger rows from these scored run directories")
    ap.add_argument("--csv", default=str(LEDGER), help=f"ledger path (default {LEDGER})")
    args = ap.parse_args()

    csv_path = Path(args.csv)

    if args.record:
        for rd in args.record:
            run_dir = Path(rd)
            if not run_dir.is_absolute():
                run_dir = (config.HERE / run_dir).resolve()
            if not (run_dir / "scored.json").exists():
                print(f"skip {run_dir.name}: no scored.json (run score.py first)")
                continue
            pos = ledger.upsert(csv_path, ledger.build_row(run_dir, config.GOLDEN_DIR))
            print(f"recorded {run_dir.name} -> {csv_path.name} (row {pos})")
        print()

    ledger.print_table(csv_path, dataset=args.dataset, last=args.last)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
