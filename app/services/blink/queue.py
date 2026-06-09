"""
BlinkQueue — serialized asyncio priority queue with a single worker.

All Blink operations run through this queue so no two Blink calls overlap.

Priority rules:
  - Lower priority number runs first.
  - A monotonic sequence counter breaks ties (FIFO within a priority).

Deduplication:
  - Entries with the same non-None key are de-duped synchronously in the
    calling thread, so submit_nowait returns the *identical* Future object
    for a duplicate while the original is still queued.

Thread safety:
  - submit_nowait / reprioritize are safe to call from any thread (e.g.
    Flask request threads). Internally they use a threading.Lock to protect
    the in-queue registry and loop.call_soon_threadsafe to wake the worker.

Priorities used by the application:
  0 = arm / disarm
  1 = manifest / status
  2 = boosted clip
  3 = bulk clip
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import heapq
import itertools
import threading
from typing import Callable, Coroutine, Any, Optional


class _Entry:
    """Metadata for one queued job (factory + future)."""

    __slots__ = ("priority", "seq", "key", "factory", "future", "stale")

    def __init__(
        self,
        priority: int,
        seq: int,
        key: Optional[str],
        factory: Callable[[], Coroutine],
        future: concurrent.futures.Future,
    ) -> None:
        self.priority = priority
        self.seq = seq
        self.key = key
        self.factory = factory
        self.future = future
        self.stale = False  # set True when reprioritized or deduped-out


class BlinkQueue:
    """Serialized asyncio priority queue with a single background worker."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._counter = itertools.count()
        self._lock = threading.Lock()           # protects heap + registry
        self._heap: list[tuple[int, int, Optional[str]]] = []  # (pri, seq, key)
        self._entries: dict[tuple, _Entry] = {} # (pri, seq, key) -> _Entry
        self._keyed: dict[str, _Entry] = {}     # non-None key -> _Entry (in-queue only)
        self._wakeup: asyncio.Event = asyncio.run_coroutine_threadsafe(
            self._make_event(), loop
        ).result(5)
        asyncio.run_coroutine_threadsafe(self._worker(), loop)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _make_event(self) -> asyncio.Event:
        return asyncio.Event()

    def _heap_key(self, entry: _Entry) -> tuple:
        return (entry.priority, entry.seq, entry.key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit_nowait(
        self,
        priority: int,
        factory: Callable[[], Coroutine],
        key: Optional[str] = None,
    ) -> concurrent.futures.Future:
        """
        Enqueue a job and return a Future for its result.

        Thread-safe; returns immediately.  If *key* is not None and an entry
        with that key is already queued, the existing Future is returned
        and the new factory is discarded (dedup).
        """
        with self._lock:
            # Synchronous dedup: if the key is already queued, return that future.
            if key is not None and key in self._keyed:
                return self._keyed[key].future

            seq = next(self._counter)
            fut: concurrent.futures.Future = concurrent.futures.Future()
            entry = _Entry(priority, seq, key, factory, fut)
            hk = self._heap_key(entry)
            heapq.heappush(self._heap, hk)
            self._entries[hk] = entry
            if key is not None:
                self._keyed[key] = entry

        # Wake the worker (thread-safe).
        self._loop.call_soon_threadsafe(self._wakeup.set)
        return fut

    def submit(
        self,
        priority: int,
        factory: Callable[[], Coroutine],
        key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Blocking version of submit_nowait. Waits for the result."""
        return self.submit_nowait(priority, factory, key).result(timeout)

    def reprioritize(self, key: str, new_priority: int) -> None:
        """
        Change the priority of a queued entry identified by *key*.

        No-op if the key is not currently queued (already running or done).
        Thread-safe; returns immediately.
        """
        with self._lock:
            if key not in self._keyed:
                return  # not queued — no-op

            old_entry = self._keyed[key]
            old_entry.stale = True           # mark old heap entry as stale
            del self._entries[self._heap_key(old_entry)]

            seq = next(self._counter)
            new_entry = _Entry(
                new_priority, seq, key, old_entry.factory, old_entry.future
            )
            hk = self._heap_key(new_entry)
            heapq.heappush(self._heap, hk)
            self._entries[hk] = new_entry
            self._keyed[key] = new_entry

        self._loop.call_soon_threadsafe(self._wakeup.set)

    # ------------------------------------------------------------------
    # Worker (runs on the background event loop)
    # ------------------------------------------------------------------

    async def _worker(self) -> None:
        while True:
            # Wait until there is something on the heap.
            await self._wakeup.wait()

            while True:
                with self._lock:
                    if not self._heap:
                        self._wakeup.clear()
                        break

                    hk = heapq.heappop(self._heap)
                    entry = self._entries.pop(hk, None)

                    if entry is None or entry.stale:
                        # Stale heap slot (reprioritized); skip.
                        continue

                    # Remove from keyed registry so dedup stops applying.
                    if entry.key is not None:
                        self._keyed.pop(entry.key, None)

                # Run outside the lock.
                try:
                    result = await entry.factory()
                    entry.future.set_result(result)
                except Exception as exc:  # noqa: BLE001
                    entry.future.set_exception(exc)
