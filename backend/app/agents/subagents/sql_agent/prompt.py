"""System prompt for the SQL subagent: how to read the schema, write SQL for the target dialect,
interpret an execute_sql error and retry (capped at three attempts), and write result rows to the
shared virtual filesystem instead of returning them inline.

Replaces prompts/sql_agent.py's GENERATION_PROMPT, FIXER_PROMPT and SYNTHESIZER_PROMPT.
"""
