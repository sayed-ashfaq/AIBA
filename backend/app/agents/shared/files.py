"""Conventions and helpers for the orchestrator's private virtual filesystem: the path scheme
subagents use to hand data to each other — e.g. where the SQL agent writes result rows for the
visualizer and Python agent to read.

Separate from the durable Postgres persistence in services/results.py, which exists for cross-turn
re-charting rather than within-turn handoff between subagents.
"""
