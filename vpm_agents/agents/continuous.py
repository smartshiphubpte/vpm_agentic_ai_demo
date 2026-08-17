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
from vpm_agents.tools.inbox_io import (
    archive_inbox_file,
    classify_inbox_file,
    list_inbox,
    parse_noon_report,
    parse_pre_voyage,
)
from vpm_agents.tools.noon_source import FolderNoonSource, archive_finished_drops, get_noon_sources
from vpm_agents.tools.noon_io import is_arrival_report
from vpm_agents.tools.route_weather import build_voyage_track, format_track_block
from vpm_agents.tools.storm_normalize import normalize_active_storms
from vpm_agents.tools.storm_proximity import assess_all_voyages
from vpm_agents.tools.templates import fill_template, format_waypoints, write_report
from vpm_agents.tools.voyage_registry import VoyageRegistry, normalize_voyage_number
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


def _auth_token(backend: Any) -> str:
    login = backend.login(settings.email, settings.password)
    token = login["token"]
    if settings.company and settings.company != login.get("company"):
        backend.set_company(token, settings.company)
    return token


def _schedule_weather(registry: VoyageRegistry, voyage_number: str, plan_key: str) -> None:
    if not settings.weather_report_on_prevoyage:
        return
    delay = max(0.0, settings.weather_report_delay_minutes)
    due = _utc_now() + __import__("datetime").timedelta(minutes=delay)
    registry.upsert(
        voyage_number,
        {"weather_due_at": due.isoformat(), "weather_plan_key": plan_key},
    )


def _voyage_subreports_dir(voyage_number: str) -> Path:
    """All alternate-route / weather subreports live under reports/{voyage}/subreports/."""
    d = settings.reports_out_dir / voyage_number / "subreports"
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
    path.write_text(json.dumps(combined, indent=2), encoding="utf-8")
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
        voyage_dir = settings.reports_out_dir / voy_no
        voyage_dir.mkdir(parents=True, exist_ok=True)
        combined_path = _write_combined(
            voyage_dir, voy_no, plan, wx, vessel_name=rec.get("vessel_name", ""), prefix="voyage_track_weather"
        )
        track_data = json.loads(combined_path.read_text())
        weather_txt = weather_json = None
        if settings.weather_report_on_prevoyage or settings.weather_report_on_noon:
            weather_txt, weather_json = write_weather_report(
                voy_no,
                track_data,
                voyage_rec=rec,
                vessel_id=rec.get("vessel_id", ""),
                vessel_name=rec.get("vessel_name", ""),
                plan_label="pre-voyage 6h plan",
            )
        registry.upsert(
            voy_no,
            {
                "last_voyage_track": str(combined_path),
                "last_weather_report": str(weather_txt) if weather_txt else rec.get("last_weather_report"),
                "weather_summary": {
                    "pointCount": len(wx.get("points", [])),
                    "hardCount": len(wx.get("hardRegions", [])),
                    "provider": wx.get("provider"),
                },
            },
        )
        if settings.weather_report_on_prevoyage:
            _schedule_weather(registry, voy_no, plan_key)
        else:
            registry.upsert(voy_no, {"weather_due_at": None})
        state.artifacts["voyage_track_weather"] = str(combined_path)
        state.note(
            "Weather",
            f"{voy_no} track+wx → {combined_path}"
            + (f" report → {weather_txt}" if weather_txt else ""),
            elapsed_s=time.monotonic() - t0,
        )
    except Exception as e:
        state.note("Weather", f"{voy_no} failed: {e}", elapsed_s=time.monotonic() - t0)
    return state


