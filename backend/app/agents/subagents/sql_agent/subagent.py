"""The SQL subagent's deep-agents config: name, description (what the orchestrator sees when
deciding to delegate here), system prompt, and its three tools (get_schema, sql_generator,
execute_sql).

Runs as a free-form ReAct loop rather than the old generate/execute/fix subgraph in agents.py — the
model drives generate -> execute, reads its own errors, and retries, guided by prompt.py's retry
cap rather than a hardcoded conditional edge. SQL generation itself is a dedicated tool
(sql_generator) rather than something the subagent writes inline — one model call focused only on
correct SQL outperformed asking the same turn to also decide what to do next.

ToolCallRetryMiddleware: Groq occasionally rejects a malformed tool call (wrong/missing parameter
names) from this model family with a hard 400 that would otherwise crash the turn — see its
docstring. A subagent's middleware is entirely its own; it doesn't inherit whatever the main agent
or another subagent declares, so this has to be listed here too, not just on the orchestrator.
"""

from deepagents import SubAgent

from app.agents.shared.tool_call_retry import ToolCallRetryMiddleware
from app.agents.subagents.sql_agent.prompt import SQL_AGENT_PROMPT
from app.agents.subagents.sql_agent.tools import execute_sql, get_schema, sql_generator
from app.core.llm import get_llm

sql_agent: SubAgent = {
    "name": "sql_agent",
    "description": (
        "Retrieves data from the business's database. Use for any question that needs real "
        "numbers or records — revenue, counts, trends, comparisons, lookups. Give it a precise, "
        "specific data request (not the raw business question); it returns a plain-language "
        "summary, a sample of the rows, and where the full result was saved."
    ),
    "system_prompt": SQL_AGENT_PROMPT,
    "tools": [get_schema, sql_generator, execute_sql],
    "model": get_llm("sql_agent"),
    "middleware": [ToolCallRetryMiddleware()],
}
