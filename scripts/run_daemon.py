"""Always-on continuous ops loop with tagged flows."""

from __future__ import annotations

import argparse
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from inbox_agent.watch import InboxWatchAgent, MailInboxAgent
from vpm_agents.agents.continuous import (
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
from vpm_agents.tools.daemon_jobs import queue_stats
from vpm_agents.tools.folder_layout import ensure_drop_dirs
from vpm_agents.tools.voyage_registry import VoyageRegistry


def _ensure_dirs() -> None:
    for d in (
        settings.inbox_dir,
        settings.noon_inbox_dir,
        settings.storm_out_dir,
        settings.reports_out_dir,
        settings.templates_dir,
        settings.data_dir,
        settings.weather_out_dir,
        settings.jobs_dir,
    ):
        ensure_drop_dirs(Path(d))


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


def _poll_loop(
    name: str,
    interval_s: float,
    stop: threading.Event,
    tick: Callable[[], None],
) -> None:
    """Independent timer — discovery never waits on other agents' heavy work."""
    while not stop.is_set():
        try:
            tick()
        except Exception as e:
            print(f"[{datetime.now(timezone.utc).isoformat()}] {name} error: {e}", flush=True)
        stop.wait(interval_s)


def run_forever(flow_name: str | None = None) -> None:
    _ensure_dirs()
    flow_name = flow_name or settings.daemon_flow
    flow = get_flow(flow_name)
    backend = get_backend()
    registry = VoyageRegistry()
    inbox_agent = InboxWatchAgent(backend, registry, flow_name=flow_name)
    mail_agent = MailInboxAgent(backend, registry)
    noon_agent = NoonExcelWatchAgent(backend, registry)
    storm_agent = StormWatchAgent(backend, registry)
    weather_agent = WeatherReportAgent(backend, registry)

    storm_interval = max(1.0, settings.storm_interval_hours * 3600.0)
    inbox_poll = max(1.0, settings.inbox_poll_seconds)
    mail_poll = max(1.0, settings.mail_poll_seconds)
    noon_poll = max(1.0, settings.noon_poll_seconds)
    weather_poll = flow_weather_poll(flow)
    # Delayed-weather poller cadence: same as inbox (cheap when nothing due)
    weather_interval = inbox_poll

    print("VPM continuous daemon started", flush=True)
    print(f"  flow        = {flow_name} — {flow.get('description', '')}", flush=True)
    print(f"  prevoyage   = {chain_steps(flow_name)} (boundary only — daemon keeps running)", flush=True)
    print(
        f"  mode        = parallel pollers + ingest/heavy queues "
        f"(ingest_workers={settings.daemon_ingest_workers} heavy_workers={settings.daemon_workers})",
        flush=True,
    )
    print(f"  inbox       = {settings.inbox_dir}", flush=True)
    print(f"  mail poll   = {mail_poll}s IMAP", flush=True)
    print(f"  noon inbox  = {settings.noon_inbox_dir}", flush=True)
    print(f"  weather poll= {weather_poll} (delay {settings.weather_report_delay_minutes}m)", flush=True)
    print(f"  noon poll   = {flow.get('noon_poll', False)} (every {noon_poll}s)", flush=True)
    print(
        f"  storm poll  = {flow.get('storm_poll', False)} "
        f"(every {settings.storm_interval_hours}h, own thread, last snapshot shared)",
        flush=True,
    )
    print(f"  reports out = {settings.reports_out_dir}", flush=True)
    print(f"  weather out = {settings.weather_out_dir}  (→ {{voyage}}/weather_report_*.pdf)", flush=True)
    print(
        f"  eov on arrive= {settings.eov_on_arrival}  (background; map={settings.voyage_map_url or 'OSM.de tiles=GUI'})",
        flush=True,
    )
    print(
        f"  jobs dir    = {settings.jobs_dir}  (routeopt claimed by routeopt poller/container)",
        flush=True,
    )
    print(
        f"  report send = separate service (scripts/run_service.py report_sender)"
        f"  inbox={settings.reports_out_dir}",
        flush=True,
    )
    print("  notes print live when an agent starts/finishes (idle inbox polls are quiet)", flush=True)

    stop = threading.Event()
    threads: list[threading.Thread] = []

    def _inbox_tick() -> None:
        inbox_agent.run(SessionState(), enqueue=True)

    def _mail_tick() -> None:
        mail_agent.run(SessionState())

    def _noon_tick() -> None:
        noon_agent.run(SessionState(), enqueue=True)

    def _weather_tick() -> None:
        weather_agent.run(SessionState(), enqueue=True)

    def _routeopt_tick() -> None:
        from vpm_agents.runtime import drain_routeopt_once

        drain_routeopt_once(backend, registry)

    def _storm_tick() -> None:
        # Own poller thread — never queued behind ingest/route-opt.
        storm_agent.run(SessionState())

    threads.append(
        threading.Thread(
            target=_poll_loop,
            args=("inbox", inbox_poll, stop, _inbox_tick),
            name="poll-inbox",
            daemon=True,
        )
    )
    threads.append(
        threading.Thread(
            target=_poll_loop,
            args=("mail", mail_poll, stop, _mail_tick),
            name="poll-mail",
            daemon=True,
        )
    )
    threads.append(
        threading.Thread(
            target=_poll_loop,
            args=("routeopt", 2.0, stop, _routeopt_tick),
            name="poll-routeopt",
            daemon=True,
        )
    )
    if weather_poll:
        threads.append(
            threading.Thread(
                target=_poll_loop,
                args=("weather", weather_interval, stop, _weather_tick),
                name="poll-weather",
                daemon=True,
            )
        )
    if flow.get("noon_poll", False):
        threads.append(
            threading.Thread(
                target=_poll_loop,
                args=("noon", noon_poll, stop, _noon_tick),
                name="poll-noon",
                daemon=True,
            )
        )
    if flow.get("storm_poll", False):
        threads.append(
            threading.Thread(
                target=_poll_loop,
                args=("storm", storm_interval, stop, _storm_tick),
                name="poll-storm",
                daemon=True,
            )
        )

    for t in threads:
        t.start()

    try:
        while True:
            st = queue_stats()
            ts = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
            ing = st["lanes"]["ingest"]
            hvy = st["lanes"]["heavy"]
            print(
                f"[{ts}] [Daemon] alive flow={flow_name} "
                f"ingest={ing['queued_n']}q/{ing['inflight_n']}run "
                f"heavy={hvy['queued_n']}q/{hvy['inflight_n']}run "
                f"pollers={','.join(t.name.replace('poll-', '') for t in threads)}",
                flush=True,
            )
            time.sleep(30.0)
    except KeyboardInterrupt:
        print("Daemon stopping…", flush=True)
        stop.set()
        for t in threads:
            t.join(timeout=2.0)


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
