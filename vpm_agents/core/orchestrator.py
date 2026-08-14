"""Supervisor orchestrator — goal routing + named multi-agent workflows.

Plan catalogue (workflows + goal hints) is loaded from
`agents/specs/SupervisorOrchestrator.md` Defaults JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from vpm_agents.agents import (
    AlertAgent,
    AuthAgent,
    FleetAgent,
    PerformanceReportAgent,
    RouteOptimizationAgent,
    StormGeofenceAgent,
    VoyageAgent,
    WeatherAgent,
)
from vpm_agents.core.spec_loader import load_agent_spec
from vpm_agents.config import settings
from vpm_agents.core.llm import plan_goal
from vpm_agents.core.state import SessionState
from vpm_agents.tools import get_backend


def _load_supervisor_plan() -> tuple[dict[str, list[str]], list[tuple[tuple[str, ...], str]], str]:
    spec = load_agent_spec("SupervisorOrchestrator")
    workflows = {k: list(v) for k, v in (spec.get("workflows") or {}).items()}
    if not workflows:
        raise ValueError("SupervisorOrchestrator.md Defaults.workflows is empty")
    hints: list[tuple[tuple[str, ...], str]] = []
    for row in spec.get("goal_hints") or []:
        keys = tuple(row["keys"])
        hints.append((keys, row["workflow"]))
    fallback = spec.get("fallback_workflow") or next(iter(workflows))
    return workflows, hints, fallback


WORKFLOWS, _GOAL_HINTS, _FALLBACK_WORKFLOW = _load_supervisor_plan()


class SupervisorOrchestrator:
    """Central planner/router over specialist agents."""

    def __init__(self, backend: Any | None = None):
        self.backend = backend or get_backend()
        self.spec = load_agent_spec("SupervisorOrchestrator")
        self.agents: dict[str, Any] = {
            "AuthAgent": AuthAgent(self.backend),
            "FleetAgent": FleetAgent(self.backend),
            "VoyageAgent": VoyageAgent(self.backend),
            "RouteOptimizationAgent": RouteOptimizationAgent(self.backend),
            "WeatherAgent": WeatherAgent(self.backend),
            "AlertAgent": AlertAgent(self.backend),
            "StormGeofenceAgent": StormGeofenceAgent(self.backend),
            "PerformanceReportAgent": PerformanceReportAgent(self.backend),
        }
        self._runners: dict[str, Callable[..., SessionState]] = {
            name: agent.run for name, agent in self.agents.items()
        }

    def roster(self) -> list[dict[str, str]]:
        return [
            {
                "name": a.name,
                "description": a.description,
                "tools": ", ".join(a.tools.keys()),
                "spec": str(a.spec.path),
            }
            for a in self.agents.values()
        ]

    def list_workflows(self) -> dict[str, list[str]]:
        return dict(WORKFLOWS)

    def resolve_goal(self, goal: str) -> list[str]:
        """Plan: LLM if available, else keyword → named workflow."""
        llm_plan = plan_goal(goal, self.roster())
        if llm_plan:
            known = [n for n in llm_plan if n in self.agents]
            if known:
                if "AuthAgent" not in known:
                    known = ["AuthAgent"] + known
                return known
        g = goal.lower()
        for keys, wf in _GOAL_HINTS:
            if any(k in g for k in keys):
                return list(WORKFLOWS[wf])
        return list(WORKFLOWS[_FALLBACK_WORKFLOW])

    def run_workflow(
        self,
        name: str,
        state: SessionState | None = None,
        **kwargs: Any,
    ) -> SessionState:
        plan = WORKFLOWS.get(name)
        if not plan:
            raise ValueError(f"unknown workflow: {name}. choose from {list(WORKFLOWS)}")
        return self._execute(plan, state or SessionState(), workflow=name, **kwargs)

    def run_goal(self, goal: str, state: SessionState | None = None, **kwargs: Any) -> SessionState:
        plan = self.resolve_goal(goal)
        return self._execute(plan, state or SessionState(), workflow=f"goal:{goal}", **kwargs)

    def _execute(
        self,
        plan: list[str],
        state: SessionState,
        workflow: str,
        **kwargs: Any,
    ) -> SessionState:
        state.note("Supervisor", f"workflow={workflow} plan={plan}")
        email = kwargs.pop("email", None) or settings.email
        password = kwargs.pop("password", None) or settings.password
        company = kwargs.pop("company", None) or settings.company
        state.user_email = state.user_email or email
        state.artifacts.setdefault("password", password)

        for agent_name in plan:
            runner = self._runners[agent_name]
            if agent_name == "AuthAgent":
                state = runner(state, email=email, password=password, company=company)
            elif agent_name == "FleetAgent":
                state = runner(state, vessel_id=kwargs.get("vessel_id"))
            elif agent_name == "VoyageAgent":
                # Ports/route fall through to VoyageAgent.md Defaults when omitted
                state = runner(
                    state,
                    departure=kwargs.get("departure"),
                    destination=kwargs.get("destination"),
                    route=kwargs.get("route"),
                    voyage_number=kwargs.get("voyage_number"),
                )
            elif agent_name == "RouteOptimizationAgent":
                state = runner(state, objectives=kwargs.get("objectives"))
            elif agent_name == "PerformanceReportAgent":
                state = runner(state, noon_reports=kwargs.get("noon_reports"))
            elif agent_name == "StormGeofenceAgent":
                state = runner(state, position=kwargs.get("position"))
            elif agent_name == "AlertAgent":
                state = runner(state, rules=kwargs.get("rules"))
            else:
                state = runner(state)

        state.note("Supervisor", f"complete phase={state.phase}")
        return state

    def save_state(self, state: SessionState, path: str | Path | None = None) -> Path:
        path = Path(path or (settings.data_dir / "last_run.json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state.to_dict(), indent=2))
        return path
