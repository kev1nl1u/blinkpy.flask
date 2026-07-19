import asyncio
import time
import pytest
from app.services.blink.queue import BlinkQueue


def _make(order, name, delay=0.0):
    async def coro():
        if delay:
            await asyncio.sleep(delay)
        order.append(name)
        return name
    return coro


def test_returns_result(bg_loop):
    q = BlinkQueue(bg_loop)
    fut = q.submit_nowait(1, _make([], "x"))
    assert fut.result(5) == "x"


def test_priority_and_fifo(bg_loop):
    q = BlinkQueue(bg_loop)
    order = []
    fb = q.submit_nowait(3, _make(order, "block", 0.3))
    time.sleep(0.05)
    fd = q.submit_nowait(3, _make(order, "dl"))
    fa = q.submit_nowait(0, _make(order, "arm"))
    for f in (fb, fd, fa):
        f.result(5)
    assert order == ["block", "arm", "dl"]


def test_dedup_returns_same_future(bg_loop):
    q = BlinkQueue(bg_loop)
    order = []
    q.submit_nowait(3, _make(order, "block", 0.3))
    time.sleep(0.05)
    f1 = q.submit_nowait(3, _make(order, "a"), key="k")
    f2 = q.submit_nowait(3, _make(order, "a2"), key="k")
    assert f1 is f2
    f1.result(5)
    assert order.count("a") == 1 and "a2" not in order


def test_reprioritize(bg_loop):
    q = BlinkQueue(bg_loop)
    order = []
    q.submit_nowait(3, _make(order, "block", 0.3))
    time.sleep(0.05)
    fa = q.submit_nowait(3, _make(order, "a"), key="a")
    fb = q.submit_nowait(3, _make(order, "b"), key="b")
    q.reprioritize("b", 0)
    for f in (fa, fb):
        f.result(5)
    assert order == ["block", "b", "a"]


def test_exception_propagates_and_worker_survives(bg_loop):
    q = BlinkQueue(bg_loop)
    async def boom():
        raise ValueError("nope")
    f1 = q.submit_nowait(1, boom)
    with pytest.raises(ValueError, match="nope"):
        f1.result(5)
    f2 = q.submit_nowait(1, _make([], "ok"))
    assert f2.result(5) == "ok"


def test_reprioritize_missing_key_is_noop(bg_loop):
    q = BlinkQueue(bg_loop)
    q.reprioritize("ghost", 0)


def test_snapshot_reports_running_and_pending(bg_loop):
    q = BlinkQueue(bg_loop)
    order = []
    fb = q.submit_nowait(3, _make(order, "block", 0.3), label="download", key="b")
    time.sleep(0.05)  # let the worker pick up "block"
    q.submit_nowait(0, _make(order, "arm"), label="arm")
    snap = q.snapshot()
    assert snap["running"] is not None
    assert snap["running"]["label"] == "download"
    assert snap["running"]["key"] == "b"
    labels = [j["label"] for j in snap["pending"]]
    assert "arm" in labels
    fb.result(5)


def test_snapshot_empty_when_idle(bg_loop):
    q = BlinkQueue(bg_loop)
    assert q.submit(1, _make([], "x")) == "x"
    time.sleep(0.05)  # let the worker's finally-block clear the running slot
    snap = q.snapshot()
    assert snap["running"] is None
    assert snap["pending"] == []
