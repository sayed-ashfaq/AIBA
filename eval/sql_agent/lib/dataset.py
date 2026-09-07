"""Load and validate a golden-question file (``.json`` or ``.yaml``).

Accepted shapes:
  - JSON: a top-level list of item objects
  - YAML: a mapping with an ``items:`` list (see datasets/dvdrental.yaml)

Fields per item (aliases accepted, so an existing question file drops straight in):

    id                         unique, never reused        (int or str, e.g. "L1-001")
    question                   NL question, verbatim
    gold_sql   | correct_supposed_sql | expected_sql       verified reference query (Postgres)
    difficulty | level         L1 | L2 | L3 | L4
    tags                       optional list[str]
    notes      | assumption    optional str (pinned interpretation, fan-out risk, why empty)
    expects_chart              optional bool
    ordered                    optional; "auto" (default) infers from a top-level ORDER BY,
                               or true/false to force it
    float_tol                  optional float; absolute numeric tolerance (default 1e-6)
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import yaml

from .compare import DEFAULT_FLOAT_TOL, infer_ordered

_DIFFICULTIES = ("L1", "L2", "L3", "L4")

# field name -> the aliases that may appear instead of it
_ALIASES = {
    "gold_sql": ("gold_sql", "correct_supposed_sql", "expected_sql", "sql"),
    "difficulty": ("difficulty", "level"),
    "notes": ("notes", "assumption"),
}


def _pick(record: dict, canonical: str):
    for key in _ALIASES.get(canonical, (canonical,)):
        if record.get(key) not in (None, ""):
            return record[key]
    return None


@dataclasses.dataclass
class GoldItem:
    id: object  # int or str; used verbatim as the golden-results key
    question: str
    gold_sql: str
    difficulty: str
    tags: list
    notes: str
    expects_chart: bool
    ordered: bool
    ordered_source: str  # "explicit" or "inferred"
    float_tol: float


def _read_raw(path: Path) -> list:
    text = path.read_text()
    if path.suffix.lower() == ".json":
        doc = json.loads(text)
    else:
        doc = yaml.safe_load(text) or {}
    raw = doc if isinstance(doc, list) else (doc.get("items") or [])
    if not isinstance(raw, list):
        raise ValueError(f"{path.name}: expected a JSON list or a YAML mapping with 'items:'")
    return raw


def load(path: str | Path, ordered_default: object = "auto") -> list[GoldItem]:
    """``ordered_default`` sets what an item with no explicit ``ordered`` field means:
    ``"auto"`` infers it from a top-level ORDER BY (Spider-style), ``True``/``False`` forces
    it. Use ``False`` for a question set that sorts every gold query only for display."""
    raw = _read_raw(Path(path))

    items: list[GoldItem] = []
    seen_ids: set = set()
    seen_questions: dict = {}

    for idx, r in enumerate(raw, start=1):
        tag = f"item #{idx}"
        if not isinstance(r, dict):
            raise ValueError(f"{tag}: must be a mapping")

        fields = {
            "id": r.get("id"),
            "question": r.get("question"),
            "gold_sql": _pick(r, "gold_sql"),
            "difficulty": _pick(r, "difficulty"),
        }
        if fields["id"] not in (None, ""):
            tag = f"item #{idx} (id={fields['id']})"
        for name, value in fields.items():
            if value in (None, ""):
                raise ValueError(f"{tag}: missing required field '{name}'")

        if fields["id"] in seen_ids:
            raise ValueError(f"{tag}: duplicate id")
        seen_ids.add(fields["id"])

        if fields["difficulty"] not in _DIFFICULTIES:
            raise ValueError(f"{tag}: difficulty must be one of {list(_DIFFICULTIES)}")

        q_norm = " ".join(fields["question"].lower().split())
        if q_norm in seen_questions:
            raise ValueError(
                f"{tag}: question is a near-duplicate of id={seen_questions[q_norm]}"
            )
        seen_questions[q_norm] = fields["id"]

        raw_ordered = r.get("ordered", "inherit")
        if raw_ordered == "inherit":
            raw_ordered = ordered_default
        if raw_ordered in (None, "auto"):
            ordered, source = infer_ordered(fields["gold_sql"]), "inferred"
        else:
            ordered, source = bool(raw_ordered), "explicit"

        items.append(
            GoldItem(
                id=fields["id"],
                question=fields["question"].strip(),
                gold_sql=fields["gold_sql"].strip(),
                difficulty=fields["difficulty"],
                tags=list(r.get("tags") or []),
                notes=(_pick(r, "notes") or "").strip(),
                expects_chart=bool(r.get("expects_chart", False)),
                ordered=ordered,
                ordered_source=source,
                float_tol=float(r.get("float_tol", DEFAULT_FLOAT_TOL)),
            )
        )

    return items
