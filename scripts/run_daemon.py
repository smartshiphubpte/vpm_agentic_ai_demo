"""Always-on continuous ops loop with tagged flows."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vpm_agents.agents.continuous import (
    InboxWatchAgent,
    NoonExcelWatchAgent,
    StormWatchAgent,
    WeatherReportAgent,
)
from vpm_agents.config import settings
from vpm_agents.core.daemon_flows import (
    DEFAULT_DAEMON_FLOW,
    chain_steps,
    flow_weather_poll,
    get_flow,
    list_daemon_flows,
)
from vpm_agents.core.state import SessionState
from vpm_agents.tools import get_backend
from vpm_agents.tools.voyage_registry import VoyageRegistry


def _ensure_dirs() -> None:
    for d in (
        settings.inbox_dir,
        settings.inbox_dir / "processed",
        settings.inbox_dir / "failed",
        settings.noon_inbox_dir,
        settings.noon_inbox_dir / "processed",
        settings.noon_inbox_dir / "failed",
        settings.storm_out_dir,
        settings.reports_out_dir,
        settings.templates_dir,
        settings.data_dir,
        settings.weather_out_dir,
    ):
        Path(d).mkdir(parents=True, exist_ok=True)


def run_once(
    storm: bool = True,
    inbox: bool = True,
    noon: bool = True,
    flow_name: str | None = None,
) -> SessionState:
    _ensure_dirs()
    flow_name = flow_name or settings.daemon_flow
    flow = get_flow(flow_name)
    backend = get_backend()
    registry = VoyageRegistry()
    state = SessionState()
    if inbox:
        state = InboxWatchAgent(backend, registry, flow_name=flow_name).run(state)
        if flow_weather_poll(flow):
            state = WeatherReportAgent(backend, registry).run(state)
    if noon and flow.get("noon_poll", False):
        state = NoonExcelWatchAgent(backend, registry).run(state)
    if storm and flow.get("storm_poll", False):
        state = StormWatchAgent(backend, registry).run(state)
    return state


def run_forever(flow_name: str | None = None) -> None:
    _ensure_dirs()
    flow_name = flow_name or settings.daemon_flow
    flow = get_flow(flow_name)
    backend = get_backend()
    registry = VoyageRegistry()
    inbox_agent = InboxWatchAgent(backend, registry, flow_name=flow_name)
    noon_agent = NoonExcelWatchAgent(backend, registry)
    storm_agent = StormWatchAgent(backend, registry)
    weather_agent = WeatherReportAgent(backend, registry)

    storm_interval = max(1.0, settings.storm_interval_hours * 3600.0)
    inbox_poll = max(1.0, settings.inbox_poll_seconds)
    noon_poll = max(1.0, settings.noon_poll_seconds)
    next_storm = 0.0
    next_noon = 0.0
    weather_poll = flow_weather_poll(flow)

    print("VPM continuous daemon started", flush=True)
    print(f"  flow        = {flow_name} — {flow.get('description', '')}", flush=True)
    print(f"  prevoyage   = {chain_steps(flow_name)} (boundary only — daemon keeps running)", flush=True)
    print(f"  inbox       = {settings.inbox_dir}", flush=True)
    print(f"  noon inbox  = {settings.noon_inbox_dir}", flush=True)
    print(f"  weather poll= {weather_poll} (delay {settings.weather_report_delay_minutes}m)", flush=True)
    print(f"  noon poll   = {flow.get('noon_poll', False)} (every {noon_poll}s)", flush=True)
    print(f"  storm poll  = {flow.get('storm_poll', False)} (every {settings.storm_interval_hours}h)", flush=True)
    print(f"  reports out = {settings.reports_out_dir}", flush=True)
    print(f"  weather out = {settings.weather_out_dir}  (→ {{voyage}}/weather_report_*.pdf)", flush=True)
    print(
        f"  eov on arrive= {settings.eov_on_arrival}  (background; map={settings.voyage_map_url or 'OSM.de tiles=GUI'})",
        flush=True,
    )
    dest = (settings.report_email or "").strip() or "(unset — PDFs not emailed)"
    print(
        f"  report email= {dest}  source={settings.report_email_source}"
        + ("" if settings.smtp_host else "  (set VPM_SMTP_HOST to send)"),
        flush=True,
    )
    print("  notes print live when an agent starts/finishes (idle inbox polls are quiet)", flush=True)

    last_hb = 0.0
    while True:
        now = time.monotonic()
        state = SessionState()
        try:
            state = inbox_agent.run(state)
        except Exception as e:
            print(f"[{datetime.now(timezone.utc).isoformat()}] inbox error: {e}", flush=True)

        if weather_poll:
            try:
                state = weather_agent.run(state)
            except Exception as e:
                print(f"[{datetime.now(timezone.utc).isoformat()}] weather error: {e}", flush=True)

        if flow.get("noon_poll", False) and now >= next_noon:
            try:
                state = noon_agent.run(state)
            except Exception as e:
                print(f"[{datetime.now(timezone.utc).isoformat()}] noon error: {e}", flush=True)
            next_noon = now + noon_poll
        elif not flow.get("noon_poll", False):
            next_noon = now + noon_poll

        if flow.get("storm_poll", False) and now >= next_storm:
            try:
                state = storm_agent.run(state)
            except Exception as e:
                print(f"[{datetime.now(timezone.utc).isoformat()}] storm error: {e}", flush=True)
            next_storm = now + storm_interval

        if now - last_hb >= 30.0:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
            print(f"[{ts}] [Daemon] alive flow={flow_name} idle-poll={inbox_poll}s", flush=True)
            last_hb = now

        time.sleep(inbox_poll)


def main() -> None:
    p = argparse.ArgumentParser(description="VoyagePM continuous daemon (tagged flows)")
    p.add_argument("--once", action="store_true")
    p.add_argument("--flow", default=None, help=f"Flow tag (default: {DEFAULT_DAEMON_FLOW})")
    p.add_argument("--list-flows", action="store_true", help="List flow tags and exit")
    p.add_argument("--inbox-only", action="store_true")
    p.add_argument("--noon-only", action="store_true")
    p.add_argument("--storm-only", action="store_true")
    args = p.parse_args()

    if args.list_flows:
        print("Daemon flows:")
        for name, desc in list_daemon_flows().items():
            marker = " (default)" if name == (args.flow or settings.daemon_flow or DEFAULT_DAEMON_FLOW) else ""
            print(f"  {name}{marker}: {desc}")
        return

    flow = args.flow or settings.daemon_flow

    if args.once or args.inbox_only or args.noon_only or args.storm_only:
        state = run_once(
            storm=not args.inbox_only and not args.noon_only,
            inbox=not args.storm_only and not args.noon_only,
            noon=not args.inbox_only and not args.storm_only,
            flow_name=flow,
        )
        for line in state.log:
            print(line)
        return

    run_forever(flow_name=flow)


if __name__ == "__main__":
    main()
