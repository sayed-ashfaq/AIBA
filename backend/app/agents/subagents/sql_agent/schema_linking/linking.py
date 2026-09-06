"""Question + SchemaGraph  ->  FocusedSchema  (the schema slice for one question).

Pipeline:

    1. extract_entities(question)   LLM: the schema-relevant "things" the question
                                    is about, as short noun phrases.
    2. match_anchors(...)           embed each phrase, take the top-k closest
                                    tables -> anchor tables.
    3. graph.connecting_tables(...) FK traversal: the anchors plus the bridge
                                    tables that join them.
    4. graph.fk_neighbours(...)     optional 1-hop "sphere": directly FK-related
                                    tables an embedding match would miss.
    5. FocusedSchema(...)           anchors + path tables + join conditions
                                    (graph.edges_within).

If nothing matches — a question the schema cannot answer — this returns an
*empty* FocusedSchema. Whether to then fall back to the full flat schema is the
caller's decision, not this module's.

Depends on embeddings + graph + models + the LLM. No database access.
"""

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.subagents.sql_agent.schema_linking import embeddings, graph, models
from app.core.llm import get_llm
from app.core.logging import get_logger, log_duration

logger = get_logger(__name__)

# Tuning knobs — all overridable per call so the eval harness can sweep them.
DEFAULT_TOP_K = 2            # max tables kept per extracted entity
DEFAULT_MIN_SCORE = 0.30     # cosine floor; below this an entity matched nothing useful
DEFAULT_SCORE_GAP = 0.05     # keep a 2nd match for an entity only if within this of its best —
                            # a distant weak match drags the join path across the whole schema
DEFAULT_NEIGHBOUR_HOPS = 0   # sphere expansion off by default — 1-hop explodes on hub tables;
                            # left as a lever for the eval harness, expands from anchors only
_HUB_DEGREE = 8             # an anchor with this many FK edges is a hub; don't sphere-expand it


_MAX_ENTITIES = 5

_ENTITY_PROMPT = """Extract the database entities this question is about — the \
things data is stored about (people, objects, transactions, business concepts), \
not the metrics, aggregates, or time ranges.

Reply with 1 to 5 short noun phrases, phrased the way a database table would be \
named (singular or plural nouns), as a comma-separated list on a single line and \
nothing else. For "average order value per supplier last month" reply exactly:
customer orders, suppliers

Never include "average", "last month", counts, or sums."""


