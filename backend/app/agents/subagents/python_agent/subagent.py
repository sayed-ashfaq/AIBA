"""The Python agent's deep-agents config: name, description, prompt, and its one tool
(python_sandbox). Handles logical operations on already-retrieved data — filtering, aggregation,
derived metrics — kept separate from the visualizer so a correctness-critical computation prompt
never shares scope with chart-styling judgement.
"""
