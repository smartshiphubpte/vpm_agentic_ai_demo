#!/usr/bin/env python3
"""End-to-end demo of the VoyagePM agentic framework (mock mode)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vpm_agents.core.orchestrator import SupervisorOrchestrator


def main() -> None:
    orch = SupervisorOrchestrator()
    out = ROOT / "data"
    out.mkdir(exist_ok=True)

    print("=== Agent roster ===")
    for a in orch.roster():
        print(f"  • {a['name']:28} {a['description']}")
        print(f"      tools: {a['tools']}")

    print("\n=== Named workflows ===")
    for name, plan in orch.list_workflows().items():
        print(f"  • {name}: {' → '.join(plan)}")

    print("\n=== Run: full_voyage_lifecycle ===")
    state = orch.run_workflow(
        "full_voyage_lifecycle",
        email="ops@smartshiphub.com",
        password="demo",
        company="orion",
        departure="Singapore",
        destination="Hong Kong",
    )
    path = orch.save_state(state, out / "full_voyage_lifecycle.json")

    print(f"\nvoyage={state.voyage_number} vessel={state.vessel_name}")
    print(f"optimized={list(state.optimized_routes)}")
    print(f"hard_regions={len(state.hard_regions)} storms={len(state.storms)}")
    print(f"alerts_issued={len(state.alerts)} cii={state.cii.get('rating')} savings={state.eov.get('savingsMt')}")
    print("\n=== agent log ===")
    for line in state.log:
        print(" ", line)
    print(f"\nState → {path}")

    print("\n=== Run: goal routing ('re-route around the storm') ===")
    gstate = orch.run_goal("re-route around the storm and alert the fleet")
    orch.save_state(gstate, out / "storm_goal.json")
    print("plan resolved →", [l for l in gstate.log if l.startswith("[Supervisor] workflow=")][0])
    print("DONE")


if __name__ == "__main__":
    main()
