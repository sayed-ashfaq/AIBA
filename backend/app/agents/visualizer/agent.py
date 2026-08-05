"""Charting rows that have already been fetched.

"Now show that as a pie chart" is a question about the previous answer, not a new question about
the database. Re-running the query to answer it would cost a round trip to the user's database and
risk coming back with different numbers — the same prose sitting above a chart that no longer
matches it. So this reads the rows the last query returned and re-picks a chart from them.

The rules and the model call are the same ones the SQL agent uses, deliberately: a chart asked for
by name and a chart chosen automatically should be the same chart, drawn by the same code.
"""

from app.agents.main_agent.state import AgentState
from app.agents.visualizer import charts
from app.core.logging import get_logger

logger = get_logger(__name__)

NO_DATA = (
    "I don't have any query results to chart yet — nothing has been fetched in this conversation. "
    "Ask for the data you want and I'll chart it as part of the answer."
)


def _cannot_chart(profile) -> str:
    shape = ", ".join(f"{c.name} ({c.role})" for c in profile.columns)
    return (
        f"I can't draw that from these rows: {profile.row_count} row(s) of {shape}. A chart needs "
        f"something to measure and something to measure it against."
    )


def visualizer_node(state: AgentState) -> dict:
    result = state.get("prior_result")
    if result is None or not result.rows:
        return {"agent_output": NO_DATA, "agent_sql": None}

    # always_ask: the user named a chart, so the model decides even where the rules leave one
    # option — it can decline, which is a better answer than quietly drawing something else
    spec, profile = charts.select(state["refined_query"], result, always_ask=True)

    if spec is None:
        logger.info("re-chart declined for a %d-row result", profile.row_count)
        # no rows returned either: without a chart there is nothing to draw them into, and storing
        # a second copy of the same result under a message that shows none of it earns nothing
        return {"agent_output": _cannot_chart(profile), "agent_sql": None}

    logger.info("re-chart: %s of %s by %s", spec.type.value, ", ".join(spec.y), spec.x)
    # The rows ride along again, so this turn's message stores its own copy rather than pointing at
    # the one it came from. A reference would be cheaper — a chain of re-charts keeps a copy per
    # turn — but it would also be a foreign key that has to survive the original being deleted, and
    # the client's chart controls already switch type without asking the server, so this path should
    # stay rare. Revisit if it doesn't.
    return {
        "agent_output": f"Here's that as a {spec.type.value} chart: {spec.title}.",
        "agent_sql": None,
        "result_rows": result.rows,
        "result_columns": result.columns,
        "result_truncated": result.truncated,
        "chart_spec": spec,
        "chart_profile": profile,
    }
