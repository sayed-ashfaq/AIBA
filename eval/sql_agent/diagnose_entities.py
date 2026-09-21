"""Standalone entity-extraction stability check — isolates extract_entities() from the rest
of the schema-linking pipeline (no embeddings, no graph, no agent, no DB connection needed).

Calls linking.extract_entities() N times per golden question at a given temperature and
reports how often the result is stable, empty, or looks like a refusal — directly measuring
whether the "wrong anchors" failures traced back to entity extraction are a temperature/
non-determinism problem, independent of the embedding model or table descriptions.

Must run with the backend's venv AND its working directory (Settings() resolves ".env"
relative to cwd):

    cd backend && .venv/bin/python ../eval/sql_agent/diagnose_entities.py
    cd backend && .venv/bin/python ../eval/sql_agent/diagnose_entities.py --temperature 0
    cd backend && .venv/bin/python ../eval/sql_agent/diagnose_entities.py --reps 8 --ids L1-006,L2-002
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "eval" / "sql_agent"))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from lib import dataset  # noqa: E402

from app.agents.subagents.sql_agent.schema_linking import linking  # noqa: E402
from app.core.llm import get_llm  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

_REFUSAL_MARKERS = ("sorry", "can't help", "cannot help", "i can't", "as an ai")


def _looks_like_refusal(entities: list[str]) -> bool:
    joined = " ".join(entities).lower()
    return any(m in joined for m in _REFUSAL_MARKERS)


def extract_entities_at(question: str, temperature: float | None) -> list[str]:
    """Same call linking.extract_entities() makes, with temperature overridable so this
    script can compare settings without editing the source file back and forth."""
    kwargs = {} if temperature is None else {"temperature": temperature}
    llm = get_llm("main_agent", **kwargs)
    reply = llm.invoke([SystemMessage(content=linking._ENTITY_PROMPT), HumanMessage(content=question)])
    return linking._parse_entities(reply.content)[: linking._MAX_ENTITIES]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--dataset-file",
        default=str(REPO_ROOT / "eval" / "sql_agent" / "datasets" / "questions.json"),
        help="dvdrental.yaml is an empty template — questions.json is the actual 40-item golden set",
    )
    ap.add_argument("--reps", type=int, default=5, help="extract_entities calls per question")
    ap.add_argument("--temperature", type=float, default=None, help="omit for provider default")
    ap.add_argument("--ids", default=None, help="comma-separated subset, e.g. L1-006,L2-002")
    args = ap.parse_args()

    items = dataset.load(args.dataset_file)
    if args.ids:
        wanted = set(args.ids.split(","))
        items = [i for i in items if str(i.id) in wanted]

    label = "default (unset)" if args.temperature is None else str(args.temperature)
    print(f"# entity extraction stability — temperature={label}, reps={args.reps}, n={len(items)} questions\n")

    unstable = 0
    any_empty = 0
    any_refusal = 0
    per_question_rows = []

    for item in items:
        runs: list[tuple[str, ...]] = []
        flags = set()
        for _ in range(args.reps):
            try:
                ents = extract_entities_at(item.question, args.temperature)
            except Exception as exc:  # noqa: BLE001
                ents = [f"<error: {exc}>"]
            runs.append(tuple(ents))
            if not ents:
                flags.add("empty")
            if _looks_like_refusal(ents):
                flags.add("refusal")

        counts = Counter(runs)
        distinct = len(counts)
        is_unstable = distinct > 1
        unstable += is_unstable
        any_empty += "empty" in flags
        any_refusal += "refusal" in flags

        marker = "UNSTABLE" if is_unstable else "stable  "
        flag_str = f" [{', '.join(sorted(flags))}]" if flags else ""
        print(f"{marker}  {item.id:<8} {item.question}{flag_str}")
        for run, count in counts.most_common():
            print(f"          x{count}  {list(run)}")
        per_question_rows.append((item.id, distinct, flags))

    n = len(items)
    print("\n# summary")
    print(f"  unstable across reps : {unstable}/{n}")
    print(f"  hit empty at least once : {any_empty}/{n}")
    print(f"  hit refusal-like output : {any_refusal}/{n}")


if __name__ == "__main__":
    main()
