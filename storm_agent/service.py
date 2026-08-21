"""Storm snapshot poller."""

from vpm_agents.config import settings
from vpm_agents.core.state import SessionState
from vpm_agents.poll_loop import poll_forever
from vpm_agents.tools import get_backend
from vpm_agents.tools.voyage_registry import VoyageRegistry


def run_forever() -> None:
    from vpm_agents.agents.continuous import StormWatchAgent

    backend = get_backend()
    registry = VoyageRegistry()
    agent = StormWatchAgent(backend, registry)
    interval = max(1.0, settings.storm_interval_hours * 3600.0)

    def tick() -> None:
        agent.run(SessionState())

    poll_forever("storm", interval, tick)
