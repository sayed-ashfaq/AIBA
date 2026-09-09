"""Live turn progress: the bridge that carries one event per tool call from the agent's worker
thread out to a streaming HTTP response, so the client can show what the agent is doing instead of
a bare spinner.

Only POST /chat/stream uses this. The agent graph is synchronous and runs on a worker thread (see
router/chat.py), so events are produced off the event loop; `ProgressChannel.emit` is what makes it
safe to call from there — it hops each event back to the loop thread before touching the asyncio
queue the SSE generator drains. OperationLoggingMiddleware is the one producer: it already sees
every tool call, and calls `emit` when the run's AgentContext carries one.
"""

import asyncio
import json
from typing import Any, AsyncIterator


def sse_event(event: str, data: dict[str, Any]) -> str:
    """One Server-Sent Events frame. `data` is JSON on a single line — the client splits frames on
    the blank line, so the payload must not contain one."""
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def reduce_events(events: list[dict]) -> list[dict]:
    """Fold the raw stream (tool_start / tool_end / stage) into the render-ready step list the
    client draws — one row per tool call, completed in place by its matching tool_end. This is the
    shape persisted on the message (Message.activity) so a reopened chat rebuilds the exact trail
    the live turn showed. Key names match the client's step objects (subagentType, not
    subagent_type) so the stored list needs no translation on the way back out.
    """
    steps: list[dict] = []
    by_key: dict[str, dict] = {}

    for ev in events:
        etype = ev.get("type")
        if etype == "tool_start":
            key = ev.get("call_id")
            if key in by_key:
                continue
            step = {
                "key": key,
                "agent": ev.get("agent"),
                "tool": ev.get("tool"),
                "sql": ev.get("sql"),
                "subagentType": ev.get("subagent_type"),
                "description": ev.get("description"),
                "todos": ev.get("todos"),
                "status": "running",
            }
            steps.append(step)
            by_key[key] = step
        elif etype == "tool_end":
            step = by_key.get(ev.get("call_id"))
            if step is None:
                continue
            step["status"] = "error" if ev.get("status") == "error" else "done"
            step["elapsed"] = ev.get("elapsed")
            if ev.get("sql"):
                step["sql"] = ev["sql"]
        elif etype == "stage":
            key = f"stage-{ev.get('stage')}"
            if key in by_key:
                continue
            step = {"key": key, "stage": ev.get("stage"), "status": "done"}
            steps.append(step)
            by_key[key] = step

    # a tool still "running" when the stream closed did finish — the turn is over by the time this
    # runs, the last tool_end just wasn't drained
    for step in steps:
        if step.get("status") == "running":
            step["status"] = "done"
    return steps


class ProgressChannel:
    """Created per streamed turn, inside the SSE generator so it binds to the running loop.

    The agent runs via `asyncio.to_thread`, so `emit` is invoked from that worker thread and must
    not call `Queue.put_nowait` directly. `drain` yields events as they arrive and returns once the
    agent task is finished and nothing is left buffered.
    """

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[dict] = asyncio.Queue()

    def emit(self, event: dict) -> None:
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)
        except RuntimeError:
            # loop already closed — the client disconnected mid-turn. The agent thread can't be
            # cancelled, so it keeps running and its events land nowhere; that's fine.
            pass

    async def drain(self, task: "asyncio.Task") -> AsyncIterator[dict]:
        while not (task.done() and self._queue.empty()):
            try:
                yield await asyncio.wait_for(self._queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
        # a threadsafe callback scheduled just as the task finished may not have run yet
        await asyncio.sleep(0)
        while not self._queue.empty():
            yield self._queue.get_nowait()
