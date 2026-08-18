#!/usr/bin/env python3
"""Run one VoyagePM microservice (ingest | noon | weather | routeopt | storm | report_sender)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vpm_agents.runtime import SERVICES  # noqa: E402


def main() -> None:
    names = ", ".join(SERVICES)
    p = argparse.ArgumentParser(description="VoyagePM microservice entrypoint")
    p.add_argument(
        "service",
        nargs="?",
        default=os.getenv("VPM_SERVICE", ""),
        help=f"Service name ({names}) or set VPM_SERVICE",
    )
    args = p.parse_args()
    name = (args.service or "").strip().lower().replace("-", "_")
    fn = SERVICES.get(name)
    if not fn:
        print(f"unknown service {args.service!r}. choose: {names}", file=sys.stderr)
        sys.exit(2)
    print(f"VPM service={name} starting", flush=True)
    fn()


if __name__ == "__main__":
    main()
