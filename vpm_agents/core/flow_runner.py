"""Execute tagged pre-voyage flow chains."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vpm_agents.core.daemon_flows import chain_steps, flow_weather_poll, get_flow, prevoyage_chain
from vpm_agents.core.state import SessionState


class PreVoyageFlowRunner:
    """Run one-shot pre-voyage chain for a flow tag; does not disable background agents."""

    def __init__(self, backend: Any, registry: Any, flow_name: str):
        from vpm_agents.agents.continuous import (
            PreVoyageIngestAgent,
            PreVoyageRouteOptimizeAgent,
            _run_immediate_weather,
        )

        self.backend = backend
        self.registry = registry
        self.flow_name = flow_name
        self.flow = get_flow(flow_name)
        self._ingest = PreVoyageIngestAgent(backend, registry)
        self._route_opt = PreVoyageRouteOptimizeAgent(backend, registry)
        self._run_weather = _run_immediate_weather

    def run(self, state: SessionState, path: Path) -> SessionState:
        chain = prevoyage_chain(self.flow_name)
        steps = chain_steps(self.flow_name)
        state.note("FlowRunner", f"flow={self.flow_name} chain={steps}")

        for step in chain:
            if step == "stop":
                state.note(
                    "FlowRunner",
                    f"chain boundary — skipping agents beyond {steps[-1] if steps else 'ingest'}",
                )
                break
            if step == "ingest":
                schedule = flow_weather_poll(self.flow) and "weather" in steps
                state = self._ingest.run(state, path=path, schedule_weather=schedule)
            elif step == "weather":
                voy = state.voyage_number
                if not voy:
                    state.note("FlowRunner", "weather skip — no voyage from ingest")
                    continue
                state = self._run_weather(self.backend, self.registry, state, voy, "six_hour_plan")
            elif step == "route_optimize":
                voy = state.voyage_number
                if not voy:
                    state.note("FlowRunner", "route_optimize skip — no voyage")
                    continue
                state = self._route_opt.run(state, voyage_number=voy)
            else:
                state.note("FlowRunner", f"unknown step {step}")

        state.phase = f"flow:{self.flow_name}:done"
        return state
