from langchain_groq import ChatGroq

from app.core.config import settings

_AGENT_MODELS = {
    "main_agent": settings.main_agent_model,
    "sql_agent": settings.sql_agent_model,
    "sql_generator": settings.sql_generator_model,
    "analytics_agent": settings.analytics_agent_model,
    "visualizer": settings.visualizer_model,
    "verifier": settings.verifier_model,
}

# per-agent reasoning_effort override — absent/None means "send nothing, let the model default".
_AGENT_REASONING_EFFORT = {
    "sql_agent": settings.sql_agent_reasoning_effort,
    "sql_generator": settings.sql_generator_reasoning_effort,
}

# Groq's implicit max_completion_tokens (applied whenever a call omits max_tokens) is well under
# what a tool call needs when it has to echo a whole schema_context back as a JSON argument — a wide
# schema (a few dozen wide tables, each carrying enumerated value hints) generates a tool-call
# payload long enough to hit that implicit cap mid-JSON, which Groq reports as a 400
# 'tool_use_failed' rather than a length-limit error. 32768 is comfortably above every payload size
# observed so far and still well under gpt-oss-120b's actual 65536-token ceiling.
_DEFAULT_MAX_TOKENS = 32768


def get_llm(agent: str, **kwargs) -> ChatGroq:
    model = _AGENT_MODELS.get(agent)
    if model is None:
        raise ValueError(f"No model configured for agent '{agent}'")
    effort = _AGENT_REASONING_EFFORT.get(agent)
    if effort and "reasoning_effort" not in kwargs:
        kwargs["reasoning_effort"] = effort
    kwargs.setdefault("max_tokens", _DEFAULT_MAX_TOKENS)
    return ChatGroq(model=model, api_key=settings.groq_api_key, **kwargs)
