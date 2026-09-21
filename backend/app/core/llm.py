from langchain_groq import ChatGroq

from app.core.config import settings

_AGENT_MODELS = {
    "main_agent": settings.main_agent_model,
    "sql_agent": settings.sql_agent_model,
    "sql_generator": settings.sql_generator_model,
    "python_agent": settings.python_agent_model,
    "visualizer": settings.visualizer_model,
    "verifier": settings.verifier_model,
}

# per-agent reasoning_effort override — absent/None means "send nothing, let the model default".
_AGENT_REASONING_EFFORT = {
    "sql_agent": settings.sql_agent_reasoning_effort,
    "sql_generator": settings.sql_generator_reasoning_effort,
}


def get_llm(agent: str, **kwargs) -> ChatGroq:
    model = _AGENT_MODELS.get(agent)
    if model is None:
        raise ValueError(f"No model configured for agent '{agent}'")
    effort = _AGENT_REASONING_EFFORT.get(agent)
    if effort and "reasoning_effort" not in kwargs:
        kwargs["reasoning_effort"] = effort
    return ChatGroq(model=model, api_key=settings.groq_api_key, **kwargs)
