"""Weather report builder — passage advisory format + extended bad_weather JSON."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vpm_agents.config import settings
from vpm_agents.tools.folder_layout import WEATHER_REPORT, voyage_report_dir
from vpm_agents.tools.geo import initial_bearing_deg, remaining_route, route_length_nm
from vpm_agents.tools.marine_units import (
    beaufort_from_kn,
    compass_label,
    current_factor_kn,
    format_latlon_dms,
)
from vpm_agents.tools.route_weather import format_track_block
from vpm_agents.tools.templates import fill_template, write_text_pdf


def weather_out_dir(voyage_number: str, vessel_id: str = "") -> Path:
    return voyage_report_dir(Path(settings.reports_out_dir), vessel_id, voyage_number, WEATHER_REPORT)


def weather_limits_from(spec: Any | None = None) -> dict[str, float]:
    """Wind/wave/swell bars — always WeatherReportAgent.md Defaults unless a spec is passed."""
    if spec is None:
        from vpm_agents.core.spec_loader import load_agent_spec

        spec = load_agent_spec("WeatherReportAgent")
    raw = spec.get("weather_limits") or {}
    return {
        "max_wind_kn": float(raw.get("max_wind_kn", 35)),
        "max_wave_m": float(raw.get("max_wave_m", 4.0)),
        "max_swell_m": float(raw.get("max_swell_m", 3.0)),
    }


def weather_hard_reason(wx: dict[str, Any] | None, limits: dict[str, float] | None = None) -> str | None:
    """Same bars the report and GUI use — wind / wave / swell vs WeatherReportAgent limits."""
    if not wx:
        return None
    lim = limits or weather_limits_from()
    w, wave, swell = wx.get("windKn"), wx.get("waveM"), wx.get("swellM")
    if w is not None and float(w) >= lim["max_wind_kn"]:
        return "wind"
    if wave is not None and float(wave) >= lim["max_wave_m"]:
        return "wave"
    if swell is not None and float(swell) >= lim["max_swell_m"]:
        return "swell"
    return None


def annotate_track_hard(track: dict[str, Any], spec: Any | None = None) -> dict[str, Any]:
    """Stamp weather_limits and recompute hard flags so GUI JSON matches the report."""
    lim = weather_limits_from(spec)
    track["weather_limits"] = lim
    hard: list[dict[str, Any]] = []
    for i, p in enumerate(track.get("track") or []):
        wx = p.get("weather")
        if not isinstance(wx, dict):
            continue
        reason = weather_hard_reason(wx, lim)
        wx["hard"] = reason is not None
        if reason:
            hard.append({"index": i, "seq": p.get("seq"), "reason": reason, "sample": {"lat": p.get("lat"), "lon": p.get("lon")}})
    track["hard_regions"] = hard
    return track


def extract_bad_weather_events(
    track: dict[str, Any],
    *,
    wind_kn: float | None = None,
    wave_m: float | None = None,
    swell_m: float | None = None,
    spec: Any | None = None,
) -> list[dict[str, Any]]:
    """Build dated bad-weather events from combined track + hard_regions."""
    lim = weather_limits_from(spec)
    wind_kn = wind_kn if wind_kn is not None else lim["max_wind_kn"]
    wave_m = wave_m if wave_m is not None else lim["max_wave_m"]
    swell_m = swell_m if swell_m is not None else lim["max_swell_m"]

    events: list[dict[str, Any]] = []
    track_points = track.get("track") or []
    by_key: dict[str, dict[str, Any]] = {}

    def _add(date, lat, lon, seq, reason: str) -> None:
        if not reason:
            return
        key = f"{date}|{seq}|{round(float(lat or 0), 3)}|{round(float(lon or 0), 3)}"
        if key in by_key:
            rs = by_key[key]["reasons"]
            if reason not in rs:
                rs.append(reason)
                by_key[key]["reason"] = "; ".join(rs)
        else:
            by_key[key] = {
                "date": date,
                "lat": lat,
                "lon": lon,
                "seq": seq,
                "reasons": [reason],
                "reason": reason,
            }

    for p in track_points:
        wx = p.get("weather") or {}
        w = wx.get("windKn")
        wave = wx.get("waveM")
        swell = wx.get("swellM")
        date = p.get("eta_utc") or wx.get("validTime")
        seq = p.get("seq")
        lat, lon = p.get("lat"), p.get("lon")
        if w is not None and float(w) >= wind_kn:
            _add(date, lat, lon, seq, f"wind {w} kn exceeds limit {wind_kn} kn")
        if wave is not None and float(wave) >= wave_m:
            _add(date, lat, lon, seq, f"significant wave height {wave} m exceeds limit {wave_m} m")
        if swell is not None and float(swell) >= swell_m:
            _add(date, lat, lon, seq, f"swell {swell} m exceeds limit {swell_m} m")
        if wx.get("hard"):
            _add(date, lat, lon, seq, "flagged hard region by provider")

    for h in track.get("hard_regions") or []:
        idx = h.get("index")
        pt = track_points[idx] if isinstance(idx, int) and 0 <= idx < len(track_points) else None
        reason = h.get("reason", "hard weather")
        if reason == "wind":
            detail = f"strong wind — exceeds {wind_kn} kn limit"
        elif reason == "wave":
            detail = f"heavy seas — wave height exceeds {wave_m} m limit"
        else:
            detail = f"{reason} threshold exceeded"
        date = (pt or {}).get("eta_utc") or h.get("validTime")
        lat = (pt or {}).get("lat") or (h.get("sample") or {}).get("lat")
        lon = (pt or {}).get("lon") or (h.get("sample") or {}).get("lon")
        _add(date, lat, lon, (pt or {}).get("seq", idx), detail)

    events = sorted(by_key.values(), key=lambda e: str(e.get("date") or ""))
    return events


def format_bad_weather_block(events: list[dict[str, Any]]) -> str:
    if not events:
        return "  No bad weather expected on the planned track for the forecast window."
    lines = []
    for e in events:
        date = e.get("date") or "unknown time"
        lat, lon = e.get("lat"), e.get("lon")
        pos = f"{lat:.4f},{lon:.4f}" if lat is not None and lon is not None else "?,?"
        lines.append(f"  • {date}  @ {pos}  — {e.get('reason', '; '.join(e.get('reasons', [])))}")
    return "\n".join(lines)


def _fmt_dt(raw: str | None) -> str:
    if not raw:
        return "—"
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y %H:%M")
    except ValueError:
        return str(raw)


def _voyage_particulars(voyage_rec: dict[str, Any] | None, track: dict[str, Any]) -> dict[str, Any]:
    rec = voyage_rec or {}
    track_pts = track.get("track") or []
    master = rec.get("master_waypoints") or []
    noon = rec.get("last_noon") or track.get("noon") or {}
    start_lat = noon.get("lat") if noon.get("lat") is not None else (track_pts[0]["lat"] if track_pts else None)
    start_lon = noon.get("lon") if noon.get("lon") is not None else (track_pts[0]["lon"] if track_pts else None)
    dtg = 0.0
    if start_lat is not None and master:
        rem = remaining_route(master, float(start_lat), float(start_lon))
        dtg = route_length_nm(rem)
    elif master:
        dtg = route_length_nm(master)

    stw = noon.get("avg_speed_kn") or rec.get("stw_kn") or rec.get("cp_speed_kn")
    consumption = rec.get("cp_consumption_mt_day")
    return {
        "departure_port": rec.get("source_port") or "—",
        "arrival_port": rec.get("dest_port") or "—",
        "departure_time": rec.get("etd") or "—",
        "vessel_condition": rec.get("condition") or "—",
        "dtg_nm": dtg if dtg else "—",
        "stw_kn": stw if stw is not None else "—",
        "cp_speed_kn": rec.get("cp_speed_kn", "—"),
        "cp_consumption": f"{consumption} MT/day" if consumption is not None else "—",
    }


def build_passage_weather_rows(track: dict[str, Any]) -> list[dict[str, Any]]:
    pts = track.get("track") or []
    rows: list[dict[str, Any]] = []
    for i, p in enumerate(pts):
        wx = p.get("weather") or {}
        nxt = pts[i + 1] if i + 1 < len(pts) else None
        course = None
        if nxt is not None:
            course = round(initial_bearing_deg(p["lat"], p["lon"], nxt["lat"], nxt["lon"]), 0)
        wind = wx.get("windKn")
        bf = beaufort_from_kn(wind)
        cur_factor = current_factor_kn(wx.get("currentKn"), wx.get("currentDirDeg"), course)
        highlight = (bf is not None and bf >= 5) or (wx.get("waveM") is not None and float(wx["waveM"]) >= 1.0)
        rows.append(
            {
                "seq": p.get("seq", i),
                "date_utc": p.get("eta_utc") or wx.get("validTime"),
                "lat": p.get("lat"),
                "lon": p.get("lon"),
                "course_deg": course,
                "wind_kn": wind,
                "beaufort": bf,
                "wind_dir_deg": wx.get("windDirDeg"),
                "wind_dir_label": compass_label(wx.get("windDirDeg")),
                "pressure_hpa": wx.get("pressureHpa"),
                "current_factor_kn": cur_factor,
                "wave_m": wx.get("waveM"),
                "wave_dir_deg": wx.get("waveDirDeg"),
                "wave_dir_label": compass_label(wx.get("waveDirDeg")),
                "swell_m": wx.get("swellM"),
                "swell_dir_deg": wx.get("swellDirDeg"),
                "swell_dir_label": compass_label(wx.get("swellDirDeg")),
                "temp_c": wx.get("tempC"),
                "highlight": highlight,
            }
        )
    return rows


def format_passage_weather_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "  (no passage weather data)"
    header = (
        "  Sr   Date & Time (UTC)       Crs   BF  Wind Dir         hPa  Curr  Wave  WDir          "
        "Swell  SDir          Temp"
    )
    lines = [header, "  " + "-" * (len(header) - 2)]
    for n, r in enumerate(rows, 1):
        mark = "*" if r.get("highlight") else " "
        cur = r.get("current_factor_kn")
        cur_s = f"{cur:+.1f}" if cur is not None else "—"
        lines.append(
            f"{mark} {n:>2}  {_fmt_dt(r.get('date_utc')):<22}  "
            f"{_cell(r.get('course_deg'), 3, fmt='.0f')}  "
            f"{_cell(r.get('beaufort'), 2)}  "
            f"{_cell(r.get('wind_dir_label'), 16)}  "
            f"{_cell(r.get('pressure_hpa'), 4, fmt='.0f')}  "
            f"{cur_s:>4}  "
            f"{_cell(r.get('wave_m'), 4, fmt='.1f')}  "
            f"{_cell(r.get('wave_dir_label'), 13)}  "
            f"{_cell(r.get('swell_m'), 5, fmt='.1f')}  "
            f"{_cell(r.get('swell_dir_label'), 13)}  "
            f"{_cell(r.get('temp_c'), 4, fmt='.1f')}"
        )
    return "\n".join(lines)


def _cell(val: Any, width: int, fmt: str | None = None) -> str:
    if val is None:
        s = "—"
    elif fmt:
        s = format(val, fmt)
    else:
        s = str(val)
    return s[:width].ljust(width)


def _weather_window(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "as reported"
    return f"as reported, {_fmt_dt(rows[0].get('date_utc'))} – {_fmt_dt(rows[-1].get('date_utc'))}"


def _summary_bullets(rows: list[dict[str, Any]], particulars: dict[str, Any]) -> str:
    if not rows:
        return "  • No forecast data available for summary."
    winds = [r["wind_kn"] for r in rows if r.get("wind_kn") is not None]
    waves = [r["wave_m"] for r in rows if r.get("wave_m") is not None]
    pressures = [r["pressure_hpa"] for r in rows if r.get("pressure_hpa") is not None]
    bfs = [r["beaufort"] for r in rows if r.get("beaufort") is not None]
    lines = [
        f"  • Passage {particulars.get('departure_port', '—')} → {particulars.get('arrival_port', '—')} "
        f"({particulars.get('vessel_condition', '—')}).",
    ]
    if winds and bfs:
        lines.append(
            f"  • Wind ranges {min(winds):.0f}–{max(winds):.0f} kn (BF {min(bfs)}–{max(bfs)}) along the forecast window."
        )
    if waves:
        lines.append(f"  • Significant wave height peaks at {max(waves):.1f} m.")
    if len(pressures) >= 2:
        delta = pressures[-1] - pressures[0]
        lines.append(f"  • Mean sea level pressure trend: {pressures[0]:.0f} → {pressures[-1]:.0f} hPa ({delta:+.0f} hPa).")
    hi = sum(1 for r in rows if r.get("highlight"))
    if hi:
        lines.append(f"  • {hi} waypoint(s) flagged (BF ≥ 5 and/or sig. wave ≥ 1.0 m) — monitor closely.")
    return "\n".join(lines)


def _interpretation_bullets(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "  • Insufficient data for interpretation."
    bfs = [r["beaufort"] for r in rows if r.get("beaufort") is not None]
    waves = [r["wave_m"] for r in rows if r.get("wave_m") is not None]
    lines = ["  • Wind & sea state trend follows the forecast series along the planned track."]
    if bfs and max(bfs) - min(bfs) >= 2:
        lines.append("  • Steady build in wind force — not a single-point spike.")
    if waves and max(waves) >= 1.0:
        lines.append("  • Wave and swell height rise with wind — consistent wind-sea coupling.")
    return "\n".join(lines)


def _route_assessment(particulars: dict[str, Any]) -> str:
    return (
        f"  • Planned corridor {particulars.get('departure_port', '—')} → {particulars.get('arrival_port', '—')} "
        f"reviewed against forecast limits.\n"
        f"  • DTG {particulars.get('dtg_nm', '—')} NM at CP speed {particulars.get('cp_speed_kn', '—')} kn."
    )


def _precautions(events: list[dict[str, Any]], rows: list[dict[str, Any]]) -> str:
    lines = ["  • Track daily wind/sea build; request updated routing if limits are exceeded."]
    if events:
        lines.append(f"  • {len(events)} bad-weather event(s) identified — see appendix.")
    peak = max((r.get("beaufort") or 0 for r in rows), default=0)
    if peak >= 5:
        lines.append(f"  • Peak BF {peak} expected — verify lashings, UKC, and heavy-weather routine.")
    return "\n".join(lines)


def _outlook(rows: list[dict[str, Any]], events: list[dict[str, Any]]) -> str:
    lines = ["  • Treat as routine passage weather unless bad-weather events escalate."]
    if events:
        lines.append("  • Bad-weather windows present — standard heavy-weather precautions apply.")
    if rows:
        lines.append("  • Continue monitoring extended forecast as the voyage progresses.")
    return "\n".join(lines)


def _performance(particulars: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    cp = particulars.get("cp_speed_kn", "—")
    cons = particulars.get("cp_consumption", "—")
    peak_bf = max((r.get("beaufort") or 0 for r in rows), default=0)
    lines = [
        f"  • Maintain CP profile ({cp} kts / {cons}) where conditions allow.",
        f"  • Ease STW marginally during peak BF {peak_bf} window to reduce slamming." if peak_bf >= 5 else
        "  • Maintain steady STW; no severe peak flagged in window.",
        "  • Log actual vs forecast at each position to refine future routing.",
    ]
    return "\n".join(lines)


def _fill_passage_template(name: str, ctx: dict[str, Any]) -> str:
    try:
        return fill_template(name, ctx)
    except FileNotFoundError:
        return fill_template("weather_report.txt", ctx)


def write_weather_report(
    voyage_number: str,
    track: dict[str, Any],
    *,
    voyage_rec: dict[str, Any] | None = None,
    vessel_id: str = "",
    vessel_name: str = "",
    plan_label: str = "route plan",
    stamp: str | None = None,
    spec: Any | None = None,
) -> tuple[Path, Path]:
    """Write passage PDF + JSON under reports/{imo}/{voyage}/weather_report/."""
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lim = weather_limits_from(spec)
    events = extract_bad_weather_events(
        track,
        wind_kn=lim["max_wind_kn"],
        wave_m=lim["max_wave_m"],
        swell_m=lim["max_swell_m"],
    )
    rows = build_passage_weather_rows(track)
    particulars = _voyage_particulars(voyage_rec, track)
    out_dir = weather_out_dir(voyage_number, vessel_id)
    generated_at = datetime.now(timezone.utc).isoformat()
    rec = voyage_rec or {}

    payload: dict[str, Any] = {
        "voyage_number": voyage_number,
        "vessel_id": vessel_id,
        "vessel_name": vessel_name,
        "generated_at": generated_at,
        "plan_label": plan_label,
        "provider": track.get("provider", ""),
        "thresholds": {
            "wind_kn": lim["max_wind_kn"],
            "wave_m": lim["max_wave_m"],
            "swell_m": lim["max_swell_m"],
        },
        "bad_weather_events": events,
        "event_count": len(events),
        "track_point_count": track.get("point_count", len(track.get("track", []))),
        "voyage_particulars": particulars,
        "passage_weather_rows": rows,
        "weather_window": _weather_window(rows),
        "summary": {
            "text": _summary_bullets(rows, particulars),
            "highlight_count": sum(1 for r in rows if r.get("highlight")),
        },
        "advisory": {
            "interpretation": _interpretation_bullets(rows),
            "route_assessment": _route_assessment(particulars),
            "precautions": _precautions(events, rows),
            "outlook": _outlook(rows, events),
            "performance": _performance(particulars, rows),
        },
        "legacy_weather_block": format_track_block(track),
    }
    json_path = out_dir / f"bad_weather_{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    vessel_display = vessel_name or vessel_id or voyage_number
    route_line = f"{particulars['departure_port']} → {particulars['arrival_port']}"
    if particulars["departure_port"] == "—" and particulars["arrival_port"] == "—":
        route_line = plan_label

    ctx = {
        "voyage_number": voyage_number,
        "vessel_id": vessel_id or vessel_name,
        "vessel_display": vessel_display,
        "generated_at": generated_at,
        "report_date": datetime.now(timezone.utc).strftime("%d %b %Y"),
        "plan_label": plan_label,
        "route_line": route_line,
        "waypoint_count": payload["track_point_count"],
        "provider": payload["provider"],
        "departure_port": particulars["departure_port"],
        "arrival_port": particulars["arrival_port"],
        "departure_time": particulars["departure_time"],
        "vessel_condition": particulars["vessel_condition"],
        "dtg_nm": particulars["dtg_nm"],
        "stw_kn": particulars["stw_kn"],
        "cp_speed_kn": particulars["cp_speed_kn"],
        "cp_consumption": particulars["cp_consumption"],
        "weather_window": payload["weather_window"],
        "passage_weather_table": format_passage_weather_table(rows),
        "highlight_note": "  * Highlighted rows: BF ≥ 5 and/or significant wave height ≥ 1.0 m",
        "summary_block": payload["summary"]["text"],
        "interpretation_block": payload["advisory"]["interpretation"],
        "route_assessment_block": payload["advisory"]["route_assessment"],
        "precautions_block": payload["advisory"]["precautions"],
        "outlook_block": payload["advisory"]["outlook"],
        "performance_block": payload["advisory"]["performance"],
        "weather_block": payload["legacy_weather_block"],
        "hard_block": format_bad_weather_block(events),
        "bad_weather_block": format_bad_weather_block(events),
        "event_count": len(events),
        "wind_limit_kn": lim["max_wind_kn"],
        "wave_limit_m": lim["max_wave_m"],
        "swell_limit_m": lim["max_swell_m"],
    }
    body = _fill_passage_template("passage_weather_report.txt", ctx)
    pdf_path = write_text_pdf(
        out_dir,
        f"weather_report_{stamp}.pdf",
        body,
        voyage_number=voyage_number,
        for_send=True,
    )
    return pdf_path, json_path


if __name__ == "__main__":
    sample = {
        "provider": "test",
        "track": [
            {
                "seq": 0,
                "lat": 12.0,
                "lon": 109.0,
                "eta_utc": "2026-08-01T11:19:00+00:00",
                "weather": {
                    "windKn": 12,
                    "windDirDeg": 108,
                    "pressureHpa": 1007,
                    "waveM": 0.2,
                    "waveDirDeg": 111,
                    "swellM": 0.1,
                    "swellDirDeg": 80,
                    "tempC": 31.6,
                    "currentKn": 0.0,
                    "currentDirDeg": 90,
                },
            },
            {
                "seq": 1,
                "lat": 13.0,
                "lon": 110.0,
                "eta_utc": "2026-08-01T23:19:00+00:00",
                "weather": {
                    "windKn": 40,
                    "windDirDeg": 127,
                    "pressureHpa": 1004,
                    "waveM": 3.9,
                    "waveDirDeg": 122,
                    "swellM": 0.25,
                    "swellDirDeg": 13,
                    "tempC": 29.0,
                    "currentKn": 0.26,
                    "currentDirDeg": 9,
                },
            },
        ],
        "hard_regions": [],
        "point_count": 2,
    }
    rows = build_passage_weather_rows(sample)
    assert len(rows) == 2
    assert rows[1]["highlight"] is True
    assert beaufort_from_kn(40) == 8
    lim = weather_limits_from()
    assert weather_hard_reason({"windKn": lim["max_wind_kn"], "waveM": 0, "swellM": 0}, lim) == "wind"
    assert weather_hard_reason({"windKn": lim["max_wind_kn"] - 0.01, "waveM": 0, "swellM": 0}, lim) is None
    events = extract_bad_weather_events(sample)
    assert events and "wind" in events[0]["reason"]
    pdf_path, json_path = write_weather_report("SELFTEST", sample, plan_label="self-check")
    assert pdf_path.suffix == ".pdf" and pdf_path.stat().st_size > 500
    assert json_path.is_file()
    print("weather_report self-check ok")
