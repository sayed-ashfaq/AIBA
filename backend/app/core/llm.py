from langchain_groq import ChatGroq

from app.core.config import settings

_AGENT_MODELS = {
    "main_agent": settings.main_agent_model,
    "sql_agent": settings.sql_agent_model,
    "python_agent": settings.python_agent_model,
    "visualizer": settings.visualizer_model,
    "verifier": settings.verifier_model,
}


def get_llm(agent: str, **kwargs) -> ChatGroq:
    model = _AGENT_MODELS.get(agent)
    if model is None:
        raise ValueError(f"No model configured for agent '{agent}'")
    return ChatGroq(model=model, api_key=settings.groq_api_key, **kwargs)
