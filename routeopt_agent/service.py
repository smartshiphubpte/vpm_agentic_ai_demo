"""Route-optimize job worker."""

from __future__ import annotations

import os

from vpm_agents.poll_loop import poll_forever
from vpm_agents.tools import get_backend
from vpm_agents.tools.agent_log import progress
from vpm_agents.tools.voyage_registry import VoyageRegistry


def run_forever() -> None:
    from vpm_agents.runtime import drain_routeopt_once
    from vpm_agents.tools import job_bus

    backend = get_backend()
    registry = VoyageRegistry()
    interval = float(os.getenv("VPM_ROUTEOPT_POLL_SECONDS", "2") or "2")
    n = job_bus.reclaim_running(kind="routeopt")
    if n:
        progress("routeopt", f"reclaimed {n} orphaned running job(s)")

    def tick() -> None:
        n = drain_routeopt_once(backend, registry)
        if n:
            progress("routeopt", f"processed {n} job(s)")

    poll_forever("routeopt", interval, tick)