class PreVoyageIngestAgent(Agent):
    name = "PreVoyageIngestAgent"

    def __init__(self, backend: Any, registry: VoyageRegistry | None = None):
        super().__init__(backend)
        self.registry = registry or VoyageRegistry()

    def build_tools(self) -> list[Tool]:
        return [
            Tool("parse_pre_voyage", "Parse inbox pre-voyage file", self._parse),
            Tool("plan_waypoints", "Build 6h waypoint plan", self._plan),
        ]

    def _parse(self, path: str) -> ToolResult:
        return ToolResult(ok=True, data=parse_pre_voyage(path))

    def _plan(self, master: list, speed_kn: float) -> ToolResult:
        pts = six_hour_waypoints(master, speed_kn, interval_h=settings.waypoint_interval_hours)
        return ToolResult(ok=True, data=pts)

    def run(
        self,
        state: SessionState,
        path: str | Path | None = None,
        schedule_weather: bool = True,
    ) -> SessionState:
        if not path:
            state.note(self.name, "no path — skip")
            return state
        path = Path(path)
        t0 = time.monotonic()
        try:
            data = parse_pre_voyage(path)
            master = data["master_waypoints"]
            speed = data["cp_speed_kn"]
            plan = six_hour_waypoints(master, speed, interval_h=settings.waypoint_interval_hours)
            voy_no = normalize_voyage_number(data["voyage_number"])
            record = {
                **{k: data[k] for k in (
                    "voyage_number", "vessel_id", "source_port", "dest_port",
                    "cp_speed_kn", "alert_emails", "master_waypoints", "source_file",
                ) if k in data},
                "vessel_name": data.get("vessel_name", ""),
                "format": data.get("format", ""),
                "waypoint_names": data.get("waypoint_names", []),
                "etd": data.get("etd", ""),
                "eta": data.get("eta", ""),
                "condition": data.get("condition", ""),
                "cp_consumption_mt_day": data.get("cp_consumption_mt_day"),
                "six_hour_plan": plan,
                "ingested_at": _utc_now().isoformat(),
                "last_noon": None,
            }
            # upsert re-keys under normalize_voyage_number (keeps L*/B* tags distinct)
            self.registry.upsert(voy_no, record)

            voyage_dir = settings.reports_out_dir / voy_no
            voyage_dir.mkdir(parents=True, exist_ok=True)
            (voyage_dir / "master_route.json").write_text(json.dumps(master, indent=2), encoding="utf-8")

            cons = data.get("cp_consumption_mt_day")
            ctx = {
                "voyage_number": voy_no,
                "vessel_id": data["vessel_id"],
                "source_port": data["source_port"],
                "dest_port": data["dest_port"],
                "cp_speed_kn": speed,
                "cp_consumption_line": (
                    f"CP consumption: {cons} MT/day" if cons is not None else ""
                ),
                "generated_at": _utc_now().isoformat(),
                "waypoint_count": len(plan),
                "waypoint_block": format_waypoints(plan),
                "alternatives_block": "(four optimized routes are written after route optimize)",
            }
            report_path = write_report(
                voyage_dir,
                f"pre_voyage_route_{_stamp()}.txt",
                fill_template("pre_voyage_route.txt", ctx),
                email_pdf=True,
                voyage_number=voy_no,
            )

            state.voyage_number = voy_no
            state.master_route = [{"lat": p[0], "lon": p[1]} for p in master]
            state.artifacts["six_hour_plan"] = plan
            state.note(
                self.name,
                f"{voy_no} master={len(master)} six_hour={len(plan)} → {report_path.name}",
                elapsed_s=time.monotonic() - t0,
            )
            if schedule_weather:
                _schedule_weather(self.registry, voy_no, "six_hour_plan")
            if path.exists():
                archive_inbox_file(path, "processed")
        except Exception as e:
            state.note(self.name, f"failed {Path(path).name}: {e}", elapsed_s=time.monotonic() - t0)
            if Path(path).exists():
                try:
                    archive_inbox_file(path, "failed")
                except Exception:
                    pass
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
        return ToolResult(ok=True, data=parse_noon_report(path))

    def run(
        self,
        state: SessionState,
        path: str | Path | None = None,
        noon: dict[str, Any] | None = None,
    ) -> SessionState:
        if noon is None and path:
            try:
                noon = parse_noon_report(path)
            except Exception as e:
                state.note(self.name, f"failed {Path(path).name}: {e}")
                return state
        if not noon:
            state.note(self.name, "no noon data — skip")
            return state
        return self._process_noon(state, noon, path)

    def _process_noon(
        self,
        state: SessionState,
        noon: dict[str, Any],
        path: str | Path | None = None,
    ) -> SessionState:
        try:
            voy, registry_key = self.registry.find_voyage(noon["voyage_number"])
            if not voy:
                raise ValueError(
                    f"no pre-voyage registry for {noon['voyage_number']} — ingest pre-voyage first"
                )
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

            voyage_dir = settings.reports_out_dir / registry_key
            voyage_dir.mkdir(parents=True, exist_ok=True)
            combined_path = _write_combined(
                voyage_dir,
                registry_key,
                plan,
                wx,
                noon=noon,
                vessel_name=voy.get("vessel_name") or noon.get("vessel_name", ""),
                prefix="voyage_track_weather",
            )
            track_data = json.loads(combined_path.read_text())

            weather_txt = weather_json = None
            if settings.weather_report_on_noon:
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
                voyage_dir, f"noon_7day_report_{_stamp()}.txt", fill_template("noon_7day_report.txt", ctx)
            )

            self.registry.upsert(
                registry_key,
                {
                    "last_noon": noon,
                    "noon_seven_day_plan": plan,
                    "last_voyage_track": str(combined_path),
                    "last_weather_report": str(weather_txt) if weather_txt else voy.get("last_weather_report"),
                    "last_bad_weather_json": str(weather_json) if weather_json else voy.get("last_bad_weather_json"),
                    "noon_updated_at": _utc_now().isoformat(),
                    "noon_history": _append_noon_history(voy, noon),
                },
            )
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
            if settings.eov_on_arrival and is_arrival_report(noon.get("report_type")):
                _schedule_eov(self.backend, self.registry, registry_key)
                state.note(self.name, f"{registry_key} arrival → EOV queued (background)")

            # On noon + weather: recompute 4 alternate routes from ship position + remaining WPs
            try:
                opt_agent = PreVoyageRouteOptimizeAgent(self.backend, self.registry)
                state = opt_agent.run(
                    state,
                    voyage_number=registry_key,
                    waypoints=remaining,
                    speed_kn=speed,
                    trigger="noon",
                )
            except Exception as e:
                state.note(self.name, f"{registry_key} noon route alts failed: {e}")

            if path and Path(path).exists():
                try:
                    archive_inbox_file(path, "processed")
                except Exception:
                    pass
        except Exception as e:
            state.note(self.name, f"failed: {e}")
            if path and Path(path).exists():
                try:
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

    def run(self, state: SessionState) -> SessionState:
        sources = get_noon_sources()
        drop_rows: list[dict] = []
        drip_rows: list[dict] = []
        for src in sources:
            fetched = src.fetch_new(self.registry)
            if isinstance(src, FolderNoonSource):
                drop_rows.extend(fetched)
            else:
                drip_rows.extend(fetched[: max(1, settings.noon_batch_size)])
        rows = drop_rows + drip_rows
        if not rows:
            state.note(self.name, "no new noon rows", quiet=True)
            return state
        for row in rows:
            payload = {k: v for k, v in row.items() if k != "_drop_path"}
            state.note(self.name, f"noon row {payload.get('noon_id')} voy={payload.get('voyage_number')}")
            state = self.noon_agent.run(state, noon=payload)
        archive_finished_drops(drop_rows, self.registry)
        state.phase = self.spec.get("phase", "noon_excel_polled")
        return state


