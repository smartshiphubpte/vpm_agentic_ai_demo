#!/usr/bin/env python3
"""CLI: run a named workflow or a free-form goal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vpm_agents.core.orchestrator import WORKFLOWS, SupervisorOrchestrator


def main() -> None:
    p = argparse.ArgumentParser(description="VoyagePM Agentic Framework CLI")
    p.add_argument("--workflow", choices=list(WORKFLOWS), help="Named workflow")
    p.add_argument("--goal", help="Free-form goal (keyword or LLM planned)")
    p.add_argument("--company", default="orion")
    p.add_argument("--email", default="ops@smartshiphub.com")
    p.add_argument("--password", default="demo")
    p.add_argument("--departure", default=None, help="Override VoyageAgent.md default port")
    p.add_argument("--destination", default=None, help="Override VoyageAgent.md default port")
    p.add_argument("--out", default=str(ROOT / "data" / "cli_run.json"))
    p.add_argument("--list", action="store_true", help="List agents and workflows")
    args = p.parse_args()

    orch = SupervisorOrchestrator()
    if args.list:
        print("Agents:")
        for a in orch.roster():
            print(f"  {a['name']}: {a['description']}")
        print("Workflows:")
        for n, plan in orch.list_workflows().items():
            print(f"  {n}: {' → '.join(plan)}")
        return

    if not args.workflow and not args.goal:
        p.error("provide --workflow or --goal (or --list)")

    kwargs = dict(
        email=args.email,
        password=args.password,
        company=args.company,
        departure=args.departure,
        destination=args.destination,
    )
    if args.workflow:
        state = orch.run_workflow(args.workflow, **kwargs)
    else:
        state = orch.run_goal(args.goal, **kwargs)

    path = orch.save_state(state, args.out)
    print(json.dumps({"voyage": state.voyage_number, "phase": state.phase, "path": str(path)}, indent=2))
    for line in state.log:
        print(line)


if __name__ == "__main__":
    main()
