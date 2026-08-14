"""Named workflow helpers — thin wrappers over SupervisorOrchestrator."""

from __future__ import annotations

from vpm_agents.core.orchestrator import SupervisorOrchestrator
from vpm_agents.core.state import SessionState


def full_voyage_lifecycle(**kwargs) -> SessionState:
    return SupervisorOrchestrator().run_workflow("full_voyage_lifecycle", **kwargs)


def optimize_and_publish(**kwargs) -> SessionState:
    return SupervisorOrchestrator().run_workflow("optimize_and_publish", **kwargs)


def storm_response(**kwargs) -> SessionState:
    return SupervisorOrchestrator().run_workflow("storm_response", **kwargs)


def daily_monitoring(**kwargs) -> SessionState:
    return SupervisorOrchestrator().run_workflow("daily_monitoring", **kwargs)


def performance_closeout(**kwargs) -> SessionState:
    return SupervisorOrchestrator().run_workflow("performance_closeout", **kwargs)
