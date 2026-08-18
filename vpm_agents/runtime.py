"""Independent service loops — one process per role, shared registry/folders/jobs."""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Callable
from typing import Any

from vpm_agents.config import settings
from vpm_agents.core.state import SessionState
from vpm_agents.tools import get_backend
from vpm_agents.tools.agent_log import progress
from vpm_agents.tools.voyage_registry import VoyageRegistry, voyage_is_closed


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


def drain_routeopt_once(backend: Any | None = None, registry: VoyageRegistry | None = None) -> int:
    """Claim and run at most one routeopt job. Returns 1 if work ran, else 0.

    One job per call so multiple routeopt containers can share the pending queue.
    """
    from vpm_agents.agents.continuous import PreVoyageRouteOptimizeAgent
    from vpm_agents.tools import job_bus

    backend = backend or get_backend()
    registry = registry or VoyageRegistry()
    agent = PreVoyageRouteOptimizeAgent(backend, registry)
    job = job_bus.claim(kind="routeopt")
    if not job:
        return 0
    voy = str(job.get("voyage_number") or "")
    rec = registry.get(voy)
    if not rec:
        job_bus.skip(job, "voyage not in registry")
        progress("routeopt", f"{voy} skip — not in registry")
        return 1
    if voyage_is_closed(rec):
        job_bus.skip(job, "voyage closed")
        progress("routeopt", f"{voy} skip — voyage closed")
        return 1
    trigger = str(job.get("trigger") or "pre_voyage")
    wps = job.get("waypoints")
    speed = job.get("speed_kn")
    try:
        st = SessionState()
        st.voyage_number = voy
        agent.run(
            st,
            voyage_number=voy,
            waypoints=wps,
            speed_kn=float(speed) if speed is not None else None,
            trigger=trigger,
        )
        job_bus.complete(job)
    except Exception as e:
        job_bus.fail(job, str(e))
        progress("routeopt", f"{voy} failed: {e}")
    return 1


def run_ingest() -> None:
    from vpm_agents.agents.continuous import InboxWatchAgent

    backend = get_backend()
    registry = VoyageRegistry()
    agent = InboxWatchAgent(backend, registry, flow_name=settings.daemon_flow)

    def tick() -> None:
        agent.run(SessionState(), enqueue=True)

    poll_forever("ingest", settings.inbox_poll_seconds, tick)


def run_noon() -> None:
    from vpm_agents.agents.continuous import NoonExcelWatchAgent

    backend = get_backend()
    registry = VoyageRegistry()
    progress("noon", f"source={settings.noon_source} tenant={settings.tenant or '(unset)'}")
    agent = NoonExcelWatchAgent(backend, registry)

    def tick() -> None:
        agent.run(SessionState(), enqueue=True)

    poll_forever("noon", settings.noon_poll_seconds, tick)


def run_weather() -> None:
    from vpm_agents.agents.continuous import WeatherReportAgent

    backend = get_backend()
    registry = VoyageRegistry()
    agent = WeatherReportAgent(backend, registry)

    def tick() -> None:
        agent.run(SessionState(), enqueue=False)

    poll_forever("weather", max(1.0, settings.inbox_poll_seconds), tick)


def run_routeopt() -> None:
    backend = get_backend()
    registry = VoyageRegistry()
    interval = float(os.getenv("VPM_ROUTEOPT_POLL_SECONDS", "2") or "2")
    from vpm_agents.tools import job_bus

    n = job_bus.reclaim_running(kind="routeopt")
    if n:
        progress("routeopt", f"reclaimed {n} orphaned running job(s)")

    def tick() -> None:
        n = drain_routeopt_once(backend, registry)
        if n:
            progress("routeopt", f"processed {n} job(s)")

    poll_forever("routeopt", interval, tick)


def run_storm() -> None:
    from vpm_agents.agents.continuous import StormWatchAgent

    backend = get_backend()
    registry = VoyageRegistry()
    agent = StormWatchAgent(backend, registry)
    interval = max(1.0, settings.storm_interval_hours * 3600.0)

    def tick() -> None:
        agent.run(SessionState())

    poll_forever("storm", interval, tick)


def run_report_sender() -> None:
    from report_sender.service import run_forever

    run_forever()


def run_prevoyage_db() -> None:
    from prevoyage_db.service import run_forever

    run_forever()


def run_port_weather() -> None:
    from port_weather.service import run_forever

    run_forever()


SERVICES: dict[str, Callable[[], None]] = {
    "ingest": run_ingest,
    "noon": run_noon,
    "weather": run_weather,
    "routeopt": run_routeopt,
    "storm": run_storm,
    "report_sender": run_report_sender,
    "prevoyage_db": run_prevoyage_db,
    "port_weather": run_port_weather,
}
