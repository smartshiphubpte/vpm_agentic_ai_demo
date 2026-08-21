"""Four parallel route-objective sub-agents. Each loads its own MD spec."""

from __future__ import annotations

from typing import Any

from vpm_agents.core.base import Agent, Tool, ToolResult

OBJECTIVE_SPEC = {
    "fastest": "RouteOptFastestAgent",
    "shortest": "RouteOptShortestAgent",
    "fuel": "RouteOptFuelAgent",
    "lowest-fuel": "RouteOptFuelAgent",
    "safest": "RouteOptSafestAgent",
}


class RouteObjectiveAgent(Agent):
    """One objective. Spec MD is the acting brief; Defaults.optimize_for is the search key."""

    def build_tools(self) -> list[Tool]:
        return [Tool("optimize", "Propose a sea-only route for this objective", self._optimize)]

    def _optimize(
        self,
        token: str,
        waypoints: list,
        weather: dict | None = None,
        storms: list | None = None,
        speed_kn: float | None = None,
        fuel_mt_day: float | None = None,
    ) -> ToolResult:
        key = str(self.spec.get("optimize_for") or "shortest")
        data = self.backend.optimize_route(
            token,
            key,
            waypoints,
            weather=weather,
            storms=storms,
            speed_kn=speed_kn,
            fuel_mt_day=fuel_mt_day,
        )
        return ToolResult(ok=True, data=data)

    def propose(
        self,
        token: str,
        waypoints: list,
        weather: dict | None = None,
        storms: list | None = None,
        *,
        speed_kn: float | None = None,
        fuel_mt_day: float | None = None,
    ) -> ToolResult:
        return self.call(
            "optimize",
            token=token,
            waypoints=waypoints,
            weather=weather,
            storms=storms,
            speed_kn=speed_kn,
            fuel_mt_day=fuel_mt_day,
        )

    def run(self, state: Any, **kwargs: Any) -> Any:
        res = self.propose(
            kwargs.get("token") or "",
            kwargs.get("waypoints") or [],
            kwargs.get("weather"),
            kwargs.get("storms"),
            speed_kn=kwargs.get("speed_kn"),
            fuel_mt_day=kwargs.get("fuel_mt_day"),
        )
        state.note(self.name, "ok" if res.ok else (res.error or "failed"))
        return state


class RouteOptFastestAgent(RouteObjectiveAgent):
    name = "RouteOptFastestAgent"


class RouteOptShortestAgent(RouteObjectiveAgent):
    name = "RouteOptShortestAgent"


class RouteOptFuelAgent(RouteObjectiveAgent):
    name = "RouteOptFuelAgent"


class RouteOptSafestAgent(RouteObjectiveAgent):
    name = "RouteOptSafestAgent"


_AGENT_CLS = {
    "fastest": RouteOptFastestAgent,
    "shortest": RouteOptShortestAgent,
    "fuel": RouteOptFuelAgent,
    "safest": RouteOptSafestAgent,
}


def spawn_objective_agents(backend: Any) -> dict[str, RouteObjectiveAgent]:
    """One live agent per objective (each reads its MD at init)."""
    return {oid: cls(backend) for oid, cls in _AGENT_CLS.items()}
