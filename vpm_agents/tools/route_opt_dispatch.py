"""Dispatch local (or backend) route optimize by VPM_ROUTE_OPT_METHOD."""

from __future__ import annotations

from typing import Any

from vpm_agents.config import settings


def optimize_local(
    objective: str,
    waypoints: list,
    weather: dict | None = None,
    storms: list | None = None,
    *,
    speed_kn: float | None = None,
    fuel_mt_day: float | None = None,
) -> dict[str, Any]:
    method = (settings.route_opt_method or "conventional").strip().lower()
    if method == "llm":
        from vpm_agents.tools.route_opt_llm import optimize_llm

        return optimize_llm(objective, waypoints, weather, storms)
    from vpm_agents.tools.route_opt_conventional import optimize_conventional

    return optimize_conventional(
        objective, waypoints, weather, storms,
        speed_kn=speed_kn, fuel_mt_day=fuel_mt_day,
    )
