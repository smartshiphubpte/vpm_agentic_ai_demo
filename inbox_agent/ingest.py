"""Accept a validated pre-voyage record → registry / reports / prevoyage_db job."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vpm_agents.config import settings
from vpm_agents.core.base import Agent, Tool, ToolResult
from vpm_agents.core.state import SessionState
from vpm_agents.tools.folder_layout import PRE_VOYAGE_REPORT, voyage_report_dir, voyage_root
from vpm_agents.tools.geo import six_hour_waypoints
from vpm_agents.tools.templates import fill_template, format_waypoints, write_report
from vpm_agents.tools.voyage_registry import VoyageRegistry, compact_voyage_number

from inbox_agent.parse import archive_inbox_file, parse_pre_voyage


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return _utc_now().strftime("%Y%m%dT%H%M%SZ")


def enqueue_prevoyage_db(voy_no: str, record: dict[str, Any], state: SessionState) -> None:
    """Hand off to prevoyage_db microservice (no DB creds in ingest)."""
    tenant = (settings.tenant or "").strip().lower()
    if not tenant:
        state.note("PreVoyageIngestAgent", "VPM_TENANT unset — skip prevoyage_db job", quiet=True)
        return
    from vpm_agents.tools import job_bus

    key = f"prevoyage_db:{tenant}:{voy_no}"
    if job_bus.enqueue(
        key,
        {
            "kind": "prevoyage_db",
            "tenant": tenant,
            "voyage_number": voy_no,
            "record": record,
        },
        root=settings.jobs_dir,
    ):
        state.note("PreVoyageIngestAgent", f"queued {key} for DB ingest")


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

    def ingest_parsed(
        self,
        state: SessionState,
        data: dict[str, Any],
        *,
        persist_files: bool = True,
        source_path: Path | None = None,
    ) -> SessionState:
        """Turn a parsed pre-voyage dict into a prevoyage_db job. persist_files=False for IMAP."""
        t0 = time.monotonic()
        master = data["master_waypoints"]
        speed = data["cp_speed_kn"]
        plan = six_hour_waypoints(master, speed, interval_h=settings.waypoint_interval_hours)
        voy_no = compact_voyage_number(data["voyage_number"])
        record = {
            **{k: data[k] for k in (
                "voyage_number", "vessel_id", "source_port", "dest_port",
                "cp_speed_kn", "alert_emails", "master_waypoints", "source_file",
                "displacement", "cargo_weight", "max_draft_on_departure",
                "voyage_priority",
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
            "noon_history": [],
            "eov_status": None,
            "noon_seven_day_plan": None,
        }
        if persist_files:
            self.registry.forget_voyage_noons(voy_no)
            self.registry.upsert(voy_no, record)
            voyage_dir = voyage_root(settings.reports_out_dir, data["vessel_id"], voy_no)
            voyage_dir.mkdir(parents=True, exist_ok=True)
            (voyage_dir / "master_route.json").write_text(json.dumps(master, indent=2), encoding="utf-8")
            pre_voyage_dir = voyage_report_dir(
                settings.reports_out_dir,
                data["vessel_id"],
                voy_no,
                PRE_VOYAGE_REPORT,
            )
            cons = data.get("cp_consumption_mt_day")
            stamp = _stamp()
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
                "waypoint_table": "(four optimized routes are written after route optimize)",
            }
            map_images: list = []
            try:
                from vpm_agents.tools.voyage_map import render_voyage_map

                map_path = render_voyage_map(
                    [{"lat": p[0], "lon": p[1]} for p in master],
                    pre_voyage_dir / f"pre_voyage_map_{stamp}.png",
                    voyage_number=voy_no,
                    labels=(data.get("source_port") or "Departure", data.get("dest_port") or "Arrival"),
                )
                if map_path:
                    map_images.append(map_path)
            except Exception as e:
                state.note(self.name, f"{voy_no} ingest map failed: {e}")
            report_path = write_report(
                pre_voyage_dir,
                f"pre_voyage_route_{stamp}.txt",
                fill_template("pre_voyage_route.txt", ctx),
                email_pdf=True,
                voyage_number=voy_no,
                pdf_images=map_images or None,
            )
            note_tail = f" → {report_path.name}"
        else:
            note_tail = " (mail, no files)"
        state.voyage_number = voy_no
        state.master_route = [{"lat": p[0], "lon": p[1]} for p in master]
        state.artifacts["six_hour_plan"] = plan
        state.note(
            self.name,
            f"{voy_no} master={len(master)} six_hour={len(plan)}{note_tail}",
            elapsed_s=time.monotonic() - t0,
        )
        enqueue_prevoyage_db(voy_no, record, state)
        if persist_files and source_path is not None and source_path.exists():
            archive_inbox_file(source_path)
        return state

    def run(
        self,
        state: SessionState,
        path: str | Path | None = None,
    ) -> SessionState:
        if not path:
            state.note(self.name, "no path — skip")
            return state
        path = Path(path)
        t0 = time.monotonic()
        try:
            data = parse_pre_voyage(path)
            state = self.ingest_parsed(state, data, persist_files=True, source_path=path)
        except Exception as e:
            state.note(self.name, f"failed {Path(path).name}: {e}", elapsed_s=time.monotonic() - t0)
            if Path(path).exists():
                try:
                    archive_inbox_file(path, "failed")
                except Exception:
                    pass
        return state
