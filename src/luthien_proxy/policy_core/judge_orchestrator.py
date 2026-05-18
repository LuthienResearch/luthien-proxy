"""Concurrent dispatch and ordered collection of policy judge calls.

`JudgeOrchestrator` is a small generic helper for fan-out / ordered fan-in
with an optional bail rule. Policies submit awaitables as soon as they
have something to judge; tasks run concurrently in the event loop. At the
end of the response the policy calls `collect()` to receive the results
in *submission order*. If a `bail_predicate` is supplied, the first
result that matches it cancels every still-pending task — subsequent
items collect as `Bailed()` regardless of completion state.

This separates two concerns from the policy:

- *Concurrency:* the orchestrator owns task creation and cancellation;
  the policy never touches `asyncio.create_task` or `asyncio.gather`.
- *Order-respecting bail:* "later" is defined by submission order (which
  matches upstream block order), not completion order. A pass that
  finished early can still be bailed by a block that finishes later.

Reusable by any policy that wants concurrent decision-making with an
optional early-exit rule.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

TagT = TypeVar("TagT")
ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class Bailed:
    """Sentinel returned by `JudgeOrchestrator.collect()` for cancelled items.

    Items whose dispatch was cancelled because the bail predicate fired on
    a peer task are reported as `Bailed()` instead of a real result.
    Distinguishable from a real `ResultT` via `isinstance`.
    """


@dataclass
class JudgeOrchestrator(Generic[TagT, ResultT]):
    """Per-request concurrent-dispatch / ordered-collect helper.

    Construct one per request (typically via `PolicyContext.get_request_state`).
    Call `submit(tag, coro)` from each upstream event handler that has
    something to judge; the coroutine is wrapped in a task and starts
    running on the next event-loop tick. Call `collect()` once at the end
    of the response to receive `[(tag, result_or_Bailed), ...]` in
    submission order.

    `bail_predicate` is consulted on each result as it completes (not in
    submission order). The first result for which it returns True cancels
    every still-pending task immediately — results already in hand at that
    moment keep their real value. Cancellation propagates as
    `asyncio.CancelledError` into the underlying coroutine; HTTP libraries
    built on `asyncio` (httpx, aiohttp) abort in-flight requests cleanly.
    """

    bail_predicate: Callable[[ResultT], bool] | None = None
    _items: list[tuple[TagT, "asyncio.Task[ResultT]"]] = field(default_factory=list)
    _collected: bool = False

    def submit(self, tag: TagT, coro: Awaitable[ResultT]) -> None:
        """Dispatch `coro` concurrently; remember `tag` for the collect step.

        `tag` is opaque caller metadata returned alongside the result. Use
        it to associate the decision back to whichever buffered upstream
        block this judge was for.
        """
        if self._collected:
            raise RuntimeError("JudgeOrchestrator.submit called after collect()")
        task = asyncio.ensure_future(coro)
        self._items.append((tag, task))

    async def collect(self) -> list[tuple[TagT, ResultT | Bailed]]:
        """Wait for tasks as they finish, bailing immediately on the first match.

        Returns one `(tag, result_or_Bailed)` per `submit()` call, in
        submission order. Tasks that resolved before the bail trigger keep
        their real result; tasks still pending at the bail moment are
        cancelled and surface as `Bailed()`. Calling twice raises.
        """
        if self._collected:
            raise RuntimeError("JudgeOrchestrator.collect called twice")
        self._collected = True

        if not self._items:
            return []

        task_meta: dict[asyncio.Task[ResultT], tuple[int, TagT]] = {
            task: (i, tag) for i, (tag, task) in enumerate(self._items)
        }
        results: list[tuple[TagT, ResultT | Bailed] | None] = [None] * len(self._items)
        pending: set[asyncio.Task[ResultT]] = {task for _, task in self._items}
        bailed = False

        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            triggered = False
            for task in done:
                idx, tag = task_meta[task]
                if task.cancelled():
                    results[idx] = (tag, Bailed())
                    continue
                exc = task.exception()
                if exc is not None:
                    logger.warning(
                        "JudgeOrchestrator: task raised %s; recording as Bailed", type(exc).__name__, exc_info=exc
                    )
                    results[idx] = (tag, Bailed())
                    continue
                result = task.result()
                results[idx] = (tag, result)
                if not bailed and self.bail_predicate is not None and self.bail_predicate(result):
                    triggered = True
            if triggered and not bailed:
                bailed = True
                for task in pending:
                    task.cancel()

        return [r for r in results if r is not None]


__all__ = ["JudgeOrchestrator", "Bailed"]
