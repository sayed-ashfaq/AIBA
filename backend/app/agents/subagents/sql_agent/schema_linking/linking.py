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

A miss on the first pass gets two deterministic retries with progressively looser matching
(see link()) before giving up — a wrong phrasing is far more common than a genuinely
unanswerable question, and asking the ReAct agent to notice an empty slice and retry itself
proved unreliable in practice.

If nothing matches even after retrying — a question the schema genuinely cannot answer — this
returns an *empty* FocusedSchema. Whether to then fall back to the full flat schema is the
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

# a second matching pass on the WHOLE question text, unioned with the entity-phrase anchors.
# entity extraction can reduce a question to words the schema doesn't use ("guests" when the
# tables say "passengers"); embedding the raw question is the recall backstop for that.
DEFAULT_QUESTION_TOP_K = 2
DEFAULT_QUESTION_MIN_SCORE = 0.25   # a full sentence dilutes cosine — a lower floor than the phrase pass

# Retry pass, only when the first (strict) pass matches nothing. Loosened enough to catch a real
# near-miss, not loosened so far that an unrelated table gets waved in — this still requires *some*
# cosine similarity, just less of it, and connecting_tables still has to find a real FK path.
RETRY_MIN_SCORE = 0.18
RETRY_TOP_K = 3
RETRY_QUESTION_TOP_K = 3
RETRY_QUESTION_MIN_SCORE = 0.15

_MAX_ENTITIES = 5

_ENTITY_PROMPT = """Extract the database entities this question is about — the \
things data is stored about (people, objects, transactions, business concepts), \
not the metrics, aggregates, or time ranges.

Reply with 1 to 5 short noun phrases, phrased the way a database table would be \
named (singular or plural nouns), as a comma-separated list on a single line and \
nothing else. For "average order value per supplier last month" reply exactly:
customer orders, suppliers

Never include "average", "last month", counts, or sums."""

# Second-chance extraction, used only when the strict AND relaxed passes both matched nothing. A
# compound phrase ("customer segments", "at-risk customers") embeds further from a table's name
# than the bare noun inside it does — this asks for that bare noun instead, dropping every
# qualifier, so the retry has the plainest possible term to match against.
_BROAD_ENTITY_PROMPT = """Extract the database entities this question is about, but reduce each \
one to the SIMPLEST, most generic noun a database table would actually be named after — drop any \
qualifier, adjective, or business label in front of it.

"customer segments" -> customers
"at-risk customers" -> customers
"expensive movies" -> movies
"underperforming products" -> products
"most valuable clients" -> clients

Reply with 1 to 5 bare nouns (singular or plural), comma-separated, on a single line, nothing else."""


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


def extract_entities(question: str, *, broad: bool = False) -> list[str]:
    """LLM: question -> up to 5 short noun phrases naming the entities involved.

    `broad=True` swaps in _BROAD_ENTITY_PROMPT, which asks for the bare noun inside each
    phrase instead of the phrase itself — the link() retry's last resort when the normal
    phrasing didn't match anything.

    Plain text, not structured output: gpt-oss on Groq intermittently answers a
    forced tool call with bare content ("model did not call a tool" -> 400). Any
    LLM or parse failure returns [] — the caller then falls back to the full
    schema, which is always the safe default here.
    """
    prompt = _BROAD_ENTITY_PROMPT if broad else _ENTITY_PROMPT
    try:
        llm = get_llm("main_agent")
        with log_duration("Extract entities" + (" (broad)" if broad else "")):
            reply = llm.invoke([SystemMessage(content=prompt), HumanMessage(content=question)])
        entities = _parse_entities(reply.content)[:_MAX_ENTITIES]
    except Exception:
        logger.warning("entity extraction failed for %r — no anchors", question, exc_info=True)
        return []
    logger.info("entities%s: %s", " (broad)" if broad else "", entities)
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


def question_anchors(
    sg: models.SchemaGraph, question: str, *, top_k: int, min_score: float
) -> list[str]:
    """Top non-bridge tables from embedding the whole question — the recall backstop
    for when entity extraction dropped the words that actually match the schema."""
    if sg.embeddings is None or sg.embeddings.size == 0 or not question.strip():
        return []
    qvec = embeddings.embed_query(question)
    out: list[str] = []
    for idx, score in embeddings.cosine_topk(qvec, sg.embeddings, k=top_k + 2):
        if score < min_score:
            break
        table = sg.table_names[idx]
        if _is_bridge_like(sg.tables[table]):
            continue
        out.append(table)
        logger.info("anchor  %-42s <- %-24s (%.3f)", table, "[question]", score)
        if len(out) >= top_k:
            break
    return out


