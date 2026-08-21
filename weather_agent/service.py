"""Passage weather report microservice."""

from vpm_agents.config import settings
from vpm_agents.core.state import SessionState
from vpm_agents.poll_loop import poll_forever
from vpm_agents.tools import get_backend
from vpm_agents.tools.voyage_registry import VoyageRegistry


def run_forever() -> None:
    from vpm_agents.agents.continuous import WeatherReportAgent

    backend = get_backend()
    registry = VoyageRegistry()
    agent = WeatherReportAgent(backend, registry)

    def tick() -> None:
        agent.run(SessionState(), enqueue=False)

    poll_forever("weather", max(1.0, settings.inbox_poll_seconds), tick)
