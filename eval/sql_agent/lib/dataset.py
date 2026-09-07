"""Load and validate a golden-question YAML file.

Schema of one item (see datasets/dvdrental.yaml and the README for the prose version):

    id:            unique int, never reused
    question:      natural-language question, verbatim as a user would type it
    gold_sql:      your verified reference query (Postgres dialect)
    difficulty:    L1 | L2 | L3 | L4
    tags:          optional list[str]
    notes:         optional str  (pinned interpretation, known fan-out risk, why empty, ...)
    expects_chart: optional bool
    ordered:       optional; "auto" (default) infers from a top-level ORDER BY, or true/false
    float_tol:     optional float; absolute numeric tolerance (default 1e-6)
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml

from .compare import DEFAULT_FLOAT_TOL, infer_ordered

_DIFFICULTIES = ("L1", "L2", "L3", "L4")


@dataclasses.dataclass
class GoldItem:
    id: int
    question: str
    gold_sql: str
    difficulty: str
    tags: list
    notes: str
    expects_chart: bool
    ordered: bool
    ordered_source: str  # "explicit" or "inferred"
    float_tol: float


def load(path: str | Path) -> list[GoldItem]:
    doc = yaml.safe_load(Path(path).read_text()) or {}
    raw = doc.get("items") or []
    if not isinstance(raw, list):
        raise ValueError("top-level 'items' must be a list")

    items: list[GoldItem] = []
    seen_ids: set = set()
    seen_questions: dict = {}

    for idx, r in enumerate(raw, start=1):
        tag = f"item #{idx}"
        if not isinstance(r, dict):
            raise ValueError(f"{tag}: must be a mapping")
        if "id" in r:
            tag = f"item #{idx} (id={r['id']})"

        for req in ("id", "question", "gold_sql", "difficulty"):
            if r.get(req) in (None, ""):
                raise ValueError(f"{tag}: missing required field '{req}'")

        if r["id"] in seen_ids:
            raise ValueError(f"{tag}: duplicate id")
        seen_ids.add(r["id"])

        if r["difficulty"] not in _DIFFICULTIES:
            raise ValueError(f"{tag}: difficulty must be one of {list(_DIFFICULTIES)}")

        q_norm = " ".join(r["question"].lower().split())
        if q_norm in seen_questions:
            raise ValueError(
                f"{tag}: question is a near-duplicate of id={seen_questions[q_norm]}"
            )
        seen_questions[q_norm] = r["id"]

        raw_ordered = r.get("ordered", "auto")
        if raw_ordered in (None, "auto"):
            ordered, source = infer_ordered(r["gold_sql"]), "inferred"
        else:
            ordered, source = bool(raw_ordered), "explicit"

        items.append(
            GoldItem(
                id=r["id"],
                question=r["question"].strip(),
                gold_sql=r["gold_sql"].strip(),
                difficulty=r["difficulty"],
                tags=list(r.get("tags") or []),
                notes=(r.get("notes") or "").strip(),
                expects_chart=bool(r.get("expects_chart", False)),
                ordered=ordered,
                ordered_source=source,
                float_tol=float(r.get("float_tol", DEFAULT_FLOAT_TOL)),
            )
        )

    return items
