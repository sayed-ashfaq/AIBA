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

ToolCallLimitMiddleware enforces prompt.py's "up to 3 attempts total across both tools" as a hard
cap rather than a convention the model is trusted to count on its own. One instance per tool (the
middleware only filters by a single tool_name) caps sql_generator and execute_sql at 3 calls each,
matching the generate -> execute retry pair the prompt walks through — the 4th call to either is
blocked with a tool-error message instead of letting a model that loses count loop indefinitely.
run_limit is per subagent invocation (deepagents calls `.invoke()` fresh for every task()
delegation, with no checkpointer carrying state across them), which is exactly "3 attempts at this
task", not 3 for the graph's lifetime.

OperationLoggingMiddleware writes one local log line per tool call — get_schema, sql_generator,
execute_sql — naming this agent and including the exact SQL, so `grep`ing the log file shows what
ran without opening LangSmith.
"""

from deepagents import SubAgent
from langchain.agents.middleware import ToolCallLimitMiddleware

from app.agents.shared.operation_logging import OperationLoggingMiddleware
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
    "middleware": [
        ToolCallRetryMiddleware(),
        ToolCallLimitMiddleware(tool_name="sql_generator", run_limit=3, exit_behavior="continue"),
        ToolCallLimitMiddleware(tool_name="execute_sql", run_limit=3, exit_behavior="continue"),
        OperationLoggingMiddleware("sql_agent"),
    ],
}
