"""Shared poll loop for one-process microservices."""

from __future__ import annotations

import signal
import time
from collections.abc import Callable

from vpm_agents.tools.agent_log import progress


def poll_forever(name: str, interval_s: float, tick: Callable[[], None]) -> None:
    stop = False

    def _sig(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    interval = max(1.0, interval_s)
    progress(name, f"started poll={interval}s")
    while not stop:
        t0 = time.monotonic()
        try:
            tick()
        except Exception as e:
            progress(name, f"error: {e}")
        leftover = interval - (time.monotonic() - t0)
        if leftover > 0 and not stop:
            time.sleep(leftover)
    progress(name, "stopped")
