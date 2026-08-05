from typing import Optional, TypedDict

from app.agents.subagents.sql_agent.db import Connection, QueryResult
from app.agents.subagents.visualizer.charts import ChartSpec
from app.agents.subagents.visualizer.profile import ResultProfile


class SQLAgentState(TypedDict):
    refined_query: str
    # the specific database to execute against — carried through state rather than looked up, so
    # two users querying at the same time cannot land on each other's connection
    connection: Connection
    db_type: str
    db_name: str
    schema_text: str

    sql_draft: Optional[str]
    cleaned_sql: Optional[str]
    # the rows themselves, kept whole. The synthesizer is shown a slice of this and the caller gets
    # all of it — a chart needs the data behind the summary, not the fifty rows that described it.
    query_result: Optional[QueryResult]
    error: Optional[str]
    blocked_reason: Optional[str]
    fix_attempts: int

    # written by visualize, which runs beside synthesize rather than after it — the two read the
    # same rows and need nothing from each other, so serialising them would add a whole model call
    # to the turn for no reason. None whenever the result isn't worth a chart.
    chart_spec: Optional[ChartSpec]
    chart_profile: Optional[ResultProfile]

    # the prose answer, named apart from query_result so the two are not mistaken for each other
    answer: Optional[str]
