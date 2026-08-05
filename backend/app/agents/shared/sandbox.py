"""Runs model-written Python in a resource-limited subprocess: a hard timeout, a memory cap, and an
import allowlist (pandas, numpy — no filesystem or network access beyond the shared result files).

Used by python_agent. Not wired into the visualizer yet — it stays on the deterministic
charts.select path until a request needs custom charting the rule engine can't cover.
"""
