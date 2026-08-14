"""Tagged continuous daemon flows — select how far a pre-voyage run goes."""

from __future__ import annotations

from typing import Any

from vpm_agents.core.spec_loader import load_agent_spec

_VALID_STEPS = frozenset({"ingest", "weather", "route_optimize", "stop"})


def load_daemon_flows() -> tuple[dict[str, dict[str, Any]], str]:
    spec = load_agent_spec("DaemonFlows")
    flows = spec.get("flows") or {}
    if not flows:
        raise ValueError("DaemonFlows.md Defaults.flows is empty")
    default = spec.get("default_flow") or next(iter(flows))
    for name, cfg in flows.items():
        chain = cfg.get("prevoyage_chain") or []
        for step in chain:
            if step not in _VALID_STEPS:
                raise ValueError(f"flow {name}: unknown step {step!r}")
    return flows, default


DAEMON_FLOWS, DEFAULT_DAEMON_FLOW = load_daemon_flows()


def list_daemon_flows() -> dict[str, str]:
    return {name: cfg.get("description", "") for name, cfg in DAEMON_FLOWS.items()}


def get_flow(name: str) -> dict[str, Any]:
    if name not in DAEMON_FLOWS:
        raise ValueError(f"unknown flow: {name}. choose from {list(DAEMON_FLOWS)}")
    return DAEMON_FLOWS[name]


def prevoyage_chain(name: str) -> list[str]:
    """One-shot steps when pre-voyage Excel arrives; 'stop' marks chain boundary only."""
    chain = list(get_flow(name).get("prevoyage_chain") or ["ingest", "stop"])
    if "stop" not in chain:
        chain.append("stop")
    return chain[: chain.index("stop") + 1]


def chain_steps(name: str) -> list[str]:
    """Runnable steps only (no boundary marker)."""
    return [s for s in prevoyage_chain(name) if s != "stop"]


def flow_weather_poll(flow: dict[str, Any]) -> bool:
    """WeatherReportAgent keeps polling while this flow is active."""
    if "weather_poll" in flow:
        return bool(flow["weather_poll"])
    return "weather" in (flow.get("prevoyage_chain") or [])
