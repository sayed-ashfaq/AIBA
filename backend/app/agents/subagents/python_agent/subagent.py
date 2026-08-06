"""The python subagent's deep-agents config: name, description (what the orchestrator sees when
deciding to delegate here, and — critically — how it's told apart from sql_agent), system prompt,
and its one tool (run_python).
"""

from deepagents import SubAgent

from app.agents.subagents.python_agent.prompt import PYTHON_AGENT_PROMPT
from app.agents.subagents.python_agent.tools import run_python
from app.core.llm import get_llm

python_agent: SubAgent = {
    "name": "python_agent",
    "description": (
        "Computes on data sql_agent already retrieved — it has no database access of its own. Use "
        "for anything a single SQL query can't express: growth/change rates across separate "
        "periods, combining two or more separate result sets, statistical or custom logic. Give it "
        "the file path(s) sql_agent reported and a precise description of the computation, not the "
        "raw business question. If a question can be answered with SQL alone (filtering, grouping, "
        "aggregation, a single ranking), use sql_agent instead — this one is for what SQL can't do."
    ),
    "system_prompt": PYTHON_AGENT_PROMPT,
    "tools": [run_python],
    "model": get_llm("python_agent"),
}
