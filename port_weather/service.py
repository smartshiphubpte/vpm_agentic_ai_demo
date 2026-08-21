"""Poll registry: start port weather on Arrival, refresh on interval, stop on Departure."""

from __future__ import annotations

import json
import signal
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from port_weather.config import settings
from port_weather.report import hourly_waypoints, write_port_weather_report
from vpm_agents.config import settings as vpm
from vpm_agents.tools import get_backend
from vpm_agents.tools.agent_log import progress
from vpm_agents.tools.noon_io import is_arrival_report, is_departure_report, sort_noon_rows
from vpm_agents.tools.voyage_registry import VoyageRegistry


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def vessel_key(rec: dict[str, Any]) -> str:
    vid = str(rec.get("vessel_id") or "").strip()
    if vid:
        return f"id:{vid}"
    noon = rec.get("last_noon") or {}
    name = str(rec.get("vessel_name") or noon.get("vessel_name") or "").strip()
    if name:
        return f"name:{name.lower()}"
    voy = str(rec.get("voyage_number") or "").strip()
    return f"voy:{voy}" if voy else ""


def _noons_for(rec: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rec.get("noon_history") or []:
        if isinstance(row, dict):
            out.append(row)
    last = rec.get("last_noon")
    if isinstance(last, dict):
        nid = last.get("noon_id")
        if not nid or not any(r.get("noon_id") == nid for r in out):
            out.append(last)
    return out


def in_port_vessels(voyages: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Vessel is in port when its latest Arrival is newer than its latest Departure."""
    grouped: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for rec in voyages.values():
        if not isinstance(rec, dict):
            continue
        key = vessel_key(rec)
        if not key:
            continue
        for noon in _noons_for(rec):
            grouped.setdefault(key, []).append((rec, noon))

    out: dict[str, dict[str, Any]] = {}
    for key, pairs in grouped.items():
        ordered = sort_noon_rows([n for _, n in pairs])
        arrivals = [n for n in ordered if is_arrival_report(n.get("report_type"))]
        deps = [n for n in ordered if is_departure_report(n.get("report_type"))]
        last_arr = arrivals[-1] if arrivals else None
        last_dep = deps[-1] if deps else None
        if not last_arr:
            continue
        if last_dep and sort_noon_rows([last_arr, last_dep])[-1] is last_dep:
            continue
        rec = next((r for r, n in pairs if n is last_arr), pairs[-1][0])
        lat, lon = last_arr.get("lat"), last_arr.get("lon")
        if lat is None or lon is None:
            continue
        out[key] = {
            "key": key,
            "vessel_id": str(rec.get("vessel_id") or ""),
            "vessel_name": str(rec.get("vessel_name") or last_arr.get("vessel_name") or ""),
            "voyage_number": str(rec.get("voyage_number") or last_arr.get("voyage_number") or ""),
            "port_name": str(rec.get("dest_port") or ""),
            "lat": float(lat),
            "lon": float(lon),
            "arrived_at": str(last_arr.get("observed_at") or ""),
            "arrival_noon_id": str(last_arr.get("noon_id") or ""),
        }
    return out


def load_state(path: Path | None = None) -> dict[str, Any]:
    p = Path(path or settings.state_path)
    if not p.is_file():
        return {"vessels": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"vessels": {}}
    data.setdefault("vessels", {})
    return data


def save_state(data: dict[str, Any], path: Path | None = None) -> None:
    p = Path(path or settings.state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _auth_token(backend: Any) -> str:
    login = backend.login(vpm.email, vpm.password)
    token = login["token"]
    if vpm.company and vpm.company != login.get("company"):
        backend.set_company(token, vpm.company)
    return token


def _fetch_wx(backend: Any, token: str, lat: float, lon: float) -> dict[str, Any]:
    pts = hourly_waypoints(lat, lon, settings.horizon_hours)
    return backend.weather_along_route(token, pts)


def _due(rec: dict[str, Any], now: datetime) -> bool:
    due = _parse_iso(rec.get("next_due_at"))
    return due is None or due <= now


def generate_one(info: dict[str, Any], backend: Any, token: str) -> Path:
    wx = _fetch_wx(backend, token, info["lat"], info["lon"])
    pdf, _txt = write_port_weather_report(
        voyage_number=info["voyage_number"],
        vessel_id=info.get("vessel_id") or "",
        vessel_name=info.get("vessel_name") or "",
        port_name=info.get("port_name") or "",
        lat=info["lat"],
        lon=info["lon"],
        arrived_at=info.get("arrived_at") or "",
        wx=wx,
    )
    return pdf


def tick_once(
    *,
    registry: VoyageRegistry | None = None,
    backend: Any | None = None,
    state_path: Path | None = None,
) -> int:
    """Start / refresh / stop. Returns number of reports written."""
    registry = registry or VoyageRegistry()
    desired = in_port_vessels(registry.all())
    state = load_state(state_path)
    vessels: dict[str, Any] = state.setdefault("vessels", {})
    now = _utc_now()
    interval = timedelta(hours=max(0.0, settings.interval_hours))
    wrote = 0

    for key, rec in list(vessels.items()):
        if rec.get("status") != "active":
            continue
        if key in desired:
            continue
        rec["status"] = "stopped"
        rec["stopped_at"] = now.isoformat()
        progress("port_weather", f"{rec.get('voyage_number') or key} stop — departure seen")

    need: list[dict[str, Any]] = []
    for key, info in desired.items():
        rec = vessels.get(key) or {}
        new_arrival = rec.get("arrival_noon_id") != info["arrival_noon_id"] or rec.get("status") != "active"
        if new_arrival or _due(rec, now):
            need.append(info)

    token = ""
    if need:
        backend = backend or get_backend()
        token = _auth_token(backend)

    for info in need:
        key = info["key"]
        try:
            pdf = generate_one(info, backend, token)
            vessels[key] = {
                **info,
                "status": "active",
                "last_report_at": now.isoformat(),
                "next_due_at": (now + interval).isoformat(),
                "last_pdf": str(pdf),
            }
            wrote += 1
            progress("port_weather", f"{info['voyage_number']} {info.get('port_name') or ''} → {pdf.name}")
        except Exception as e:
            progress("port_weather", f"{info.get('voyage_number') or key} failed: {e}")

    save_state(state, state_path)
    return wrote


def run_forever() -> None:
    if not settings.enabled:
        progress("port_weather", "disabled (VPM_PORT_WEATHER=false)")
        return
    progress(
        "port_weather",
        f"started poll={settings.poll_seconds}s interval={settings.interval_hours}h "
        f"horizon={settings.horizon_hours}h out={settings.out_dir}",
    )
    stop = False

    def _sig(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    interval = max(1.0, settings.poll_seconds)
    while not stop:
        t0 = time.monotonic()
        try:
            n = tick_once()
            if n:
                progress("port_weather", f"wrote {n} report(s)")
        except Exception as e:
            progress("port_weather", f"error: {e}")
        leftover = interval - (time.monotonic() - t0)
        if leftover > 0 and not stop:
            time.sleep(leftover)
    progress("port_weather", "stopped")
