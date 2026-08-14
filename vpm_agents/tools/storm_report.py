"""Per-voyage tropical cyclone alert reports — template format + extended JSON."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vpm_agents.config import settings
from vpm_agents.tools.geo import haversine_nm, initial_bearing_deg
from vpm_agents.tools.marine_units import beaufort_from_kn, compass_label, format_latlon_dms
from vpm_agents.tools.route_json import parse_route_points
from vpm_agents.tools.templates import fill_template, write_report


def storm_out_dir() -> Path:
    out = Path(settings.storm_out_dir)
    out.mkdir(parents=True, exist_ok=True)
    return out


def _nearest_route_point(
    route: list[list[float]], storm_lat: float, storm_lon: float
) -> tuple[int, list[float], float]:
    best_i, best_pt, best_d = 0, route[0], float("inf")
    for i, pt in enumerate(route):
        d = haversine_nm(pt[0], pt[1], storm_lat, storm_lon)
        if d < best_d:
            best_i, best_pt, best_d = i, pt, d
    return best_i, best_pt, best_d


def _wx_at_point(track: dict[str, Any] | None, seq: int, lat: float, lon: float) -> dict[str, Any]:
    if not track:
        return {}
    for p in track.get("track") or []:
        if p.get("seq") == seq or (
            abs(float(p.get("lat", 999)) - lat) < 0.01 and abs(float(p.get("lon", 999)) - lon) < 0.01
        ):
            return p.get("weather") or {}
    return {}


def _reference_table(
    storm_lat: float,
    storm_lon: float,
    voyage_rec: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    refs: list[tuple[str, float, float]] = []
    master = parse_route_points(voyage_rec.get("master_waypoints") or []) if voyage_rec.get("master_waypoints") else []
    if master:
        refs.append((str(voyage_rec.get("source_port") or "Source"), master[0][0], master[0][1]))
        refs.append((str(voyage_rec.get("dest_port") or "Destination"), master[-1][0], master[-1][1]))
    noon = voyage_rec.get("last_noon") or {}
    if noon.get("lat") is not None:
        refs.append(("Current position (noon)", float(noon["lat"]), float(noon["lon"])))

    rows: list[dict[str, Any]] = []
    lines = ["  LOCATION                          BEARING    DISTANCE (NM)", "  " + "-" * 55]
    seen: set[str] = set()
    for label, lat, lon in refs:
        if label in seen:
            continue
        seen.add(label)
        brg = round(initial_bearing_deg(storm_lat, storm_lon, lat, lon), 0)
        dist = round(haversine_nm(storm_lat, storm_lon, lat, lon), 0)
        rows.append({"location": label, "bearing_deg": brg, "distance_nm": dist})
        lines.append(f"  {label:<32}  {brg:>3}°       {dist:>4.0f}")
    if len(lines) == 2:
        lines.append("  (no reference locations available)")
    return "\n".join(lines), rows


def _synopsis(
    voyage_number: str,
    voyage_rec: dict[str, Any],
    storm: dict[str, Any],
    wp_label: str,
    pos_dms: str,
    wx: dict[str, Any],
    cpa_nm: float,
) -> str:
    name = storm.get("name") or storm.get("id") or "tracked system"
    wind = wx.get("windKn") or storm.get("wind_kn") or "—"
    wave = wx.get("waveM") or "—"
    ts = wx.get("validTime") or "latest fix"
    src = voyage_rec.get("source_port") if isinstance(voyage_rec, dict) else None
    return (
        f"A tropical system ({name}) is tracked near {pos_dms} as of {ts}. "
        f"At the nearest route point ({wp_label}), sustained wind is {wind} kn with "
        f"significant wave height {wave} m. Closest point of approach to the system center "
        f"is approximately {cpa_nm:.0f} NM. "
        f"The planned route for voyage {voyage_number}"
        + (f" from {src}" if src else "")
        + " may intersect the danger corridor — review safest-route alternatives."
    )


def _recommended_actions(cpa_nm: float, wx: dict[str, Any], center_buffer: float) -> str:
    wind = float(wx.get("windKn") or 0)
    wave = float(wx.get("waveM") or 0)
    actions = [
        "Confirm official storm classification and warning number with a recognized meteorological authority.",
        "Adopt the tool's safest route alternative where available before resuming the planned track.",
        f"Target CPA of at least 60–100 NM from the system's last known position (current ~{cpa_nm:.0f} NM).",
        f"Increase reporting frequency to every 6 hours while within the {center_buffer:.0f} NM geo-fence.",
    ]
    if wave >= 2.0:
        actions.append(f"Secure deck cargo ahead of transit near the {wave:.1f} m wave-height area.")
    if cpa_nm < 40 or wind >= 34:
        actions.append("Escalate to fleet operations — CPA below 40 NM or gale-force winds along route.")
    else:
        actions.append("Maintain UKC and sea-room margins — do not trade wind avoidance for grounding risk.")
    return "\n".join(f"  • {a}" for a in actions)


def write_storm_voyage_report(
    voyage_number: str,
    voyage_rec: dict[str, Any],
    storm: dict[str, Any],
    hit: dict[str, Any],
    *,
    track: dict[str, Any] | None = None,
    stamp: str | None = None,
    storm_source: str = "",
) -> tuple[Path, Path]:
    """Write per-voyage cyclone txt + json; keeps storm snapshot fields in json payload."""
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    generated_at = datetime.now(timezone.utc).isoformat()
    out_dir = storm_out_dir() / voyage_number
    out_dir.mkdir(parents=True, exist_ok=True)

    route = parse_route_points(
        voyage_rec.get("noon_seven_day_plan")
        or voyage_rec.get("six_hour_plan")
        or voyage_rec.get("master_waypoints")
        or []
    )
    storm_lat = float(hit.get("storm_lat") or storm.get("lat"))
    storm_lon = float(hit.get("storm_lon") or storm.get("lon"))
    seq, pt, cpa_nm = _nearest_route_point(route, storm_lat, storm_lon) if route else (0, [storm_lat, storm_lon], 0.0)
    wx = _wx_at_point(track, seq, pt[0], pt[1])
    wind_kn = wx.get("windKn") or storm.get("wind_kn")
    bf = beaufort_from_kn(wind_kn) if wind_kn is not None else "—"
    pos_dms = format_latlon_dms(pt[0], pt[1])
    storm_dms = format_latlon_dms(storm_lat, storm_lon)
    wp_label = f"Waypoint {seq}"

    ref_table, ref_rows = _reference_table(storm_lat, storm_lon, voyage_rec)
    synopsis = _synopsis(voyage_number, voyage_rec, storm, wp_label, pos_dms, wx, cpa_nm)
    center_buffer = hit.get("center_buffer_nm") or settings.storm_center_buffer_nm
    edge_buffer = hit.get("edge_buffer_nm") or settings.storm_edge_buffer_nm
    suggested = voyage_rec.get("suggested_route") or voyage_rec.get("optimized_routes") or {}
    safest_note = "See route_alternatives / optimized_routes in voyage registry"
    if isinstance(suggested, dict) and suggested.get("safest"):
        safest_note = "Safest route available in registry (optimized_routes.safest)"

    payload: dict[str, Any] = {
        "voyage_number": voyage_number,
        "generated_at": generated_at,
        "storm_id": storm.get("id"),
        "storm_name": storm.get("name"),
        "storm_category": storm.get("category") or storm.get("status") or "—",
        "storm_center": {"lat": storm_lat, "lon": storm_lon},
        "storm_radius_nm": storm.get("radius_nm"),
        "center_buffer_nm": center_buffer,
        "edge_buffer_nm": edge_buffer,
        "route_encounter_likely": hit.get("route_encounter_likely"),
        "distance_to_route_nm": hit.get("distance_to_route_nm"),
        "cpa_nm": round(cpa_nm, 1),
        "nearest_waypoint": {"seq": seq, "lat": pt[0], "lon": pt[1], "weather": wx},
        "reference_locations": ref_rows,
        "synopsis": synopsis,
        "storm_source": storm_source,
        "storm_hit": {k: v for k, v in hit.items() if k != "voyage_rec"},
        "storm": {
            "id": storm.get("id"),
            "name": storm.get("name"),
            "lat": storm.get("lat"),
            "lon": storm.get("lon"),
            "radius_nm": storm.get("radius_nm"),
            "wind_kn": storm.get("wind_kn"),
            "category": storm.get("category"),
            "positions": storm.get("positions"),
        },
    }

    json_path = out_dir / f"cyclone_alert_{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    vessel_display = voyage_rec.get("vessel_name") or voyage_rec.get("vessel_id") or voyage_number
    route_line = f"{voyage_rec.get('source_port', '—')} → {voyage_rec.get('dest_port', '—')}"
    ctx = {
        "voyage_number": voyage_number,
        "vessel_display": vessel_display,
        "route_line": route_line,
        "report_date": datetime.now(timezone.utc).strftime("%d %b %Y"),
        "generated_at": generated_at,
        "storm_name": storm.get("name") or storm.get("id") or "—",
        "storm_id": storm.get("id") or "—",
        "storm_category": payload["storm_category"],
        "storm_center_dms": storm_dms,
        "storm_radius_nm": storm.get("radius_nm") or "—",
        "center_buffer_nm": center_buffer,
        "edge_buffer_nm": edge_buffer,
        "wind_at_fix_kn": wind_kn if wind_kn is not None else "—",
        "wind_bf": bf,
        "wind_dir_deg": wx.get("windDirDeg") or "—",
        "waypoint_label": wp_label,
        "waypoint_seq": seq,
        "position_dms": pos_dms,
        "wave_m": wx.get("waveM") if wx.get("waveM") is not None else "—",
        "wave_dir_label": compass_label(wx.get("waveDirDeg")),
        "swell_m": wx.get("swellM") if wx.get("swellM") is not None else "—",
        "swell_dir_label": compass_label(wx.get("swellDirDeg")),
        "current_kn": wx.get("currentKn") if wx.get("currentKn") is not None else "—",
        "current_dir_deg": wx.get("currentDirDeg") or "—",
        "fix_timestamp": wx.get("validTime") or "—",
        "cpa_nm": f"{cpa_nm:.0f}",
        "synopsis_block": synopsis,
        "reference_locations_table": ref_table,
        "outlook_block": "  • Monitor routing tool for updated fixes while the system remains active.",
        "recommended_actions_block": _recommended_actions(cpa_nm, wx, center_buffer),
        "safest_route_note": safest_note,
        "storm_source": storm_source or settings.storm_source,
    }

    try:
        body = fill_template("tropical_cyclone_alert_report.txt", ctx)
    except FileNotFoundError:
        body = fill_template("storm_alert.txt", ctx)
    txt_path = write_report(out_dir, f"cyclone_alert_{stamp}.txt", body)
    return txt_path, json_path


def write_storm_voyage_reports(
    storms: list[dict[str, Any]],
    voyage_hits: dict[str, list[dict[str, Any]]],
    voyages: dict[str, dict[str, Any]],
    *,
    stamp: str | None = None,
    storm_source: str = "",
) -> list[dict[str, Any]]:
    """One cyclone report per (voyage, storm) encounter; returns manifest for storms_*.json extension."""
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest: list[dict[str, Any]] = []
    storm_by_id = {s.get("id"): s for s in storms}

    for voy_no, hits in voyage_hits.items():
        rec = voyages.get(voy_no) or {}
        track_path = rec.get("last_voyage_track")
        track = None
        if track_path and Path(track_path).is_file():
            track = json.loads(Path(track_path).read_text(encoding="utf-8"))
        for hit in hits:
            if not hit.get("route_encounter_likely"):
                continue
            storm = storm_by_id.get(hit.get("storm_id")) or hit
            txt, js = write_storm_voyage_report(
                voy_no,
                rec,
                storm,
                hit,
                track=track,
                stamp=stamp,
                storm_source=storm_source,
            )
            manifest.append(
                {
                    "voyage_number": voy_no,
                    "storm_id": hit.get("storm_id"),
                    "storm_name": hit.get("storm_name"),
                    "txt_path": str(txt),
                    "json_path": str(js),
                    "route_encounter_likely": True,
                }
            )
    return manifest
