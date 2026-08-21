"""Daemon work queue — pollers enqueue; ingest and heavy work use separate pools.

Route-opt / weather / storm must not occupy the workers that ingest inbox and noon
drops. Two ThreadPoolExecutors: `ingest` and `heavy`.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from vpm_agents.tools.agent_log import progress

LANE_INGEST = "ingest"
LANE_HEAVY = "heavy"
_LANES = (LANE_INGEST, LANE_HEAVY)

_lock = threading.Lock()
_pools: dict[str, ThreadPoolExecutor] = {}
_inflight: dict[str, set[str]] = {LANE_INGEST: set(), LANE_HEAVY: set()}
_queued: dict[str, set[str]] = {LANE_INGEST: set(), LANE_HEAVY: set()}


def _lane_workers(lane: str) -> int:
    try:
        from vpm_agents.config import settings

        if lane == LANE_INGEST:
            return max(1, int(getattr(settings, "daemon_ingest_workers", 2) or 2))
        return max(1, int(getattr(settings, "daemon_workers", 4) or 4))
    except Exception:
        return 2 if lane == LANE_INGEST else 4


def _executor(lane: str) -> ThreadPoolExecutor:
    if lane not in _LANES:
        lane = LANE_HEAVY
    pool = _pools.get(lane)
    if pool is None:
        n = _lane_workers(lane)
        pool = ThreadPoolExecutor(max_workers=n, thread_name_prefix=f"d-{lane[:2]}")
        _pools[lane] = pool
        progress("DaemonJobs", f"{lane} pool started max_workers={n}")
    return pool


def _known(k: str) -> bool:
    return any(k in _queued[L] or k in _inflight[L] for L in _LANES)


def lane_has_prefix(lane: str, prefix: str) -> bool:
    """True if any queued or inflight job key on lane starts with prefix."""
    if lane not in _LANES or not prefix:
        return False
    with _lock:
        for k in _queued[lane]:
            if k.startswith(prefix):
                return True
        for k in _inflight[lane]:
            if k.startswith(prefix):
                return True
    return False


def submit_job(key: str, fn: Callable[[], Any], *, lane: str = LANE_HEAVY) -> Future | None:
    """Enqueue work keyed for dedupe. Returns None if already queued/running."""
    k = (key or "").strip()
    if not k:
        return None
    if lane not in _LANES:
        lane = LANE_HEAVY
    with _lock:
        if _known(k):
            return None
        _queued[lane].add(k)

    def _wrap() -> Any:
        with _lock:
            _queued[lane].discard(k)
            _inflight[lane].add(k)
        try:
            progress("DaemonJobs", f"start {k} [{lane}]")
            return fn()
        except Exception as e:
            progress("DaemonJobs", f"failed {k}: {e}")
            raise
        finally:
            with _lock:
                _inflight[lane].discard(k)
            progress("DaemonJobs", f"done {k}")

    return _executor(lane).submit(_wrap)


def queue_stats() -> dict[str, Any]:
    with _lock:
        lanes = {
            name: {
                "queued": sorted(_queued[name]),
                "inflight": sorted(_inflight[name]),
                "queued_n": len(_queued[name]),
                "inflight_n": len(_inflight[name]),
                "workers": _lane_workers(name),
            }
            for name in _LANES
        }
        queued = [k for name in _LANES for k in lanes[name]["queued"]]
        inflight = [k for name in _LANES for k in lanes[name]["inflight"]]
        return {
            "inflight": inflight,
            "queued": queued,
            "inflight_n": len(inflight),
            "queued_n": len(queued),
            "lanes": lanes,
        }


def reset_for_tests() -> None:
    """Drain tracking sets (does not shut down the pools)."""
    with _lock:
        for name in _LANES:
            _inflight[name].clear()
            _queued[name].clear()


if __name__ == "__main__":
    import time

    done: list[str] = []
    barrier = threading.Barrier(2)

    def a() -> None:
        barrier.wait()
        time.sleep(0.05)
        done.append("a")

    def b() -> None:
        barrier.wait()
        time.sleep(0.05)
        done.append("b")

    fa = submit_job("t:a", a)
    fb = submit_job("t:b", b)
    assert fa is not None and fb is not None
    assert submit_job("t:a", a) is None  # dedupe
    fa.result(timeout=5)
    fb.result(timeout=5)
    assert lane_has_prefix(LANE_INGEST, "t:") is False
    assert set(done) == {"a", "b"}
    st = queue_stats()
    assert st["inflight_n"] == 0 and st["queued_n"] == 0

    # ingest lane must run while heavy pool is saturated
    gate = threading.Event()
    n_heavy = _lane_workers(LANE_HEAVY)
    ready = threading.Barrier(n_heavy + 1)

    def _block() -> None:
        ready.wait(timeout=5)
        gate.wait(timeout=10)

    blockers = [submit_job(f"h:{i}", _block, lane=LANE_HEAVY) for i in range(n_heavy)]
    ready.wait(timeout=5)
    ingest_ran = threading.Event()
    fi = submit_job("i:1", lambda: ingest_ran.set(), lane=LANE_INGEST)
    assert fi is not None
    assert ingest_ran.wait(timeout=2), "ingest starved behind heavy/route-opt"
    gate.set()
    for f in blockers:
        if f is not None:
            f.result(timeout=5)
    fi.result(timeout=5)
    print("daemon_jobs self-check ok")
