"""Assembles the orchestrator: a single `create_deep_agent` graph wired with the four subagents
(sql_agent, visualizer, python_agent, knowledge_agent), the orchestrator's own instructions, and its
runtime context schema.

Replaces the hand-rolled supervisor in main_agent/main.py — routing between subagents becomes a
tool-calling decision the orchestrator's own model makes, not a separate classifier LLM call.
"""
