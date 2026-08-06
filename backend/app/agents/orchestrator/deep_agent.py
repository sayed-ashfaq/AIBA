"""Assembles the orchestrator: a single `create_deep_agent` graph wired with the sql_agent and
python_agent subagents (visualizer and knowledge_agent join once they're built), the orchestrator's
own instructions, and its runtime context schema.

Replaces the hand-rolled supervisor in main_agent/main.py — routing between subagents becomes a
tool-calling decision the orchestrator's own model makes, not a separate classifier LLM call.

TodoListMiddleware is added explicitly rather than relied on as a default: create_deep_agent only
auto-includes it for harness profiles that request it (e.g. OpenAI's Codex profile), and Groq
models don't match any built-in profile, so without this line write_todos would not exist.
"""

from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware

from app.agents.orchestrator.context import AgentContext
from app.agents.orchestrator.instructions import ORCHESTRATOR_INSTRUCTIONS
from app.agents.subagents.python_agent.subagent import python_agent
from app.agents.subagents.sql_agent.subagent import sql_agent
from app.core.llm import get_llm

agent = create_deep_agent(
    model=get_llm("main_agent"),
    system_prompt=ORCHESTRATOR_INSTRUCTIONS,
    subagents=[sql_agent, python_agent],
    middleware=[TodoListMiddleware()],
    context_schema=AgentContext,
)
