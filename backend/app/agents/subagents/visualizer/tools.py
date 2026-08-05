"""Tools exposed to the visualizer subagent: select_chart, wrapping the existing rule-based
charts.select — deliberately not code execution. Most requests are a re-plot of already-correct
rows, and the deterministic picker answers those without an LLM loop at all.
"""
