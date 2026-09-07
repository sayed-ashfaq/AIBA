"""Parse a slice of ``aiba.log`` (the bytes written while one question ran) into structured
counts and timings.

The backend's OperationLoggingMiddleware writes one line per tool call, shaped like:

    2026-09-01 17:33:38 - app.agents.shared.operation_logging - INFO - SpawnProcess-2 - \
        orchestrator: task({'description': 'Return the total count...', 'subagent_type': 'sql_agent'}) — 3.98s
    ... - sql_agent: sql_generator({...}) — 1.20s
    ... - sql_agent: execute_sql({}) — 0.34s
    ... - app.agents.subagents.sql_agent.tools - INFO - ... - execute_sql: 9 row(s), truncated=False, written to /.../result.json
    ... - root - INFO - ... - Total query completion took 5.54s

The one number you asked about — how many times the orchestrator re-activated the SQL
agent for a single question — is ``sql_agent_invocations`` (count of ``task(... 'sql_agent')``
lines). ``redundant`` = that minus 1.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

# a real log record starts with a timestamp; anything else is a continuation line
# (the indented SQL/code body the middleware prints under a call) and is skipped.
_LINE = re.compile(
    r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d - (?P<logger>\S+) - \w+ - \S+ - (?P<msg>.*)$"
)
_DURATION = re.compile(r"(?:—|--)\s*(?P<sec>\d+(?:\.\d+)?)s\s*$")
_FAILED = re.compile(r"failed after (?P<sec>\d+(?:\.\d+)?)s")
_SUBAGENT = re.compile(r"subagent_type'?\s*:\s*'(?P<name>[a-z_]+)'")
_TASK_DESC = re.compile(r"'description'\s*:\s*'(?P<d>.*?)'\s*,\s*'subagent_type'")
_ROWS_WRITTEN = re.compile(
    r"execute_sql:\s*(?P<rows>\d+) row\(s\), truncated=(?P<trunc>\w+)"
)
_TOOK = re.compile(r"took\s+(?P<sec>\d+(?:\.\d+)?)s")
_GRAPH = re.compile(r"graph schema(?: linking)?: (?P<rest>.+)$")


@dataclass
class LogSlice:
    sql_agent_invocations: int = 0  # orchestrator -> sql_agent delegations for this question
    redundant_sql_agent_calls: int = 0  # = max(0, sql_agent_invocations - 1)
    other_delegations: dict = field(default_factory=dict)  # e.g. {"python_agent": 1}
    sql_generator_calls: int = 0
    execute_sql_calls: int = 0
    execute_sql_failures: int = 0
    tool_seconds: dict = field(default_factory=dict)  # tool name -> summed wall seconds
    total_completion_seconds: float | None = None
    verifier_seconds: float | None = None
    rows_written: int | None = None
    result_truncated: bool | None = None
    graph_events: list = field(default_factory=list)  # graph-mode schema-linking log messages
    task_descriptions: list = field(default_factory=list)
    # the SQL of the LAST execute_sql call in this slice, recovered from the indented body
    # the middleware logs. The scorer falls back to this when the /chat response carries no
    # `sql` (e.g. python_agent/visualizer produced the final turn).
    last_executed_sql: str | None = None
    executed_sql: list = field(default_factory=list)  # every execute_sql body, in order

    def as_dict(self) -> dict:
        return asdict(self)


def _sec(text: str) -> float | None:
    m = _DURATION.search(text) or _FAILED.search(text)
    return float(m["sec"]) if m else None


def parse(slice_text: str) -> LogSlice:
    out = LogSlice()
    collecting_sql: list[str] | None = None  # buffer while inside an execute_sql body block

    for raw in slice_text.splitlines():
        m = _LINE.match(raw)
        if not m:
            # continuation line: the indented SQL/code body the middleware prints under a call
            if collecting_sql is not None and (raw.startswith("    ") or not raw.strip()):
                collecting_sql.append(raw[4:] if raw.startswith("    ") else raw)
            continue

        if collecting_sql is not None:  # the body block just ended
            body = "\n".join(collecting_sql).strip()
            if body:
                out.executed_sql.append(body)
                out.last_executed_sql = body
            collecting_sql = None

        logger, msg = m["logger"], m["msg"]

        if logger.endswith("operation_logging"):
            _agent, _, rest = msg.partition(": ")
            if not rest:
                continue
            tool = rest.split("(", 1)[0].split(" ", 1)[0].strip()
            secs = _sec(rest)
            if secs is not None:
                out.tool_seconds[tool] = round(out.tool_seconds.get(tool, 0.0) + secs, 3)

            if tool == "execute_sql" and not _FAILED.search(rest):
                collecting_sql = []  # the next indented block is this query

            if tool == "task":
                sub = _SUBAGENT.search(rest)
                name = sub["name"] if sub else "unknown"
                desc = _TASK_DESC.search(rest)
                if desc:
                    out.task_descriptions.append(desc["d"])
                if name == "sql_agent":
                    out.sql_agent_invocations += 1
                else:
                    out.other_delegations[name] = out.other_delegations.get(name, 0) + 1
            elif tool == "sql_generator":
                out.sql_generator_calls += 1
            elif tool == "execute_sql":
                out.execute_sql_calls += 1
                if _FAILED.search(rest):
                    out.execute_sql_failures += 1

        elif logger.endswith("sql_agent.tools"):
            rw = _ROWS_WRITTEN.search(msg)
            if rw:
                out.rows_written = int(rw["rows"])
                out.result_truncated = rw["trunc"] == "True"
            g = _GRAPH.search(msg)
            if g:
                out.graph_events.append(g["rest"].strip())

        elif logger == "root":
            if msg.startswith("Total query completion"):
                t = _TOOK.search(msg)
                out.total_completion_seconds = float(t["sec"]) if t else None
            elif msg.startswith("Verifier review"):
                t = _TOOK.search(msg)
                out.verifier_seconds = float(t["sec"]) if t else None

    if collecting_sql is not None:  # slice ended mid-body
        body = "\n".join(collecting_sql).strip()
        if body:
            out.executed_sql.append(body)
            out.last_executed_sql = body

    out.redundant_sql_agent_calls = max(0, out.sql_agent_invocations - 1)
    return out


if __name__ == "__main__":
    sample = (
        "2026-09-01 17:33:34 - app.agents.shared.operation_logging - INFO - SpawnProcess-2 - "
        "orchestrator: task({'description': 'Count the films', 'subagent_type': 'sql_agent'}) — 3.98s\n"
        "2026-09-01 17:33:35 - app.agents.shared.operation_logging - INFO - SpawnProcess-2 - "
        "sql_agent: sql_generator({'task': 'count films'}) — 1.10s\n"
        "2026-09-01 17:33:35 - app.agents.shared.operation_logging - INFO - SpawnProcess-2 - "
        "sql_agent: execute_sql({}) — 0.20s\n"
        "    SELECT count(*) FROM film\n"
        "2026-09-01 17:33:36 - app.agents.subagents.sql_agent.tools - INFO - SpawnProcess-2 - "
        "execute_sql: 1 row(s), truncated=False, written to /tmp/r.json\n"
        "2026-09-01 17:33:37 - app.agents.shared.operation_logging - INFO - SpawnProcess-2 - "
        "orchestrator: task({'description': 'Count them again', 'subagent_type': 'sql_agent'}) — 2.00s\n"
        "2026-09-01 17:33:38 - root - INFO - SpawnProcess-2 - Total query completion took 9.10s\n"
        "2026-09-01 17:33:39 - root - INFO - SpawnProcess-2 - Verifier review took 0.60s\n"
    )
    r = parse(sample)
    assert r.sql_agent_invocations == 2, r
    assert r.redundant_sql_agent_calls == 1, r
    assert r.sql_generator_calls == 1 and r.execute_sql_calls == 1, r
    assert r.rows_written == 1 and r.result_truncated is False, r
    assert r.total_completion_seconds == 9.10 and r.verifier_seconds == 0.60, r
    assert r.tool_seconds["task"] == 5.98, r.tool_seconds
    assert r.last_executed_sql == "SELECT count(*) FROM film", repr(r.last_executed_sql)
    assert r.executed_sql == ["SELECT count(*) FROM film"], r.executed_sql
    print("logparse self-test passed:", r.as_dict())
