"""Central knobs for the SQL-agent eval harness.

Everything that depends on *your machine* (URLs, DB passwords, the log-file path) is
resolved here and can be overridden with an environment variable, so the scripts
themselves stay clean.
"""

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

DATASETS_DIR = HERE / "datasets"
GOLDEN_DIR = HERE / "golden"
RUNS_DIR = HERE / "runs"

# --- the AIBA backend under test -----------------------------------------------------------------

BASE_URL = os.environ.get("AIBA_BASE_URL", "http://127.0.0.1:8010")

# A dedicated account used only for eval, kept separate from your dev login so its chat
# history and active connection never collide with yours. Created on first run if missing.
EVAL_EMAIL = os.environ.get("AIBA_EVAL_EMAIL", "sql-eval@aiba.dev")
EVAL_PASSWORD = os.environ.get("AIBA_EVAL_PASSWORD", "sqlEvalPassword123")
EVAL_FULL_NAME = "SQL Eval"

# The backend log file OperationLoggingMiddleware writes to (one line per tool call). The
# harness reads the byte-range written during each question to count sql_agent delegations
# and tool timings. Assumes the eval is the only traffic hitting the backend while it runs.
BACKEND_LOG = Path(
    os.environ.get("AIBA_BACKEND_LOG", str(REPO_ROOT / "backend" / "logs" / "aiba.log"))
)

# --- datasets ----------------------------------------------------------------------------------
#
# For each dataset:
#   yaml            - the golden question file you hand-write (see README "Building the ground truth")
#   aiba_connection - how the AIBA app should connect; used to create + activate the connection
#                     over the API before a run
#   dsn             - libpq DSN the harness uses to talk to the SAME database directly (psycopg2),
#                     to run reference SQL in build_golden.py and re-run agent SQL in score.py

DATASETS = {
    "dvdrental": {
        # the hand-written question file: JSON list or YAML with an `items:` list
        "path": DATASETS_DIR / "questions.json",
        # what an item without an explicit `ordered` field means. "auto" = infer from a
        # top-level ORDER BY. False here because the question set sorts every gold query
        # just for display; set `ordered: true` on the items where the ranking IS the answer.
        "ordered_default": False,
        # "exact"  = agent must return the same columns as the reference query.
        # "subset" = agent may also carry extra columns, or omit a reference column that
        #            was only a tie-breaker, as long as row count and values still match.
        #            Right default here since the golds carry helper columns (film_id, ...).
        "column_match": "subset",
        "aiba_connection": {
            "name": "dvdrental (eval)",
            "db_type": "postgres",
            "url": os.environ.get(
                "DVDRENTAL_URL", "postgresql://postgres:12345@localhost:5432/dvdrental"
            ),
        },
        "dsn": os.environ.get(
            "DVDRENTAL_DSN",
            "host=localhost port=5432 dbname=dvdrental user=postgres password=12345",
        ),
    },
}

# How long to wait on a single /chat call before giving up on that question (seconds).
CHAT_TIMEOUT = int(os.environ.get("AIBA_CHAT_TIMEOUT", "300"))

# Statement timeout for reference / agent SQL we run ourselves (milliseconds).
SQL_TIMEOUT_MS = int(os.environ.get("AIBA_EVAL_SQL_TIMEOUT_MS", "30000"))
