from app.agents.main_agent.state import AgentState


def knowledge_agent_node(state: AgentState) -> dict:
    return {
        "agent_output": (
            f"[knowledge_agent] not wired up yet — this will search the web (Tavily) and any "
            f"provided database for context. Refined query was: {state['refined_query']}"
        ),
        "agent_sql": None,
    }
