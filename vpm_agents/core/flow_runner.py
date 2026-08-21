"""Execute tagged pre-voyage flow chains."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
import time

from vpm_agents.core.daemon_flows import chain_steps, flow_weather_poll, get_flow, prevoyage_chain
from vpm_agents.core.state import SessionState

# Steps that only need ingest output — safe to run concurrently with each other.
_PARALLEL_AFTER_INGEST = frozenset({"weather", "route_optimize"})


class PreVoyageFlowRunner:
    """Run one-shot pre-voyage chain for a flow tag; does not disable background agents."""

    def __init__(self, backend: Any, registry: Any, flow_name: str):
        from inbox_agent.ingest import PreVoyageIngestAgent
        from vpm_agents.agents.continuous import (
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

    def _run_step(self, state: SessionState, step: str) -> SessionState:
        t0 = time.monotonic()
        state.phase = f"flow:{self.flow_name}:{step}"
        state.note("FlowRunner", f"start {step}")
        try:
            if step == "ingest":
                raise RuntimeError("ingest must run via run(), not _run_step")
            if step == "weather":
                voy = state.voyage_number
                if not voy:
                    state.note(
                        "FlowRunner",
                        "weather skip — no voyage from ingest",
                        elapsed_s=time.monotonic() - t0,
                    )
                    return state
                state = self._run_weather(self.backend, self.registry, state, voy, "six_hour_plan")
            elif step == "route_optimize":
                voy = state.voyage_number
                if not voy:
                    state.note(
                        "FlowRunner",
                        "route_optimize skip — no voyage",
                        elapsed_s=time.monotonic() - t0,
                    )
                    return state
                state = self._route_opt.run(state, voyage_number=voy)
            else:
                state.note("FlowRunner", f"unknown step {step}")
        except Exception as e:
            state.note("FlowRunner", f"{step} FAILED: {e}", elapsed_s=time.monotonic() - t0)
        else:
            state.note("FlowRunner", f"done {step}", elapsed_s=time.monotonic() - t0)
        return state

    def _run_parallel(self, state: SessionState, steps: list[str]) -> SessionState:
        """Run independent post-ingest steps concurrently; merge notes/artifacts."""
        if not steps:
            return state
        if len(steps) == 1:
            return self._run_step(state, steps[0])

        voy = state.voyage_number
        state.note("FlowRunner", f"parallel start {steps} voy={voy}")
        t0 = time.monotonic()

        def _one(step: str) -> SessionState:
            st = SessionState()
            st.voyage_number = voy
            st.phase = state.phase
            return self._run_step(st, step)

        with ThreadPoolExecutor(max_workers=len(steps), thread_name_prefix="flow") as pool:
            futs = {pool.submit(_one, s): s for s in steps}
            for fut in as_completed(futs):
                step = futs[fut]
                try:
                    st = fut.result()
                except Exception as e:
                    state.note("FlowRunner", f"{step} FAILED: {e}")
                    continue
                state.log.extend(st.log)
                state.artifacts.update(st.artifacts)
                if st.voyage_number:
                    state.voyage_number = st.voyage_number

        state.note("FlowRunner", f"parallel done {steps}", elapsed_s=time.monotonic() - t0)
        return state

    def run(
        self,
        state: SessionState,
        path: Path,
        *,
        enqueue_after_ingest: bool = False,
    ) -> SessionState:
        chain = prevoyage_chain(self.flow_name)
        steps = chain_steps(self.flow_name)
        chain_t0 = time.monotonic()
        state.note("FlowRunner", f"flow={self.flow_name} chain={steps} file={path.name}")

        pending_parallel: list[str] = []
        for step in chain:
            if step == "stop":
                if pending_parallel:
                    state = self._run_parallel(state, pending_parallel)
                    pending_parallel = []
                state.note(
                    "FlowRunner",
                    f"chain boundary — skipping agents beyond {steps[-1] if steps else 'ingest'}",
                )
                break
            if step == "ingest":
                if pending_parallel:
                    state = self._run_parallel(state, pending_parallel)
                    pending_parallel = []
                state.phase = f"flow:{self.flow_name}:{step}"
                state.note("FlowRunner", f"start {step}")
                t0 = time.monotonic()
                try:
                    state = self._ingest.run(state, path=path)
                except Exception as e:
                    state.note("FlowRunner", f"{step} FAILED: {e}", elapsed_s=time.monotonic() - t0)
                else:
                    state.note("FlowRunner", f"done {step}", elapsed_s=time.monotonic() - t0)
                if enqueue_after_ingest:
                    rest = [s for s in steps if s != "ingest"]
                    self._enqueue_post_ingest(state, rest)
                    state.phase = f"flow:{self.flow_name}:ingest_queued"
                    state.note(
                        "FlowRunner",
                        f"ingest done — heavy steps queued {rest}",
                        elapsed_s=time.monotonic() - chain_t0,
                    )
                    return state
                continue
            if step in _PARALLEL_AFTER_INGEST:
                pending_parallel.append(step)
                continue
            if pending_parallel:
                state = self._run_parallel(state, pending_parallel)
                pending_parallel = []
            state = self._run_step(state, step)

        if pending_parallel:
            state = self._run_parallel(state, pending_parallel)

        state.phase = f"flow:{self.flow_name}:done"
        state.note("FlowRunner", "chain complete", elapsed_s=time.monotonic() - chain_t0)
        return state

    def _enqueue_post_ingest(self, state: SessionState, steps: list[str]) -> None:
        """Hand off to other services via registry + job bus — ingest does not wait."""
        voy = state.voyage_number
        if not voy:
            return
        from vpm_agents.tools import job_bus

        rec = self.registry.get(voy) or {}
        if "weather" in steps:
            # weather_due_at is set on Departure Report ingest, not pre-voyage
            state.note("FlowRunner", f"weather handoff {voy} (post-departure only)")
        if "route_optimize" in steps:
            key = f"routeopt:{voy}:pre_voyage"
            if job_bus.enqueue(
                key,
                {"kind": "routeopt", "voyage_number": voy, "trigger": "pre_voyage"},
            ):
                state.note("FlowRunner", f"queued {key}")
            else:
                state.note("FlowRunner", f"{key} already pending/running — not re-queued")
