"""Assembles the orchestrator: a single `create_deep_agent` graph wired with the sql_agent,
python_agent, and visualizer subagents (knowledge_agent joins once it's built), the orchestrator's
own instructions, and its runtime context schema.

Replaces the hand-rolled supervisor in main_agent/main.py — routing between subagents becomes a
tool-calling decision the orchestrator's own model makes, not a separate classifier LLM call.

TodoListMiddleware is added explicitly rather than relied on as a default: create_deep_agent only
auto-includes it for harness profiles that request it (e.g. OpenAI's Codex profile), and Groq
models don't match any built-in profile, so without this line write_todos would not exist.

ToolCallRetryMiddleware is here for the same reason it's on each subagent (see their subagent.py) —
this main-agent model call is just as exposed to Groq rejecting a malformed tool call as any
subagent's is, and each agent's model calls are only covered by middleware declared on that same
agent's own stack, not inherited from here.

OperationLoggingMiddleware writes one local log line per tool call this agent makes directly —
task() delegations (which subagent, what it was asked) and write_todos — the same reason it's on
each subagent too: nothing here is inherited from a subagent's own stack or vice versa.
"""

from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware

from app.agents.orchestrator.context import AgentContext
from app.agents.orchestrator.instructions import ORCHESTRATOR_INSTRUCTIONS
from app.agents.shared.operation_logging import OperationLoggingMiddleware
from app.agents.shared.tool_call_retry import ToolCallRetryMiddleware
from app.agents.subagents.python_agent.subagent import python_agent
from app.agents.subagents.sql_agent.subagent import sql_agent
from app.agents.subagents.visualizer.subagent import visualizer
from app.core.llm import get_llm

agent = create_deep_agent(
    model=get_llm("main_agent"),
    system_prompt=ORCHESTRATOR_INSTRUCTIONS,
    subagents=[sql_agent, python_agent, visualizer],
    middleware=[
        TodoListMiddleware(),
        ToolCallRetryMiddleware(),
        OperationLoggingMiddleware("orchestrator"),
    ],
    context_schema=AgentContext,
)
