"""Independent service loops — dispatch to each service package."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vpm_agents.poll_loop import poll_forever
from vpm_agents.tools.agent_log import progress
from vpm_agents.tools.voyage_registry import VoyageRegistry, voyage_is_closed

__all__ = ["SERVICES", "poll_forever", "drain_routeopt_once"]


def drain_routeopt_once(backend: Any | None = None, registry: VoyageRegistry | None = None) -> int:
    """Claim and run at most one routeopt job. Returns 1 if work ran, else 0."""
    from vpm_agents.agents.continuous import PreVoyageRouteOptimizeAgent
    from vpm_agents.tools import get_backend, job_bus

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
        from vpm_agents.core.state import SessionState

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
    from inbox_agent.service import run_forever

    run_forever()


def run_noon() -> None:
    from noon_agent.service import run_forever

    run_forever()


def run_weather() -> None:
    from weather_agent.service import run_forever

    run_forever()


def run_routeopt() -> None:
    from routeopt_agent.service import run_forever

    run_forever()


def run_storm() -> None:
    from storm_agent.service import run_forever

    run_forever()


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
