from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.agents.main_agent.state import AgentState
from app.agents.sql_agent import db, sql
from app.agents.visualizer import charts
from app.agents.sql_agent.state import SQLAgentState
from app.core.exceptions import DestructiveSQLError, NL2SQLError, NoActiveConnectionError
from app.core.llm import get_llm
from app.core.logging import get_logger, log_duration
from app.prompts.sql_agent import FIXER_PROMPT, GENERATION_PROMPT, SYNTHESIZER_PROMPT

MAX_FIX_ATTEMPTS = 3

# How many rows the synthesizer is shown. A result can now run to thousands, and feeding all of
# them to the model would cost more per turn than the answer is worth while crowding out the rest
# of its context. Fifty is plenty to describe a shape and name the outliers; the full set goes to
# the caller untouched.
SYNTHESIZER_ROW_LIMIT = 50

logger = get_logger(__name__)


def generate_node(state: SQLAgentState) -> dict:
    prompt = GENERATION_PROMPT.format(db_type=state["db_type"], schema=state["schema_text"])
    with log_duration("SQL generation"):
        response = get_llm("sql_agent").invoke([SystemMessage(content=prompt), HumanMessage(content=state["refined_query"])])
    return {"sql_draft": response.content}


def execute_node(state: SQLAgentState) -> dict:
    try:
        with log_duration("SQL execution"):
            cleaned, result = sql.clean_and_execute(state["sql_draft"], state["db_type"], state["connection"])
        logger.info("executed SQL: %s — %d row(s)%s", cleaned, result.row_count, " (capped)" if result.truncated else "")
        return {"cleaned_sql": cleaned, "query_result": result, "error": None, "blocked_reason": None}
    except DestructiveSQLError as exc:
        logger.info("blocked a write/destructive query attempt: %s", exc)
        return {"blocked_reason": str(exc), "error": None}
    except NL2SQLError as exc:
        logger.info("SQL attempt failed: %s", exc)
        return {"error": str(exc)}


def fix_node(state: SQLAgentState) -> dict:
    prompt = FIXER_PROMPT.format(db_type=state["db_type"], schema=state["schema_text"])
    context = (
        f"Original request: {state['refined_query']}\n\n"
        f"Previous SQL attempt:\n{state['sql_draft']}\n\n"
        f"Error:\n{state['error']}"
    )
    with log_duration("SQL fix attempt"):
        response = get_llm("sql_agent").invoke([SystemMessage(content=prompt), HumanMessage(content=context)])
    return {"sql_draft": response.content, "fix_attempts": state.get("fix_attempts", 0) + 1}


def route_after_execute(state: SQLAgentState) -> list[str]:
    """A list, not a name: on success both branches run, in parallel, in the same superstep.

    They write disjoint keys — synthesize the prose, visualize the chart — so LangGraph merges the
    two updates without either overwriting the other.
    """
    if state.get("error") is None:
        return ["synthesize", "visualize"]
    if state.get("fix_attempts", 0) >= MAX_FIX_ATTEMPTS:
        return ["give_up"]
    return ["fix"]


def _rows_for_synthesis(result: db.QueryResult) -> str:
    """The rows the synthesizer is shown, and — when that is not all of them — a plain statement
    that it is summarising a larger set, so it doesn't present a slice as the whole answer."""
    shown = result.rows[:SYNTHESIZER_ROW_LIMIT]
    if len(shown) == result.row_count and not result.truncated:
        return f"Result rows ({result.row_count}): {shown}"

    total = f"{result.row_count}+" if result.truncated else str(result.row_count)
    return (
        f"Result rows: the first {len(shown)} of {total}. Answer for the whole result, and say "
        f"plainly that you are describing a larger set — do not imply these are all of them.\n"
        f"{shown}"
    )


def synthesize_node(state: SQLAgentState) -> dict:
    if state.get("blocked_reason"):
        context = (
            f"Question: {state['refined_query']}\n\n"
            f"This request would require modifying the database rather than just reading from "
            f"it ({state['blocked_reason']}), which is not permitted. Explain this limitation to "
            f"the user."
        )
    else:
        context = (
            f"Question: {state['refined_query']}\n\n"
            f"SQL used: {state['cleaned_sql']}\n\n"
            f"{_rows_for_synthesis(state['query_result'])}"
        )
    with log_duration("Response synthesis"):
        response = get_llm("sql_agent").invoke([SystemMessage(content=SYNTHESIZER_PROMPT), HumanMessage(content=context)])
    return {"answer": response.content}


def visualize_node(state: SQLAgentState) -> dict:
    result = state.get("query_result")
    # no rows to chart: a blocked write never ran a query, and an empty result has nothing to show
    if result is None or not result.rows:
        return {"chart_spec": None, "chart_profile": None}

    spec, profile = charts.select(state["refined_query"], result)
    if spec:
        logger.info("chart: %s of %s by %s", spec.type.value, ", ".join(spec.y), spec.x)
    return {"chart_spec": spec, "chart_profile": profile}


def give_up_node(state: SQLAgentState) -> dict:
    return {
        "answer": (
            f"I couldn't produce a working query for this after {state.get('fix_attempts', 0)} "
            f"attempts. Last error: {state.get('error')}"
        )
    }


def _build_subgraph():
    graph = StateGraph(SQLAgentState)

    graph.add_node("generate", generate_node)
    graph.add_node("execute", execute_node)
    graph.add_node("fix", fix_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("visualize", visualize_node)
    graph.add_node("give_up", give_up_node)

    graph.add_edge(START, "generate")
    graph.add_edge("generate", "execute")
    graph.add_conditional_edges("execute", route_after_execute, ["synthesize", "visualize", "fix", "give_up"])
    graph.add_edge("fix", "execute")
    graph.add_edge("synthesize", END)
    graph.add_edge("visualize", END)
    graph.add_edge("give_up", END)

    return graph.compile()


_subgraph = _build_subgraph()


def sql_agent_node(state: AgentState) -> dict:
    context = state.get("db_context")
    if context is None:
        raise NoActiveConnectionError

    with log_duration("sql_agent total"):
        final = _subgraph.invoke(
            {
                "refined_query": state["refined_query"],
                "connection": context.connection,
                "db_type": context.db_type,
                "db_name": context.db_name,
                "schema_text": context.schema_text,
                "sql_draft": None,
                "cleaned_sql": None,
                "query_result": None,
                "error": None,
                "blocked_reason": None,
                "fix_attempts": 0,
                "chart_spec": None,
                "chart_profile": None,
                "answer": None,
            }
        )

    # None whenever no query ran — a blocked write, or every fix attempt exhausted
    result: db.QueryResult | None = final.get("query_result")
    return {
        "agent_output": final["answer"],
        "agent_sql": final.get("cleaned_sql"),
        "result_rows": result.rows if result else None,
        "result_columns": result.columns if result else None,
        "result_truncated": bool(result and result.truncated),
        "chart_spec": final.get("chart_spec"),
        "chart_profile": final.get("chart_profile"),
    }