def _link_once(
    question: str,
    sg: models.SchemaGraph,
    entities: list[str],
    *,
    top_k: int,
    min_score: float,
    neighbour_hops: int,
    question_top_k: int,
    question_min_score: float,
) -> models.FocusedSchema:
    """One pass of anchor-matching + graph traversal for a fixed entity list and thresholds.
    Empty FocusedSchema when no anchor matches at these thresholds — link() decides whether
    that's worth retrying looser."""
    anchors = match_anchors(sg, entities, top_k=top_k, min_score=min_score)
    anchors = list(
        dict.fromkeys(
            [*anchors, *question_anchors(sg, question, top_k=question_top_k, min_score=question_min_score)]
        )
    )
    if not anchors:
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


def link(
    question: str,
    sg: models.SchemaGraph,
    *,
    entities: list[str] | None = None,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = DEFAULT_MIN_SCORE,
    neighbour_hops: int = DEFAULT_NEIGHBOUR_HOPS,
    question_top_k: int = DEFAULT_QUESTION_TOP_K,
    question_min_score: float = DEFAULT_QUESTION_MIN_SCORE,
) -> models.FocusedSchema:
    """Run the pipeline, retrying deterministically before giving up.

    A miss at the strict thresholds isn't necessarily "the schema can't answer this" — it's
    often "the phrasing didn't embed close enough". Rather than surface an empty slice and
    hope the ReAct model notices and re-asks (it often doesn't — see the two dvdrental L4
    questions where the agent flatly claimed a table didn't exist instead of retrying), retry
    twice here, deterministically, before this returns empty:

      1. same entities, loosened score/top-k thresholds (RETRY_*) — catches a genuine
         near-miss cheaply, no extra LLM call.
      2. only if that's still empty: one more extract_entities call asking for the bare noun
         inside each phrase ("customer segments" -> "customers") — a compound phrase embeds
         further from a table name than the noun alone does.

    Deliberately NOT looser than this: connecting_tables still has to find a real FK path, so
    a wrong table pulled in by an overly relaxed floor would need a join to something relevant
    to survive — but the floor is still the guard against waving in an unrelated table, so it's
    nudged down, never removed.

    Pass `entities` to skip the LLM extraction call and the retries — the eval harness (and any
    caller re-running link with a fixed entity list) wants exactly the given anchors, not a
    second-guessing pass on top.

    A blank/degenerate question (the ReAct agent calling get_schema with task="" mid-thrash —
    seen in practice) skips straight to empty: retrying different thresholds or phrasings on
    nothing to phrase is two wasted LLM calls that only add latency and Groq rate-limit exposure
    for a result that was never going to change.
    """
    if not question or not question.strip():
        logger.info("link() called with a blank question — skipping retries, empty FocusedSchema")
        return models.FocusedSchema(question=question, anchor_tables=[], path_tables=[], edges=[])

    skip_retry = entities is not None
    if entities is None:
        entities = extract_entities(question)

    focused = _link_once(
        question, sg, entities,
        top_k=top_k, min_score=min_score, neighbour_hops=neighbour_hops,
        question_top_k=question_top_k, question_min_score=question_min_score,
    )
    if focused.path_tables or skip_retry:
        return focused

    logger.info("no anchors for %r — retrying with relaxed thresholds", question)
    focused = _link_once(
        question, sg, entities,
        top_k=RETRY_TOP_K, min_score=RETRY_MIN_SCORE, neighbour_hops=neighbour_hops,
        question_top_k=RETRY_QUESTION_TOP_K, question_min_score=RETRY_QUESTION_MIN_SCORE,
    )
    if focused.path_tables:
        return focused

    logger.info("still no anchors for %r — retrying with broadened entity extraction", question)
    broad_entities = extract_entities(question, broad=True)
    if broad_entities and broad_entities != entities:
        focused = _link_once(
            question, sg, broad_entities,
            top_k=RETRY_TOP_K, min_score=RETRY_MIN_SCORE, neighbour_hops=neighbour_hops,
            question_top_k=RETRY_QUESTION_TOP_K, question_min_score=RETRY_QUESTION_MIN_SCORE,
        )
        if focused.path_tables:
            return focused

    logger.info("linking exhausted all passes for %r — returning empty FocusedSchema", question)
    return models.FocusedSchema(question=question, anchor_tables=[], path_tables=[], edges=[])
