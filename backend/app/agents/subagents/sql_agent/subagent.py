"""The SQL subagent's deep-agents config: name, description (what the orchestrator sees when
deciding to delegate here), system prompt, and its two tools (get_schema, execute_sql).

Runs as a free-form ReAct loop rather than the old generate/execute/fix subgraph in agents.py — the
model calls execute_sql, reads its own errors, and retries, guided by prompt.py's retry cap rather
than a hardcoded conditional edge.
"""

from deepagents import SubAgent

from app.agents.subagents.sql_agent.prompt import SQL_AGENT_PROMPT
from app.agents.subagents.sql_agent.tools import execute_sql, get_schema
from app.core.llm import get_llm

sql_agent: SubAgent = {
    "name": "sql_agent",
    "description": (
        "Retrieves data from the business's database by writing and running SQL. Use for any "
        "question that needs real numbers or records — revenue, counts, trends, comparisons, "
        "lookups. Give it a precise, specific data request (not the raw business question); it "
        "returns a plain-language summary, a sample of the rows, and where the full result was "
        "saved."
    ),
    "system_prompt": SQL_AGENT_PROMPT,
    "tools": [get_schema, execute_sql],
    "model": get_llm("sql_agent"),
}
