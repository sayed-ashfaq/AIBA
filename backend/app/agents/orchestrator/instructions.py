"""The orchestrator's system prompt: what it is, how to use write_todos to plan multi-step
requests, when to delegate to which subagent, and how to read and write the shared result files in
its virtual filesystem instead of passing large row sets through conversation history.

Replaces prompts/main_agent.py's SYSTEM_PROMPT and RESPOND_PROMPT.
"""
