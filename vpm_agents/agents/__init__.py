from vpm_agents.agents.specialists import (
    AlertAgent,
    AuthAgent,
    FleetAgent,
    PerformanceReportAgent,
    RouteOptimizationAgent,
    StormGeofenceAgent,
    VoyageAgent,
    WeatherAgent,
)
from vpm_agents.agents.continuous import (
    EndOfVoyageReportAgent,
    NoonExcelWatchAgent,
    NoonOpsAgent,
    PreVoyageRouteOptimizeAgent,
    StormWatchAgent,
    WeatherReportAgent,
)

__all__ = [
    "AuthAgent",
    "FleetAgent",
    "VoyageAgent",
    "RouteOptimizationAgent",
    "WeatherAgent",
    "AlertAgent",
    "StormGeofenceAgent",
    "PerformanceReportAgent",
    "PreVoyageIngestAgent",
    "PreVoyageRouteOptimizeAgent",
    "NoonOpsAgent",
    "NoonExcelWatchAgent",
    "StormWatchAgent",
    "WeatherReportAgent",
    "InboxWatchAgent",
    "MailInboxAgent",
    "EndOfVoyageReportAgent",
]


def __getattr__(name: str):
    if name in ("PreVoyageIngestAgent", "InboxWatchAgent", "MailInboxAgent"):
        from inbox_agent.ingest import PreVoyageIngestAgent
        from inbox_agent.watch import InboxWatchAgent, MailInboxAgent

        mapping = {
            "PreVoyageIngestAgent": PreVoyageIngestAgent,
            "InboxWatchAgent": InboxWatchAgent,
            "MailInboxAgent": MailInboxAgent,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
