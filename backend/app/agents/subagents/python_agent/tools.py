"""The python_sandbox tool: runs model-written code against a shared result file in a
resource-limited subprocess (timeout, memory cap, restricted imports — pandas/numpy only) and
returns whatever it writes back to the virtual filesystem. Backed by shared/sandbox.py.
"""
