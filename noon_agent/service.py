"""Noon Excel / DB poll microservice."""

from vpm_agents.config import settings
from vpm_agents.core.state import SessionState
from vpm_agents.poll_loop import poll_forever
from vpm_agents.tools import get_backend
from vpm_agents.tools.agent_log import progress
from vpm_agents.tools.voyage_registry import VoyageRegistry


def run_forever() -> None:
    from vpm_agents.agents.continuous import NoonExcelWatchAgent

    backend = get_backend()
    registry = VoyageRegistry()
    progress("noon", f"source={settings.noon_source} tenant={settings.tenant or '(unset)'}")
    agent = NoonExcelWatchAgent(backend, registry)

    def tick() -> None:
        agent.run(SessionState(), enqueue=True)

    poll_forever("noon", settings.noon_poll_seconds, tick)