def _parse_entities(text: str) -> list[str]:
    """Pull the noun phrases out of the model's reply. Tolerates a plain
    comma/newline list, a JSON array (`["a", "b"]`), or either wrapped in a code
    fence — gpt-oss on Groq drifts between these. Returns [] if nothing usable."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text[:4].lower() == "json":
            text = text[4:].strip()

    # a JSON array anywhere in the reply wins (that's the shape the model 400'd with
    # when it was being forced into a tool call)
    match = re.search(r"\[.*?\]", text, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                text = ", ".join(str(x) for x in data)
        except ValueError:
            pass

    out: list[str] = []
    for part in re.split(r"[\n,]+", text):
        phrase = part.strip().lstrip("-*•").strip().strip("\"'[]").strip()
        if phrase and len(phrase) <= 60 and phrase.lower() not in {"json", "entities"}:
            out.append(phrase)
    return out


def extract_entities(question: str) -> list[str]:
    """LLM: question -> up to 5 short noun phrases naming the entities involved.

    Plain text, not structured output: gpt-oss on Groq intermittently answers a
    forced tool call with bare content ("model did not call a tool" -> 400). Any
    LLM or parse failure returns [] — the caller then falls back to the full
    schema, which is always the safe default here.
    """
    try:
        llm = get_llm("main_agent")
        with log_duration("Extract entities"):
            reply = llm.invoke(
                [SystemMessage(content=_ENTITY_PROMPT), HumanMessage(content=question)]
            )
        entities = _parse_entities(reply.content)[:_MAX_ENTITIES]
    except Exception:
        logger.warning("entity extraction failed for %r — no anchors", question, exc_info=True)
        return []
    logger.info("entities: %s", entities)
    return entities


def _is_bridge_like(table: models.Table) -> bool:
    """A table that exists mainly to connect others — 2+ FKs and at most one
    column of its own. A poor *anchor* (it's a path, not a destination) though
    fine as a bridge, so we let connecting_tables surface it instead of seeding
    from it. e.g. film_actor(actor_id, film_id, last_update)."""
    if len(table.foreign_keys) < 2:
        return False
    fk_cols = {e.from_column for e in table.foreign_keys}
    own = [c for c in table.columns if not c.pk and c.name not in fk_cols]
    return len(own) <= 1


def match_anchors(
    sg: models.SchemaGraph,
    entities: list[str],
    *,
    top_k: int,
    min_score: float,
    score_gap: float = DEFAULT_SCORE_GAP,
) -> list[str]:
    """For each entity phrase: embed it, walk its ranked matches, and keep the
    best non-bridge table, plus up to `top_k`-1 more only while they stay within
    `score_gap` of that best. De-duplicated across entities, in match order.
    Falls back to the single overall-best match if the bridge filter empties it."""
    if not entities or sg.embeddings is None or sg.embeddings.size == 0:
        return []

    anchors: list[str] = []
    overall_best: tuple[float, str] | None = None

    for entity in entities:
        qvec = embeddings.embed_query(entity)
        best_score: float | None = None
        kept = 0
        for idx, score in embeddings.cosine_topk(qvec, sg.embeddings, k=top_k + 2):
            if score < min_score:
                break
            table = sg.table_names[idx]
            if overall_best is None or score > overall_best[0]:
                overall_best = (score, table)
            if _is_bridge_like(sg.tables[table]):
                continue
            if best_score is None:
                best_score = score
            elif score < best_score - score_gap or kept >= top_k:
                break
            kept += 1
            if table not in anchors:
                anchors.append(table)
            logger.info("anchor  %-42s <- %-24s (%.3f)", table, entity, score)

    return anchors or ([overall_best[1]] if overall_best else [])


def link(
    question: str,
    sg: models.SchemaGraph,
    *,
    entities: list[str] | None = None,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = DEFAULT_MIN_SCORE,
    neighbour_hops: int = DEFAULT_NEIGHBOUR_HOPS,
) -> models.FocusedSchema:
    """Run the full pipeline. Empty FocusedSchema when no anchor matches.

    Pass `entities` to skip the LLM extraction call — the extraction is the only
    non-deterministic step, so the eval harness (and any caller re-running link)
    injects a fixed list.
    """
    if entities is None:
        entities = extract_entities(question)
    anchors = match_anchors(sg, entities, top_k=top_k, min_score=min_score)
    if not anchors:
        logger.info("no anchors matched for %r — returning empty FocusedSchema", question)
        return models.FocusedSchema(question=question, anchor_tables=[], path_tables=[], edges=[])

    path = graph.connecting_tables(sg.graph, anchors)
    # An anchor that connecting_tables dropped (its component doesn't reach the
    # others) is still worth including — a lone table in the prompt beats a
    # missing one.
    path = list(dict.fromkeys([*anchors, *path]))

    if neighbour_hops > 0:
        # From anchors only, and never out of a hub — one hop off a 15-FK table
        # pulls in half the schema.
        seeds = [a for a in anchors if a in sg.graph and sg.graph.degree(a) < _HUB_DEGREE]
        sphere = graph.fk_neighbours(sg.graph, seeds, hops=neighbour_hops)
        path = list(dict.fromkeys([*path, *sorted(sphere)]))

    edges = graph.edges_within(sg.graph, path)
    logger.info(
        "focused %r: %d anchors -> %d tables, %d join edges",
        question, len(anchors), len(path), len(edges),
    )
    return models.FocusedSchema(
        question=question, anchor_tables=anchors, path_tables=path, edges=edges
    )
