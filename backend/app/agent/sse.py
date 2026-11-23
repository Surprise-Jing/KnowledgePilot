"""SSE framing for one Agent run (docs/contracts.md §4). Owner: B.

The Agent runs as its own task and hands events over a queue, so a slow or silent model
does not block heartbeats. `done` is always the last frame when the client is still there;
if the client goes away the run task is cancelled and the run is persisted as interrupted."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from pydantic import BaseModel

from app.agent.loop import INTERNAL_ERROR, AgentRun, RunSetup

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 15.0
PING_FRAME = ": ping\n\n"


def event_name(event: BaseModel) -> str:
    # schemas.py names each SSE payload model's event in its docstring.
    return (type(event).__doc__ or "").strip()


def format_event(event_id: int, event: BaseModel) -> str:
    data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: {event_name(event)}\ndata: {data}\n\n"


async def stream_run(setup: RunSetup, heartbeat: float = HEARTBEAT_SECONDS) -> AsyncIterator[str]:
    queue: asyncio.Queue[BaseModel] = asyncio.Queue()
    runner = AgentRun(setup, queue.put)
    task = asyncio.create_task(runner.run())
    next_id = 0

    def frame(event: BaseModel) -> str:
        nonlocal next_id
        next_id += 1
        return format_event(next_id, event)

    try:
        while True:
            if not queue.empty():
                yield frame(queue.get_nowait())
                continue
            if task.done():
                break
            getter = asyncio.ensure_future(queue.get())
            try:
                finished, _ = await asyncio.wait(
                    {getter, task}, timeout=heartbeat, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                if not getter.done():
                    getter.cancel()
            if getter in finished:
                yield frame(getter.result())
            elif not finished:
                yield PING_FRAME
        try:
            done = task.result()
        except Exception:
            # AgentRun handles its own errors; reaching this is a bug, but `done` still goes out.
            logger.exception("agent task crashed")
            runner.persist_failed()
            yield frame(INTERNAL_ERROR)
            done = runner.done_event("interrupted")
        yield frame(done)
    finally:
        # Client disconnect or cancellation: stop the run and persist it. Synchronous only,
        # because awaiting is not possible while this generator is being cancelled or closed.
        if not task.done():
            task.cancel()
            runner.persist_interrupted()