class WeatherReportAgent(Agent):
    """Delayed weather for pre-voyage plan — writes combined track+weather JSON."""

    name = "WeatherReportAgent"

    def __init__(self, backend: Any, registry: VoyageRegistry | None = None):
        super().__init__(backend)
        self.registry = registry or VoyageRegistry()

    def run(self, state: SessionState) -> SessionState:
        now = _utc_now()
        due: list[tuple[str, dict]] = []
        for voy_no, rec in self.registry.all().items():
            raw = rec.get("weather_due_at")
            if not raw:
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

        try:
            token = _auth_token(self.backend)
        except Exception as e:
            state.note(self.name, f"auth failed: {e}")
            return state

        for voy_no, rec in due:
            t0 = time.monotonic()
            plan_key = rec.get("weather_plan_key") or "six_hour_plan"
            plan = rec.get(plan_key) or []
            if not plan:
                self.registry.upsert(voy_no, {"weather_due_at": None})
                continue
            try:
                pts = [{"lat": p["lat"], "lon": p["lon"]} for p in plan]
                wx = self.backend.weather_along_route(token, pts)
                voyage_dir = settings.reports_out_dir / voy_no
                voyage_dir.mkdir(parents=True, exist_ok=True)
                combined_path = _write_combined(
                    voyage_dir, voy_no, plan, wx, vessel_name=rec.get("vessel_name", ""), prefix="voyage_track_weather"
                )
                track = json.loads(combined_path.read_text())
                if settings.weather_report_on_prevoyage:
                    weather_txt, weather_json = write_weather_report(
                        voy_no,
                        track,
                        voyage_rec=rec,
                        vessel_id=rec.get("vessel_id", ""),
                        vessel_name=rec.get("vessel_name", ""),
                        plan_label="6-hour pre-voyage plan",
                        spec=self.spec,
                    )
                else:
                    weather_txt = weather_json = None
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
        storms = _fetch_active_storms(self.backend, token)
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
        state.note(
            self.name,
            f"{voy_no} [{trigger}] start — {len(master)} WPs method={settings.route_opt_method} "
            f"algo={settings.route_opt_algo} (4 objectives; can take several minutes)",
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
            buffers = data.get("storm_buffers") or {}
            applied = data.get("weather_limits_applied") or limits
            cons = rec.get("cp_consumption_mt_day")
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
                    f"Suggested: {ctx['suggested_id']}\n\n" + ctx["alternatives_block"]
                )
            txt_path = write_report(sub_dir, f"route_alternatives_{trigger}_{stamp}.txt", txt_body)

            if trigger == "pre_voyage":
                voyage_dir = settings.reports_out_dir / voy_no
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
                }
                try:
                    pv_body = fill_template("pre_voyage_route.txt", pv_ctx)
                except Exception:
                    pv_body = (
                        f"Pre-voyage {voy_no} {rec.get('source_port')} → {rec.get('dest_port')}\n"
                        f"CP speed: {rec.get('cp_speed_kn')} kn\n"
                        + (cons_line + "\n" if cons_line else "")
                        + f"Suggested: {ctx['suggested_id']}\n\n{alt_block}\n"
                    )
                write_report(
                    voyage_dir,
                    f"pre_voyage_route_{stamp}.txt",
                    pv_body,
                    email_pdf=True,
                    voyage_number=voy_no,
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
            state.optimized_routes = registry_routes
            state.suggested_route = sug_wps
            state.artifacts["route_alternatives"] = str(index_path)
            state.phase = self.spec.get("phase", "routes_optimized")
            state.note(
                self.name,
                f"{voy_no} [{trigger}] alternatives={len(routes)} "
                f"suggested={data.get('suggested_id')} → subreports/{index_path.name}",
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
            state.storms = storms
            voyage_hits = assess_all_voyages(storms, self.registry.all())

            out = Path(settings.storm_out_dir)
            out.mkdir(parents=True, exist_ok=True)
            payload = {
                "fetched_at": _utc_now().isoformat(),
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


class InboxWatchAgent(Agent):
    name = "InboxWatchAgent"

    def __init__(
        self,
        backend: Any,
        registry: VoyageRegistry | None = None,
        pre_agent: PreVoyageIngestAgent | None = None,
        noon_agent: NoonOpsAgent | None = None,
        flow_name: str | None = None,
    ):
        self.registry = registry or VoyageRegistry()
        self.pre_agent = pre_agent or PreVoyageIngestAgent(backend, self.registry)
        self.noon_agent = noon_agent or NoonOpsAgent(backend, self.registry)
        self.flow_name = flow_name or settings.daemon_flow
        super().__init__(backend)

    def build_tools(self) -> list[Tool]:
        return [Tool("list_inbox", "List new inbox files", self._list)]

    def _list(self) -> ToolResult:
        return ToolResult(ok=True, data=[str(p) for p in list_inbox(settings.inbox_dir)])

    def run(self, state: SessionState) -> SessionState:
        files = list_inbox(settings.inbox_dir)
        if not files:
            state.note(self.name, "inbox empty", quiet=True)
            return state
        from vpm_agents.core.flow_runner import PreVoyageFlowRunner

        ordered = [(classify_inbox_file(p), p) for p in files]
        ordered.sort(key=lambda t: 0 if t[0] == "pre_voyage" else 1)
        for kind, path in ordered:
            state.note(self.name, f"seen {path.name} kind={kind}")
            if kind == "pre_voyage":
                runner = PreVoyageFlowRunner(self.backend, self.registry, self.flow_name)
                state = runner.run(state, path)
            elif kind == "noon_report":
                state.note(self.name, f"{path.name}: drop noon files in {settings.noon_inbox_dir}")
                archive_inbox_file(path, "failed")
            else:
                archive_inbox_file(path, "failed")
        state.phase = self.spec.get("phase", "inbox_scanned")
        return state
