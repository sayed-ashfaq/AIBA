"""Step 2 - run the SQL agent over a dataset in ONE schema mode and capture everything.

This does not score. It drives the live AIBA backend once per question and records, per
question: the reply, the SQL the agent settled on, the rows it returned, wall-clock
latency, and a parse of the backend log slice for that question (how many times the
orchestrator delegated to sql_agent, per-tool timings, whether graph linking fired).

Run it once per mode:

    python run_eval.py --dataset dvdrental --schema-mode plain
    python run_eval.py --dataset dvdrental --schema-mode graph --label after-prompt-fix

Then score each run (step 3) and compare them (step 4).

Assumes the backend is up on config.BASE_URL and that nothing else is hitting it while
this runs (the log-slice parsing keys off byte offsets in a single shared log file).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from lib import dataset, logparse  # noqa: E402


# --- backend session helpers -------------------------------------------------------------------


def _api(session: requests.Session, method: str, path: str, **kw):
    resp = session.request(method, config.BASE_URL + path, timeout=kw.pop("timeout", 60), **kw)
    return resp


def login(session: requests.Session) -> None:
    # signup is idempotent for our purposes: created -> fine, already exists -> also fine
    r = _api(
        session,
        "POST",
        "/auth/signup",
        json={
            "email": config.EVAL_EMAIL,
            "password": config.EVAL_PASSWORD,
            "full_name": config.EVAL_FULL_NAME,
        },
    )
    if r.status_code not in (200, 201):
        print(f"  signup: {r.status_code} (assuming the eval account already exists)")

    r = _api(
        session,
        "POST",
        "/auth/login",
        json={"email": config.EVAL_EMAIL, "password": config.EVAL_PASSWORD},
    )
    if r.status_code != 200:
        raise SystemExit(
            f"login failed ({r.status_code}): {r.text}\n"
            f"Set AIBA_EVAL_EMAIL / AIBA_EVAL_PASSWORD or create the account manually."
        )


def ensure_connection(session: requests.Session, spec: dict) -> None:
    existing = _api(session, "GET", "/connections").json()
    match = next((c for c in existing if c["name"] == spec["name"]), None)
    if match is None:
        created = _api(
            session,
            "POST",
            "/connections",
            json={"name": spec["name"], "db_type": spec["db_type"], "url": spec["url"]},
        )
        if created.status_code != 200:
            raise SystemExit(f"could not save connection: {created.status_code} {created.text}")
        conn_id = created.json()["id"]
    else:
        conn_id = match["id"]

    activated = _api(session, "POST", f"/connections/{conn_id}/activate")
    if activated.status_code != 200:
        raise SystemExit(f"could not activate connection: {activated.status_code} {activated.text}")


def set_schema_mode(session: requests.Session, mode: str) -> None:
    r = _api(session, "PATCH", "/me/schema-mode", json={"schema_mode": mode})
    if r.status_code != 200:
        raise SystemExit(f"could not set schema_mode={mode}: {r.status_code} {r.text}")
    confirmed = _api(session, "GET", "/me").json().get("schema_mode")
    if confirmed != mode:
        raise SystemExit(f"schema_mode did not stick: asked {mode}, got {confirmed}")


# --- log slicing -------------------------------------------------------------------


def log_size() -> int:
    try:
        return config.BACKEND_LOG.stat().st_size
    except OSError:
        return 0


def read_log_since(offset: int) -> str:
    p = config.BACKEND_LOG
    if not p.exists():
        return ""
    with p.open("r", encoding="utf-8", errors="replace") as f:
        # if the file shrank, it rotated (midnight) mid-run - take it from the top
        f.seek(0 if p.stat().st_size < offset else offset)
        return f.read()


# --- chat call -------------------------------------------------------------------


def ask(session: requests.Session, question: str, timeout: int) -> dict:
    """One fresh-chat /chat call. Returns a dict with the fields we care about plus any error."""
    off = log_size()
    t0 = time.monotonic()
    try:
        resp = _api(session, "POST", "/chat", json={"message": question}, timeout=timeout)
    except requests.RequestException as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "elapsed_s": round(time.monotonic() - t0, 2)}
    elapsed = round(time.monotonic() - t0, 2)
    slice_text = read_log_since(off)

    out: dict = {
        "http_status": resp.status_code,
        "elapsed_s": elapsed,
        "log_slice": slice_text,
        "log": logparse.parse(slice_text).as_dict(),
    }
    try:
        body = resp.json()
    except ValueError:
        out["error"] = f"non-JSON response: {resp.text[:500]}"
        return out

    if resp.status_code != 200:
        out["error"] = f"HTTP {resp.status_code}: {json.dumps(body)[:500]}"
        return out

    data = body.get("data") or {}
    columns = data.get("columns") or []
    rows = data.get("rows") or []
    out.update(
        reply=body.get("reply"),
        routed_to=body.get("routed_to"),
        agent_sql=body.get("sql"),
        agent_columns=columns,
        agent_rows=[[row.get(c) for c in columns] for row in rows],
        agent_row_count=data.get("row_count"),
        agent_truncated=data.get("truncated"),
    )
    return out


# --- main -------------------------------------------------------------------


def git_rev() -> str:
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=config.REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=config.REPO_ROOT, capture_output=True, text=True
        ).stdout.strip()
        return rev + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, choices=sorted(config.DATASETS))
    ap.add_argument("--schema-mode", required=True, choices=["plain", "graph"])
    ap.add_argument("--label", default="", help="free-text tag appended to the run directory name")
    ap.add_argument("--ids", help="comma-separated subset of item ids to run")
    ap.add_argument("--timeout", type=int, default=config.CHAT_TIMEOUT)
    ap.add_argument("--sleep", type=float, default=0.0, help="pause between questions (rate limiting)")
    ap.add_argument(
        "--no-warmup",
        action="store_true",
        help="skip the throwaway first call that pays graph-build cost outside the timed loop",
    )
    args = ap.parse_args()

    cfg = config.DATASETS[args.dataset]
    items = dataset.load(cfg["yaml"])
    if args.ids:
        wanted = {int(x) for x in args.ids.split(",")}
        items = [it for it in items if it.id in wanted]
    if not items:
        raise SystemExit(f"no questions to run in {cfg['yaml']}")

    golden = config.GOLDEN_DIR / f"{args.dataset}.results.json"
    if not golden.exists():
        print(f"note: {golden} not found - you can still run, but score.py needs it. "
              f"Run build_golden.py first.\n")

    session = requests.Session()
    print(f"logging in as {config.EVAL_EMAIL} ...")
    login(session)
    print(f"activating connection '{cfg['aiba_connection']['name']}' ...")
    ensure_connection(session, cfg["aiba_connection"])
    print(f"setting schema_mode={args.schema_mode} ...")
    set_schema_mode(session, args.schema_mode)

    if args.schema_mode == "graph" and not args.no_warmup:
        print("warmup call (builds the schema-linking graph, first time only) ...")
        w = ask(session, "How many tables are in the database?", args.timeout)
        print(f"  warmup done in {w.get('elapsed_s')}s\n")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{args.dataset}__{args.schema_mode}__{stamp}" + (f"__{args.label}" if args.label else "")
    run_dir = config.RUNS_DIR / name
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)

    meta = {
        "dataset": args.dataset,
        "schema_mode": args.schema_mode,
        "label": args.label,
        "base_url": config.BASE_URL,
        "git_rev": git_rev(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "question_ids": [it.id for it in items],
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    results: list[dict] = []
    raw_path = run_dir / "raw.json"

    for it in items:
        res = ask(session, it.question, args.timeout)

        # the raw log slice goes to its own file; keep raw.json lean
        (run_dir / "logs" / f"q{it.id}.log").write_text(res.pop("log_slice", "") or "")

        row = {"id": it.id, "question": it.question, "difficulty": it.difficulty, **res}
        results.append(row)
        (raw_path).write_text(json.dumps({"meta": meta, "results": results}, indent=2, default=str))

        log = res.get("log", {})
        status = res.get("http_status", "ERR")
        print(
            f"Q{it.id:<3} {args.schema_mode:<5} HTTP{status} {res.get('elapsed_s'):>6}s  "
            f"routed={str(res.get('routed_to')):<12} "
            f"sql_agent x{log.get('sql_agent_invocations', 0)} "
            f"rows={res.get('agent_row_count')}"
            + (f"  ERROR {res['error'][:80]}" if res.get("error") else "")
        )
        if args.sleep:
            time.sleep(args.sleep)

    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    (raw_path).write_text(json.dumps({"meta": meta, "results": results}, indent=2, default=str))

    print(f"\nwrote {raw_path}")
    print(f"next:  python score.py --run {run_dir.relative_to(config.HERE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
