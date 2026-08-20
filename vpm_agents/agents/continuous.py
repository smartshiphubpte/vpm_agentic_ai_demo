"""Continuous / folder-driven agents: pre-voyage ingest, noon ops, storm watch."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vpm_agents.config import settings
from vpm_agents.core.base import Agent, Tool, ToolResult
from vpm_agents.core.state import SessionState
from vpm_agents.tools.geo import remaining_route, six_hour_waypoints
from vpm_agents.tools.folder_layout import (
    PRE_VOYAGE_REPORT,
    VPA_REPORT,
    WEATHER_REPORT,
    voyage_report_dir,
    voyage_root,
)
from vpm_agents.tools.route_weather import build_voyage_track, format_track_block
from vpm_agents.tools.storm_cache import last_storms, last_storms_fetched_at, remember_storms
from vpm_agents.tools.storm_normalize import normalize_active_storms
from vpm_agents.tools.storm_proximity import assess_all_voyages
from vpm_agents.tools.templates import fill_template, format_waypoints, write_report
from vpm_agents.tools.voyage_registry import VoyageRegistry, compact_voyage_number, voyage_is_closed
from vpm_agents.tools.storm_report import write_storm_voyage_reports
from vpm_agents.tools.weather_report import (
    extract_bad_weather_events,
    format_bad_weather_block,
    write_weather_report,
    annotate_track_hard,
)
from vpm_agents.tools.eov_jobs import submit_eov_report
from vpm_agents.tools.agent_log import progress


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return _utc_now().strftime("%Y%m%dT%H%M%SZ")


def _compact_wx_points(wx: Any) -> list[dict[str, Any]]:
    pts = wx if isinstance(wx, list) else (wx or {}).get("points") or []
    out: list[dict[str, Any]] = []
    for p in pts:
        row = {
            k: p.get(k)
            for k in ("windKn", "waveM", "swellM", "pressureHpa")
            if isinstance(p, dict) and p.get(k) is not None
        }
        if row:
            out.append(row)
    return out


def _enqueue_suggested_routes(
    voy_no: str,
    rec: dict[str, Any],
    routes: dict[str, Any],
    suggested_id: str | None,
    trigger: str,
    state: SessionState,
) -> None:
    """Hand off 4 alts to prevoyage_db → shipping_db.suggested_routes."""
    tenant = (settings.tenant or "").strip().lower()
    if not tenant or not routes:
        return
    from vpm_agents.tools import job_bus

    compact = []
    for rid, r in routes.items():
        wps = (r.get("route") or {}).get("waypoints") or r.get("waypoints") or []
        compact.append(
            {
                "id": rid,
                "waypoints": wps,
                "six_hour_plan": r.get("six_hour_plan") or [],
                "voyage": r.get("voyage") or {},
                "weather_points": _compact_wx_points(r.get("weather")),
            }
        )
    key = f"suggested_routes:{tenant}:{voy_no}:{trigger}"
    if job_bus.enqueue(
        key,
        {
            "kind": "suggested_routes",
            "tenant": tenant,
            "voyage_number": voy_no,
            "record": {
                "voyage_number": voy_no,
                "vessel_name": rec.get("vessel_name") or "",
                "vessel_id": rec.get("vessel_id") or "",
                "cp_speed_kn": rec.get("cp_speed_kn"),
                "cp_consumption_mt_day": rec.get("cp_consumption_mt_day"),
                "etd": rec.get("etd") or (rec.get("last_noon") or {}).get("observed_at"),
                "suggested_id": suggested_id or "safest",
                "trigger": trigger,
                "routes": compact,
            },
        },
        root=settings.jobs_dir,
    ):
        state.note("PreVoyageRouteOptimizeAgent", f"queued {key} for suggested_routes")

def _auth_token(backend: Any) -> str:
    login = backend.login(settings.email, settings.password)
    token = login["token"]
    if settings.company and settings.company != login.get("company"):
        backend.set_company(token, settings.company)
    return token


def _schedule_passage_weather(registry: VoyageRegistry, voyage_number: str, plan_key: str) -> None:
    """Queue the next delayed passage weather report (post-departure only)."""
    if not settings.weather_report_on_noon:
        return
    delay = max(0.0, settings.weather_report_delay_minutes)
    due = _utc_now() + __import__("datetime").timedelta(minutes=delay)
    registry.upsert(
        voyage_number,
        {"weather_due_at": due.isoformat(), "weather_plan_key": plan_key},
    )


def _voyage_subreports_dir(voyage_number: str) -> Path:
    """All route-analysis outputs live under .../{vessel}/{voyage}/vpa/."""
    rec = VoyageRegistry().get(voyage_number) or {}
    d = voyage_report_dir(
        settings.reports_out_dir,
        str(rec.get("vessel_id") or ""),
        voyage_number,
        VPA_REPORT,
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fetch_active_storms(backend: Any, token: str) -> list[dict[str, Any]]:
    """Load active storms + progressions from configured source (live NHC/JTWC by default)."""
    source = (settings.storm_source or "live").strip().lower()

    if source == "live":
        from vpm_agents.tools.storm_live_api import fetch_live_storms

        return normalize_active_storms(fetch_live_storms())

    if source == "mock":
        raw = backend.storm_registry(token) if hasattr(backend, "storm_registry") else []
        if hasattr(backend, "storm_map_layer"):
            try:
                raw = backend.storm_map_layer(token)
            except Exception:
                pass
        return normalize_active_storms(raw)

    # backend (voyagepm_be map-layer)
    raw: Any = None
    if hasattr(backend, "storm_map_layer"):
        try:
            raw = backend.storm_map_layer(token)
        except Exception:
            raw = None
    if raw is None and hasattr(backend, "storm_registry"):
        raw = backend.storm_registry(token)
    return normalize_active_storms(raw)


def _write_combined(
    voyage_dir: Path,
    voyage_number: str,
    plan: list[dict],
    wx: dict[str, Any],
    *,
    noon: dict | None = None,
    vessel_name: str = "",
    prefix: str = "voyage_track",
) -> Path:
    combined = build_voyage_track(
        voyage_number, plan, wx, noon=noon, vessel_name=vessel_name, provider=wx.get("provider", "")
    )
    annotate_track_hard(combined)
    path = voyage_dir / f"{prefix}_{_stamp()}.json"
    path.write_text(json.dumps(combined, indent=2, default=str), encoding="utf-8")
    return path


def _run_immediate_weather(
    backend: Any,
    registry: VoyageRegistry,
    state: SessionState,
    voy_no: str,
    plan_key: str = "six_hour_plan",
) -> SessionState:
    """Fetch weather now, write combined track + weather report (no delay)."""
    rec = registry.get(voy_no) or {}
    plan = rec.get(plan_key) or []
    if not plan:
        state.note("Weather", f"{voy_no} missing {plan_key}")
        return state
    state.note("Weather", f"{voy_no} fetching along {len(plan)} waypoints ({plan_key})")
    t0 = time.monotonic()
    try:
        token = _auth_token(backend)
        pts = [{"lat": p["lat"], "lon": p["lon"]} for p in plan]
        wx = backend.weather_along_route(token, pts)
        vessel_id = str(rec.get("vessel_id") or "")
        voyage_dir = voyage_root(settings.reports_out_dir, vessel_id, voy_no)
        voyage_dir.mkdir(parents=True, exist_ok=True)
        combined_path = _write_combined(
            voyage_report_dir(
                settings.reports_out_dir,
                vessel_id,
                voy_no,
                WEATHER_REPORT,
            ),
            voy_no,
            plan,
            wx,
            vessel_name=rec.get("vessel_name", ""),
            prefix="voyage_track_weather",
        )
        track_data = json.loads(combined_path.read_text())
        registry.upsert(
            voy_no,
            {
                "last_voyage_track": str(combined_path),
                "weather_summary": {
                    "pointCount": len(wx.get("points", [])),
                    "hardCount": len(wx.get("hardRegions", [])),
                    "provider": wx.get("provider"),
                },
                "weather_due_at": None,
            },
        )
        state.artifacts["voyage_track_weather"] = str(combined_path)
        state.note(
            "Weather",
            f"{voy_no} track+wx → {combined_path}",
            elapsed_s=time.monotonic() - t0,
        )
    except Exception as e:
        state.note("Weather", f"{voy_no} failed: {e}", elapsed_s=time.monotonic() - t0)
    return state


class NoonOpsAgent(Agent):
    """Noon position → 7-day 6h plan + weather overlay in one JSON."""

    name = "NoonOpsAgent"

    def __init__(self, backend: Any, registry: VoyageRegistry | None = None):
        super().__init__(backend)
        self.registry = registry or VoyageRegistry()

    def build_tools(self) -> list[Tool]:
        return [Tool("parse_noon", "Parse noon report file", self._parse)]

    def _parse(self, path: str) -> ToolResult:
        from inbox_agent.parse import parse_noon_report

        return ToolResult(ok=True, data=parse_noon_report(path))

    def run(
        self,
        state: SessionState,
        path: str | Path | None = None,
        noon: dict[str, Any] | None = None,
        *,
        enqueue_route_opt: bool = False,
    ) -> SessionState:
        if noon is None and path:
            try:
                from inbox_agent.parse import parse_noon_report

                noon = parse_noon_report(path)
            except Exception as e:
                state.note(self.name, f"failed {Path(path).name}: {e}")
                return state
        if not noon:
            state.note(self.name, "no noon data — skip")
            return state
        return self._process_noon(state, noon, path, enqueue_route_opt=enqueue_route_opt)

    def _process_noon(
        self,
        state: SessionState,
        noon: dict[str, Any],
        path: str | Path | None = None,
        *,
        enqueue_route_opt: bool = False,
    ) -> SessionState:
        from vpm_agents.tools.noon_io import is_arrival_report, is_departure_report, voyage_has_departed

        try:
            voy, registry_key = self.registry.find_voyage(noon["voyage_number"])
            if not voy:
                raise ValueError(
                    f"no pre-voyage registry for {noon['voyage_number']} — ingest pre-voyage first"
                )
            if voyage_is_closed(voy) and not is_arrival_report(noon.get("report_type")):
                if noon.get("noon_id"):
                    self.registry.mark_noon_processed(noon["noon_id"])
                state.note(
                    self.name,
                    f"{registry_key} skip noon — voyage already closed (eov={voy.get('eov_status')})",
                )
                return state
            master = voy["master_waypoints"]
            speed = float(noon.get("avg_speed_kn") or voy["cp_speed_kn"])
            start = _utc_now()
            if noon.get("observed_at"):
                try:
                    start = datetime.fromisoformat(str(noon["observed_at"]).replace("Z", "+00:00"))
                except ValueError:
                    pass
            remaining = remaining_route(master, noon["lat"], noon["lon"])
            if len(remaining) < 2:
                remaining = master[-2:] if len(master) >= 2 else master
            plan = six_hour_waypoints(
                remaining,
                speed,
                start=start,
                horizon_hours=settings.noon_horizon_hours,
                interval_h=settings.waypoint_interval_hours,
            )

            token = _auth_token(self.backend)
            pts = [{"lat": p["lat"], "lon": p["lon"]} for p in plan]
            wx = self.backend.weather_along_route(token, pts)

            vessel_id = str(voy.get("vessel_id") or "")
            voyage_dir = voyage_root(settings.reports_out_dir, vessel_id, registry_key)
            voyage_dir.mkdir(parents=True, exist_ok=True)
            combined_path = _write_combined(
                voyage_report_dir(
                    settings.reports_out_dir,
                    vessel_id,
                    registry_key,
                    WEATHER_REPORT,
                ),
                registry_key,
                plan,
                wx,
                noon=noon,
                vessel_name=voy.get("vessel_name") or noon.get("vessel_name", ""),
                prefix="voyage_track_weather",
            )
            track_data = json.loads(combined_path.read_text())

            departure = is_departure_report(noon.get("report_type"))
            has_departed = departure or voyage_has_departed(voy)
            weather_txt = weather_json = None
            if settings.weather_report_on_noon and has_departed:
                weather_txt, weather_json = write_weather_report(
                    registry_key,
                    track_data,
                    voyage_rec=voy,
                    vessel_id=voy.get("vessel_id", ""),
                    vessel_name=voy.get("vessel_name") or noon.get("vessel_name", ""),
                    plan_label="7-day noon track",
                )

            track_block = format_track_block(track_data)
            bad_block = format_bad_weather_block(extract_bad_weather_events(track_data))
            ctx = {
                "voyage_number": registry_key,
                "vessel_id": voy.get("vessel_id", ""),
                "source_port": voy.get("source_port", ""),
                "dest_port": voy.get("dest_port", ""),
                "cp_speed_kn": speed,
                "lat": noon["lat"],
                "lon": noon["lon"],
                "observed_at": noon.get("observed_at") or start.isoformat(),
                "generated_at": _utc_now().isoformat(),
                "waypoint_block": track_block,
                "weather_block": track_block,
                "hard_block": bad_block,
            }
            report_path = write_report(
                voyage_report_dir(
                    settings.reports_out_dir,
                    vessel_id,
                    registry_key,
                    VPA_REPORT,
                ),
                f"noon_7day_report_{_stamp()}.txt",
                fill_template("noon_7day_report.txt", ctx),
            )

            registry_patch: dict[str, Any] = {
                    "last_noon": noon,
                    "noon_seven_day_plan": plan,
                    "last_voyage_track": str(combined_path),
                    "last_weather_report": str(weather_txt) if weather_txt else voy.get("last_weather_report"),
                    "last_bad_weather_json": str(weather_json) if weather_json else voy.get("last_bad_weather_json"),
                    "noon_updated_at": _utc_now().isoformat(),
                    "noon_history": _append_noon_history(voy, noon),
                }
            if departure:
                registry_patch["passage_weather_active"] = True
            self.registry.upsert(registry_key, registry_patch)
            if departure and settings.weather_report_on_noon:
                _schedule_passage_weather(self.registry, registry_key, "noon_seven_day_plan")
            if noon.get("noon_id"):
                self.registry.mark_noon_processed(noon["noon_id"])

            state.voyage_number = registry_key
            state.artifacts["noon"] = noon
            state.artifacts["voyage_track_weather"] = str(combined_path)
            state.phase = self.spec.get("phase", "noon_reported")
            state.note(
                self.name,
                f"{registry_key} noon@{noon['lat']:.4f},{noon['lon']:.4f} "
                f"plan={len(plan)} track+wx → {combined_path.name}"
                + (f" weather → {weather_txt.name}" if weather_txt else ""),
            )

            # Arrival → EOV in background (does not block noon route alts or daemon poll)
            arrival = is_arrival_report(noon.get("report_type"))
            if settings.eov_on_arrival and arrival:
                _schedule_eov(self.backend, self.registry, registry_key)
                state.note(self.name, f"{registry_key} arrival → EOV queued (background)")

            # Route-opt is a separate service — drop a job file, do not wait.
            nid = noon.get("noon_id") or "latest"
            remaining_wps = remaining
            speed_kn = speed
            voy = registry_key
            if arrival or voyage_is_closed(self.registry.get(voy)):
                state.note(self.name, f"{voy} skip noon route-opt — voyage closing/closed")
            elif enqueue_route_opt:
                from vpm_agents.tools import job_bus

                key = f"routeopt:{voy}:noon:{nid}"
                if job_bus.enqueue(
                    key,
                    {
                        "kind": "routeopt",
                        "voyage_number": voy,
                        "trigger": "noon",
                        "noon_id": nid,
                        "speed_kn": speed_kn,
                        "waypoints": remaining_wps,
                    },
                ):
                    state.note(self.name, f"{voy} noon route alts queued")
            else:
                try:
                    opt_agent = PreVoyageRouteOptimizeAgent(self.backend, self.registry)
                    state = opt_agent.run(
                        state,
                        voyage_number=voy,
                        waypoints=remaining_wps,
                        speed_kn=speed_kn,
                        trigger="noon",
                    )
                except Exception as e:
                    state.note(self.name, f"{voy} noon route alts failed: {e}")

            if path and Path(path).exists():
                try:
                    from inbox_agent.parse import archive_inbox_file

                    archive_inbox_file(path)
                except Exception:
                    pass
        except Exception as e:
            state.note(self.name, f"failed: {e}")
            if path and Path(path).exists():
                try:
                    from inbox_agent.parse import archive_inbox_file

                    archive_inbox_file(path, "failed")
                except Exception:
                    pass
        return state


def _append_noon_history(voy: dict[str, Any], noon: dict[str, Any]) -> list[dict[str, Any]]:
    hist = list(voy.get("noon_history") or [])
    nid = noon.get("noon_id")
    if nid and any(h.get("noon_id") == nid for h in hist):
        return hist
    hist.append(
        {
            "noon_id": nid,
            "observed_at": noon.get("observed_at"),
            "report_type": noon.get("report_type"),
            "lat": noon.get("lat"),
            "lon": noon.get("lon"),
            "avg_speed_kn": noon.get("avg_speed_kn"),
            "vessel_name": noon.get("vessel_name"),
            "eov_row": noon.get("eov_row"),
            "source_file": noon.get("source_file"),
        }
    )
    return hist


def _schedule_eov(backend: Any, registry: VoyageRegistry, voyage_number: str) -> None:
    def _job() -> Any:
        from vpm_agents.tools.eov_report import build_end_of_voyage_report

        try:
            token = _auth_token(backend)
        except Exception:
            token = ""
        return build_end_of_voyage_report(
            backend=backend,
            registry=VoyageRegistry(registry.path),
            voyage_number=voyage_number,
            token=token,
        )

    fut = submit_eov_report(voyage_number, _job)
    if fut is None:
        progress("NoonOpsAgent", f"{voyage_number} EOV already in flight")


class EndOfVoyageReportAgent(Agent):
    """Arrival-triggered end-of-voyage report (also callable synchronously for demos)."""

    name = "EndOfVoyageReportAgent"

    def __init__(self, backend: Any, registry: VoyageRegistry | None = None):
        self.registry = registry or VoyageRegistry()
        super().__init__(backend)

    def run(
        self,
        state: SessionState,
        voyage_number: str | None = None,
        *,
        background: bool = False,
    ) -> SessionState:
        voy = voyage_number or state.voyage_number
        if not voy:
            state.note(self.name, "no voyage — skip")
            return state
        if background:
            _schedule_eov(self.backend, self.registry, voy)
            state.note(self.name, f"{voy} queued background")
            state.phase = self.spec.get("phase", "eov_queued")
            return state
        t0 = time.monotonic()
        try:
            from vpm_agents.tools.eov_report import build_end_of_voyage_report

            token = _auth_token(self.backend)
            result = build_end_of_voyage_report(
                backend=self.backend,
                registry=self.registry,
                voyage_number=voy,
                token=token,
            )
            state.artifacts["eov_report"] = result
            state.phase = self.spec.get("phase", "eov_reported")
            state.note(
                self.name,
                f"{voy} → {result.get('path')}",
                elapsed_s=time.monotonic() - t0,
            )
        except Exception as e:
            state.note(self.name, f"{voy} failed: {e}", elapsed_s=time.monotonic() - t0)
        return state


class NoonExcelWatchAgent(Agent):
    """Poll combined noon Excel (or DB stub) on VPM_NOON_POLL_SECONDS interval."""

    name = "NoonExcelWatchAgent"

    def __init__(
        self,
        backend: Any,
        registry: VoyageRegistry | None = None,
        noon_agent: NoonOpsAgent | None = None,
    ):
        self.registry = registry or VoyageRegistry()
        self.noon_agent = noon_agent or NoonOpsAgent(backend, self.registry)
        super().__init__(backend)

    def _process_row(self, row: dict[str, Any]) -> SessionState:
        state = SessionState()
        payload = {k: v for k, v in row.items() if k != "_drop_path"}
        state.note(self.name, f"noon row {payload.get('noon_id')} voy={payload.get('voyage_number')}")
        state = self.noon_agent.run(state, noon=payload, enqueue_route_opt=True)
        from vpm_agents.tools.noon_source import archive_finished_drops

        archive_finished_drops([row], self.registry)
        return state

    def run(self, state: SessionState, *, enqueue: bool = False) -> SessionState:
        from vpm_agents.tools.noon_io import sort_noon_rows
        from vpm_agents.tools.noon_source import get_noon_sources

        sources = get_noon_sources()
        rows: list[dict] = []
        for src in sources:
            rows.extend(src.fetch_new(self.registry))
        if not rows:
            state.note(self.name, "no new noon rows", quiet=True)
            return state

        # One row at a time, oldest observed_at first — do not skip ahead of a held row.
        row = sort_noon_rows(rows)[0]
        voy = row.get("voyage_number") or "?"
        rec, _ = self.registry.find_voyage(voy)
        if not rec:
            state.note(
                self.name,
                f"holding noon row {row.get('noon_id')} voy={voy} until pre-voyage ingested",
            )
            state.phase = self.spec.get("phase", "noon_excel_polled")
            return state

        from vpm_agents.tools import job_bus

        rec_key = compact_voyage_number(voy)
        if job_bus.has_open(f"routeopt:{rec_key}:"):
            state.note(
                self.name,
                f"{rec_key} waiting — previous route-opt still pending/running",
                quiet=True,
            )
            state.phase = self.spec.get("phase", "noon_excel_polled")
            return state

        if enqueue:
            from vpm_agents.tools.daemon_jobs import LANE_INGEST, lane_has_prefix, submit_job

            if lane_has_prefix(LANE_INGEST, "noon:"):
                state.note(self.name, "noon ingest busy — waiting for previous row", quiet=True)
                state.phase = self.spec.get("phase", "noon_excel_polled")
                return state
            nid = row.get("noon_id") or f"{row.get('voyage_number')}:{row.get('observed_at')}"
            key = f"noon:{nid}"
            fut = submit_job(key, lambda r=row: self._process_row(r), lane=LANE_INGEST)
            if fut is not None:
                state.note(self.name, f"queued {key}")
            state.phase = self.spec.get("phase", "noon_excel_polled")
            return state

        payload = {k: v for k, v in row.items() if k != "_drop_path"}
        state.note(self.name, f"noon row {payload.get('noon_id')} voy={payload.get('voyage_number')}")
        state = self.noon_agent.run(state, noon=payload)
        from vpm_agents.tools.noon_source import archive_finished_drops

        archive_finished_drops([row], self.registry)
        state.phase = self.spec.get("phase", "noon_excel_polled")
        return state


class WeatherReportAgent(Agent):
    """Delayed passage weather — only after a real Departure Report."""

    name = "WeatherReportAgent"

    def __init__(self, backend: Any, registry: VoyageRegistry | None = None):
        super().__init__(backend)
        self.registry = registry or VoyageRegistry()

    def _run_one(self, voy_no: str, rec: dict[str, Any]) -> SessionState:
        state = SessionState()
        t0 = time.monotonic()
        from vpm_agents.tools.noon_io import voyage_has_departed

        if not voyage_has_departed(rec):
            self.registry.upsert(voy_no, {"weather_due_at": None})
            state.note(self.name, f"{voy_no} skip — voyage not departed yet")
            return state
        plan_key = rec.get("weather_plan_key") or (
            "noon_seven_day_plan" if rec.get("noon_seven_day_plan") else "six_hour_plan"
        )
        plan = rec.get(plan_key) or []
        if not plan:
            self.registry.upsert(voy_no, {"weather_due_at": None})
            return state
        try:
            token = _auth_token(self.backend)
            pts = [{"lat": p["lat"], "lon": p["lon"]} for p in plan]
            wx = self.backend.weather_along_route(token, pts)
            vessel_id = str(rec.get("vessel_id") or "")
            voyage_dir = voyage_root(settings.reports_out_dir, vessel_id, voy_no)
            voyage_dir.mkdir(parents=True, exist_ok=True)
            combined_path = _write_combined(
                voyage_report_dir(
                    settings.reports_out_dir,
                    vessel_id,
                    voy_no,
                    WEATHER_REPORT,
                ),
                voy_no,
                plan,
                wx,
                vessel_name=rec.get("vessel_name", ""),
                prefix="voyage_track_weather",
            )
            track = json.loads(combined_path.read_text())
            weather_txt = weather_json = None
            if settings.weather_report_on_noon:
                weather_txt, weather_json = write_weather_report(
                    voy_no,
                    track,
                    voyage_rec=rec,
                    vessel_id=rec.get("vessel_id", ""),
                    vessel_name=rec.get("vessel_name", ""),
                    plan_label="7-day noon track",
                    spec=self.spec,
                )
            self.registry.upsert(
                voy_no,
                {
                    "weather_due_at": None,
                    "last_voyage_track": str(combined_path),
                    "last_weather_report": str(weather_txt) if weather_txt else rec.get("last_weather_report"),
                    "last_bad_weather_json": str(weather_json) if weather_json else rec.get("last_bad_weather_json"),
                },
            )
            state.note(
                self.name,
                f"{voy_no} track+wx → {combined_path}"
                + (f" weather → {weather_txt}" if weather_txt else ""),
                elapsed_s=time.monotonic() - t0,
            )
        except Exception as e:
            state.note(self.name, f"{voy_no} weather failed: {e}", elapsed_s=time.monotonic() - t0)
        return state

    def run(self, state: SessionState, *, enqueue: bool = False) -> SessionState:
        now = _utc_now()
        from vpm_agents.tools.noon_io import voyage_has_departed

        due: list[tuple[str, dict]] = []
        for voy_no, rec in self.registry.all().items():
            raw = rec.get("weather_due_at")
            if not raw or not voyage_has_departed(rec):
                continue
            try:
                due_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if due_at <= now:
                due.append((voy_no, rec))

        if not due:
            return state
        state.note(self.name, f"due weather jobs={len(due)}")

        if enqueue:
            from vpm_agents.tools.daemon_jobs import submit_job

            for voy_no, rec in due:
                key = f"weather:{voy_no}"
                fut = submit_job(key, lambda v=voy_no, r=rec: self._run_one(v, r), lane="heavy")
                if fut is not None:
                    state.note(self.name, f"queued {key}")
            state.phase = self.spec.get("phase", "weather_reported")
            return state

        try:
            _auth_token(self.backend)
        except Exception as e:
            state.note(self.name, f"auth failed: {e}")
            return state

        for voy_no, rec in due:
            st = self._run_one(voy_no, rec)
            state.log.extend(st.log)

        state.phase = self.spec.get("phase", "weather_reported")
        return state


class PreVoyageRouteOptimizeAgent(Agent):
    """4 weather+storm-scored route alternatives — pre-voyage and noon refresh."""

    name = "PreVoyageRouteOptimizeAgent"

    def __init__(self, backend: Any, registry: VoyageRegistry | None = None):
        super().__init__(backend)
        self.registry = registry or VoyageRegistry()

    def build_tools(self) -> list[Tool]:
        return [Tool("optimize_all", "Run 4 objective optimizers", self._opt_all)]

    def _opt_all(
        self,
        voyage_number: str,
        waypoints: list | None = None,
        speed_kn: float | None = None,
        trigger: str = "pre_voyage",
    ) -> ToolResult:
        from vpm_agents.tools.route_optimize import optimize_route_alternatives

        rec = self.registry.get(voyage_number)
        if not rec:
            return ToolResult(ok=False, error="voyage not in registry")
        master = waypoints or rec.get("master_waypoints")
        if not master or len(master) < 2:
            return ToolResult(ok=False, error="need >=2 waypoints")
        token = _auth_token(self.backend)
        storms = last_storms()  # storm poller owns live fetch; we use last snapshot only
        spec = dict(self.spec.defaults)
        spec["full_voyage"] = trigger == "pre_voyage"
        result = optimize_route_alternatives(
            self.backend,
            token,
            master,
            float(speed_kn if speed_kn is not None else rec["cp_speed_kn"]),
            rec.get("weather_summary"),
            spec,
            storms=storms,
            fuel_mt_day=rec.get("cp_consumption_mt_day"),
        )
        return ToolResult(ok=True, data=result)

    def run(
        self,
        state: SessionState,
        voyage_number: str | None = None,
        waypoints: list | None = None,
        speed_kn: float | None = None,
        trigger: str = "pre_voyage",
    ) -> SessionState:
        voy_no = voyage_number or state.voyage_number
        if not voy_no:
            state.note(self.name, "no voyage — skip")
            return state
        rec = self.registry.get(voy_no)
        if not rec:
            state.note(self.name, f"{voy_no} not in registry — skip")
            return state
        master = waypoints or rec.get("master_waypoints")
        if not master or len(master) < 2:
            state.note(self.name, f"{voy_no} no route waypoints — skip")
            return state
        snap_at = last_storms_fetched_at() or "none"
        state.note(
            self.name,
            f"{voy_no} [{trigger}] start — {len(master)} WPs method={settings.route_opt_method} "
            f"algo={settings.route_opt_algo} storms={len(last_storms())} snapshot={snap_at} "
            f"(4 objectives; can take several minutes)",
        )
        t0 = time.monotonic()
        try:
            res = self._opt_all(voy_no, waypoints=master, speed_kn=speed_kn, trigger=trigger)
            if not res.ok:
                raise ValueError(res.error or "optimize failed")
            data = res.data
            routes = data["routes"]
            limits = data["weather_limits"]
            stamp = _stamp()
            sub_dir = _voyage_subreports_dir(voy_no)
            # Index + one JSON per objective
            index_path = sub_dir / f"route_alternatives_{trigger}_{stamp}.json"
            index_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            per_files: dict[str, str] = {}
            for rid, r in routes.items():
                p = sub_dir / f"route_alt_{rid}_{trigger}_{stamp}.json"
                p.write_text(json.dumps(r, indent=2, default=str), encoding="utf-8")
                per_files[rid] = str(p)

            alt_block = data.get("alternatives_block") or "  (none)"
            wp_table = data.get("waypoint_table") or "  (none)"
            buffers = data.get("storm_buffers") or {}
            applied = data.get("weather_limits_applied") or limits
            cons = rec.get("cp_consumption_mt_day")
            vessel_id = str(rec.get("vessel_id") or "")
            voyage_dir = voyage_root(settings.reports_out_dir, vessel_id, voy_no)
            ctx = {
                "voyage_number": voy_no,
                "vessel_id": rec.get("vessel_id", ""),
                "generated_at": _utc_now().isoformat(),
                "horizon_hours": "full voyage" if trigger == "pre_voyage" else self.spec.get("horizon_hours", 168),
                "interval_hours": self.spec.get("waypoint_interval_hours", 6),
                "max_wind_kn": applied.get("max_wind_kn", limits.get("max_wind_kn", 35)),
                "max_wave_m": applied.get("max_wave_m", limits.get("max_wave_m", 4.0)),
                "max_swell_m": applied.get("max_swell_m", limits.get("max_swell_m", 3.0)),
                "suggested_id": data.get("suggested_id", ""),
                "alternatives_block": alt_block,
                "waypoint_table": wp_table,
                "center_buffer_nm": buffers.get("center_buffer_nm", settings.storm_center_buffer_nm),
                "edge_buffer_nm": buffers.get("edge_buffer_nm", settings.storm_edge_buffer_nm),
                "trigger": trigger,
                "weather_relaxed": data.get("weather_relaxed", False),
                "rejected_for_land": ", ".join(data.get("rejected_for_land") or []) or "none",
            }
            try:
                txt_body = fill_template("route_alternatives.txt", ctx)
            except Exception:
                txt_body = (
                    f"Route alternatives ({trigger}) {voy_no} @ {ctx['generated_at']}\n"
                    f"Suggested: {ctx['suggested_id']}\n\n{alt_block}\n\n{wp_table}"
                )
            map_path = None
            try:
                from vpm_agents.tools.voyage_map import render_routes_map

                order = ("fastest", "shortest", "fuel", "safest")
                alt_pairs = [
                    (routes[rid].get("label", rid), (routes[rid].get("route") or {}).get("waypoints") or [])
                    for rid in order
                    if rid in routes
                ]
                alt_pairs.extend(
                    (r.get("label", rid), (r.get("route") or {}).get("waypoints") or [])
                    for rid, r in routes.items()
                    if rid not in order
                )
                map_path = render_routes_map(
                    rec.get("master_waypoints") or master,
                    alt_pairs,
                    voyage_dir / f"route_alts_map_{trigger}_{stamp}.png",
                    voyage_number=voy_no,
                    labels=(
                        rec.get("source_port") or "Departure",
                        rec.get("dest_port") or "Arrival",
                    ),
                    title=f"Route alternatives — {voy_no} ({trigger})",
                )
            except Exception as e:
                state.note(self.name, f"{voy_no} route map failed: {e}")
            if map_path:
                state.note(self.name, f"{voy_no} route map → {map_path.name}")
            map_images = [map_path] if map_path else None
            txt_path = write_report(sub_dir, f"route_alternatives_{trigger}_{stamp}.txt", txt_body)
            if trigger == "noon":
                write_report(
                    voyage_report_dir(
                        settings.reports_out_dir,
                        vessel_id,
                        voy_no,
                        VPA_REPORT,
                    ),
                    f"route_alternatives_{trigger}_{stamp}.txt",
                    txt_body,
                    email_pdf=True,
                    voyage_number=voy_no,
                    pdf_images=map_images,
                )

            if trigger == "pre_voyage":
                cons_line = f"CP consumption: {cons} MT/day" if cons is not None else ""
                pv_ctx = {
                    "voyage_number": voy_no,
                    "vessel_id": rec.get("vessel_id", ""),
                    "source_port": rec.get("source_port", ""),
                    "dest_port": rec.get("dest_port", ""),
                    "cp_speed_kn": rec.get("cp_speed_kn", ""),
                    "cp_consumption_line": cons_line,
                    "generated_at": ctx["generated_at"],
                    "waypoint_count": len(rec.get("six_hour_plan") or []),
                    "waypoint_block": format_waypoints(rec.get("six_hour_plan") or []),
                    "alternatives_block": alt_block,
                    "waypoint_table": wp_table,
                }
                try:
                    pv_body = fill_template("pre_voyage_route.txt", pv_ctx)
                except Exception:
                    pv_body = (
                        f"Pre-voyage {voy_no} {rec.get('source_port')} → {rec.get('dest_port')}\n"
                        f"CP speed: {rec.get('cp_speed_kn')} kn\n"
                        + (cons_line + "\n" if cons_line else "")
                        + f"Suggested: {ctx['suggested_id']}\n\n{alt_block}\n\n{wp_table}\n"
                    )
                write_report(
                    voyage_report_dir(
                        settings.reports_out_dir,
                        vessel_id,
                        voy_no,
                        PRE_VOYAGE_REPORT,
                    ),
                    f"pre_voyage_route_{stamp}.txt",
                    pv_body,
                    email_pdf=True,
                    voyage_number=voy_no,
                    pdf_images=map_images,
                )

            suggested = routes.get(data.get("suggested_id") or "")
            sug_wps = suggested["route"]["waypoints"] if suggested else []
            registry_routes = {
                rid: {
                    "objective": r["optimize_for"],
                    "waypoints": r["route"].get("waypoints"),
                    "distanceNm": r["route"].get("distanceNm"),
                    "fuelMt": r["route"].get("fuelMt"),
                    "etaHours": r["route"].get("etaHours"),
                    "days": (r.get("voyage") or {}).get("days"),
                    "weather_along": r.get("weather_along"),
                    "weather_score": r["weather_score"],
                    "storm_score": r.get("storm_score"),
                    "land_score": r.get("land_score"),
                    "sea_clear": r.get("sea_clear", True),
                    "avoids_storms": r.get("avoids_storms"),
                    "file": per_files.get(rid),
                }
                for rid, r in routes.items()
            }
            self.registry.upsert(
                voy_no,
                {
                    "optimized_routes": registry_routes,
                    "suggested_route_id": data.get("suggested_id"),
                    "suggested_route": sug_wps,
                    "route_optimize_at": _utc_now().isoformat(),
                    "route_optimize_trigger": trigger,
                    "route_weather_relaxed": data.get("weather_relaxed", False),
                    "last_route_alternatives": str(index_path),
                },
            )
            _enqueue_suggested_routes(
                voy_no, rec, routes, data.get("suggested_id"), trigger, state
            )
            state.optimized_routes = registry_routes
            state.suggested_route = sug_wps
            state.artifacts["route_alternatives"] = str(index_path)
            state.phase = self.spec.get("phase", "routes_optimized")
            state.note(
                self.name,
                f"{voy_no} [{trigger}] alternatives={len(routes)} "
                f"suggested={data.get('suggested_id')} → {index_path.parent.name}/{index_path.name}",
                elapsed_s=time.monotonic() - t0,
            )
            if (settings.route_opt_method or "").strip().lower() == "llm":
                llm_ok = sum(
                    1 for r in routes.values() if not r.get("route", {}).get("llm_fallback")
                )
                llm_fb = len(routes) - llm_ok
                fb_errors = {
                    rid: r["route"].get("llm_error")
                    for rid, r in routes.items()
                    if r.get("route", {}).get("llm_fallback")
                }
                err_summary = ", ".join(f"{k}={v}" for k, v in fb_errors.items()) or "none"
                state.note(
                    self.name,
                    f"{voy_no} [{trigger}] llm ok={llm_ok} fallback={llm_fb} "
                    f"provider={settings.effective_llm_provider} model={settings.llm_model} "
                    f"errors: {err_summary}",
                )
        except Exception as e:
            state.note(self.name, f"{voy_no} failed: {e}", elapsed_s=time.monotonic() - t0)
        return state


class StormWatchAgent(Agent):
    name = "StormWatchAgent"

    def build_tools(self) -> list[Tool]:
        return [
            Tool("watcher", "Refresh storm pipeline", self._watch),
            Tool("storms", "List active storms + progressions", self._storms),
        ]

    def _watch(self, token: str = "") -> ToolResult:
        return ToolResult(ok=True, data=self.backend.run_storm_watcher(token))

    def _storms(self, token: str = "") -> ToolResult:
        return ToolResult(ok=True, data=_fetch_active_storms(self.backend, token))

    def run(self, state: SessionState, token: str = "") -> SessionState:
        source = (settings.storm_source or "live").strip().lower()
        state.note(self.name, f"poll start source={source}")
        t0 = time.monotonic()
        try:
            if source == "backend":
                if not token:
                    token = _auth_token(self.backend)
                self.backend.run_storm_watcher(token)
            elif source == "mock" and not token:
                token = _auth_token(self.backend)

            storms = _fetch_active_storms(self.backend, token or "")
            fetched_at = _utc_now().isoformat()
            remember_storms(storms, fetched_at)  # share immediately; reports/email can lag
            state.storms = storms
            voyage_hits = assess_all_voyages(storms, self.registry.all())

            out = Path(settings.storm_out_dir)
            out.mkdir(parents=True, exist_ok=True)
            payload = {
                "fetched_at": fetched_at,
                "source": source,
                "count": len(storms),
                "storms": storms,
                "voyage_route_alerts": voyage_hits,
                "center_buffer_nm": settings.storm_center_buffer_nm,
                "edge_buffer_nm": settings.storm_edge_buffer_nm,
                "threshold_nm": settings.storm_center_buffer_nm,
            }
            stamp = _stamp()
            cyclone_manifest = write_storm_voyage_reports(
                storms,
                voyage_hits,
                self.registry.all(),
                stamp=stamp,
                storm_source=source,
            )
            payload["voyage_cyclone_reports"] = cyclone_manifest
            json_path = out / f"storms_{stamp}.json"
            json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            storm_lines = []
            for s in storms:
                npos = len(s.get("positions") or [])
                line = (
                    f"- {s.get('id', '?')} {s.get('name', '')} "
                    f"center={s.get('lat')},{s.get('lon')} "
                    f"radius={s.get('radius_nm')}NM wind={s.get('wind_kn')}kn "
                    f"progressions={npos}"
                )
                storm_lines.append(line)
            if not storm_lines:
                storm_lines.append("- (none)")

            encounter_lines = []
            for voy_no, hits in voyage_hits.items():
                for h in hits:
                    encounter_lines.append(f"  ⚠ {voy_no}: {h['alert']}")
            encounter_block = (
                "\n".join(encounter_lines)
                if encounter_lines
                else "  (no route encounters within center/edge buffers)"
            )

            try:
                body = fill_template(
                    "storm_alert.txt",
                    {
                        "fetched_at": payload["fetched_at"],
                        "count": len(storms),
                        "storm_block": "\n".join(storm_lines),
                        "encounter_block": encounter_block,
                        "threshold_nm": settings.storm_center_buffer_nm,
                        "center_buffer_nm": settings.storm_center_buffer_nm,
                        "edge_buffer_nm": settings.storm_edge_buffer_nm,
                    },
                )
            except FileNotFoundError:
                body = (
                    f"Storm alert @ {payload['fetched_at']}\nStorms: {len(storms)}\n"
                    f"Center buffer: {settings.storm_center_buffer_nm} NM  "
                    f"Edge buffer: {settings.storm_edge_buffer_nm} NM\n\n"
                    + "\n".join(storm_lines)
                    + "\n\nRoute encounters:\n"
                    + encounter_block
                )
            txt_path = write_report(
                out, f"storms_{stamp}.txt", body, email_pdf=True, voyage_number="fleet"
            )
            state.artifacts["storm_json"] = str(json_path)
            state.note(
                self.name,
                f"storms={len(storms)} encounters={len(voyage_hits)} → {json_path.name}",
                elapsed_s=time.monotonic() - t0,
            )
        except Exception as e:
            state.note(self.name, f"storm poll failed: {e}", elapsed_s=time.monotonic() - t0)
        return state

    def __init__(self, backend: Any, registry: VoyageRegistry | None = None):
        super().__init__(backend)
        self.registry = registry or VoyageRegistry()
