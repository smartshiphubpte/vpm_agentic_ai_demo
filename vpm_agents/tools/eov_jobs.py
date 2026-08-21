"""Background job queue for End-of-Voyage reports — never blocks the daemon poll loop."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from vpm_agents.tools.agent_log import progress

_lock = threading.Lock()
_pool: ThreadPoolExecutor | None = None
_inflight: set[str] = set()


def _executor() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        # ponytail: small pool; EOV is rare vs inbox/noon poll cadence
        _pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="eov")
    return _pool


def submit_eov_report(voyage_number: str, fn: Callable[[], Any]) -> Future | None:
    """Fire-and-forget EOV build. Dedupes concurrent jobs for the same voyage."""
    voy = (voyage_number or "").strip()
    if not voy:
        return None
    with _lock:
        if voy in _inflight:
            progress("EOVJobs", f"{voy} already running — skip duplicate")
            return None
        _inflight.add(voy)

    def _wrap() -> Any:
        try:
            progress("EOVJobs", f"{voy} start (background)")
            return fn()
        except Exception as e:
            progress("EOVJobs", f"{voy} failed: {e}")
            raise
        finally:
            with _lock:
                _inflight.discard(voy)
            progress("EOVJobs", f"{voy} done")

    return _executor().submit(_wrap)


def inflight_voyages() -> list[str]:
    with _lock:
        return sorted(_inflight)
