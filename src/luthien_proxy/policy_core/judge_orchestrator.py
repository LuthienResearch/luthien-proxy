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
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

TagT = TypeVar("TagT")
ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class Bailed:
    """Sentinel returned by `JudgeOrchestrator.collect()` for cancelled items.

    Items whose dispatch was cancelled because an earlier submission triggered
    the bail predicate are reported as `Bailed()` instead of a real result.
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

    `bail_predicate` is consulted on each non-bailed result; the first
    result for which it returns True cancels every later task. Cancellation
    propagates as `asyncio.CancelledError` into the underlying coroutine —
    HTTP libraries built on `asyncio` (httpx, aiohttp) abort in-flight
    requests cleanly, so cancellation is a real perf win, not just a
    bookkeeping change.
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
        """Await all submitted tasks in submission order, applying the bail rule.

        Returns one `(tag, result_or_Bailed)` per `submit()` call, in the
        order they were submitted. Idempotent within a single
        orchestrator: calling twice raises `RuntimeError`.
        """
        if self._collected:
            raise RuntimeError("JudgeOrchestrator.collect called twice")
        self._collected = True

        results: list[tuple[TagT, ResultT | Bailed]] = []
        bailed = False

        for tag, task in self._items:
            if bailed:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
                results.append((tag, Bailed()))
                continue

            try:
                result = await task
            except asyncio.CancelledError:
                results.append((tag, Bailed()))
                continue

            results.append((tag, result))
            if self.bail_predicate is not None and self.bail_predicate(result):
                bailed = True

        return results


__all__ = ["JudgeOrchestrator", "Bailed"]
