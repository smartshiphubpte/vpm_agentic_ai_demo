"""Write a port-weather PDF + txt from a single-point hourly forecast."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from port_weather.config import settings
from vpm_agents.config import settings as vpm_settings
from vpm_agents.tools.folder_layout import PORT_WEATHER_REPORT, incoming_dir, voyage_report_dir
from vpm_agents.tools.marine_units import format_latlon_dms
from vpm_agents.tools.report_charts import weather_series_charts
from vpm_agents.tools.report_narrative import compact_wx_facts, llm_section
from vpm_agents.tools.templates import fill_template, write_text_pdf
from vpm_agents.tools.weather_report import (
    extract_bad_weather_events,
    format_bad_weather_block,
    format_passage_weather_table,
    build_passage_weather_rows,
    weather_limits_from,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return _utc_now().strftime("%Y%m%dT%H%M%SZ")


def _fmt_dt(raw: str | None) -> str:
    if not raw:
        return "—"
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y %H:%M")
    except ValueError:
        return str(raw)


def hourly_waypoints(lat: float, lon: float, hours: int) -> list[dict[str, Any]]:
    start = _utc_now().replace(minute=0, second=0, microsecond=0)
    n = max(1, int(hours))
    return [
        {"lat": lat, "lon": lon, "eta_utc": (start + timedelta(hours=i)).isoformat()}
        for i in range(n)
    ]


def track_from_wx(lat: float, lon: float, wx: dict[str, Any]) -> dict[str, Any]:
    pts = []
    for i, p in enumerate(wx.get("points") or []):
        pts.append(
            {
                "seq": i,
                "lat": p.get("lat", lat),
                "lon": p.get("lon", lon),
                "eta_utc": p.get("validTime"),
                "weather": p,
            }
        )
    return {
        "provider": wx.get("provider", ""),
        "track": pts,
        "hard_regions": wx.get("hardRegions") or [],
        "point_count": len(pts),
    }


def _summary(rows: list[dict[str, Any]], port: str) -> str:
    if not rows:
        return f"  • No forecast data available for {port}."
    winds = [r["wind_kn"] for r in rows if r.get("wind_kn") is not None]
    waves = [r["wave_m"] for r in rows if r.get("wave_m") is not None]
    bfs = [r["beaufort"] for r in rows if r.get("beaufort") is not None]
    lines = [f"  • Vessel in port at {port} for the forecast window."]
    if winds and bfs:
        lines.append(
            f"  • Wind {min(winds):.0f}–{max(winds):.0f} kn (BF {min(bfs)}–{max(bfs)})."
        )
    if waves:
        lines.append(f"  • Significant wave height peaks at {max(waves):.1f} m.")
    hi = sum(1 for r in rows if r.get("highlight"))
    if hi:
        lines.append(f"  • {hi} hour(s) flagged (BF ≥ 5 and/or sig. wave ≥ 1.0 m).")
    return "\n".join(lines)


def _outlook(rows: list[dict[str, Any]], events: list[dict[str, Any]]) -> str:
    lines = ["  • Remain in port until the next departure report; this forecast refreshes on the configured interval."]
    if events:
        lines.append("  • Bad-weather hours present — review cargo work and gangway/anchorage limits.")
    elif rows:
        lines.append("  • No threshold exceedance in the window — routine port watch.")
    return "\n".join(lines)


def _precautions(events: list[dict[str, Any]], rows: list[dict[str, Any]]) -> str:
    lines = [
        "  • Monitor local port / terminal weather limits and harbour master notices.",
        "  • Confirm mooring / anchorage setup against peak wind and swell in this window.",
    ]
    peak = max((r.get("beaufort") or 0 for r in rows), default=0)
    if peak >= 5:
        lines.append(f"  • Peak BF {peak} — extra lines, watch gangway, suspend non-essential deck work if required.")
    if events:
        lines.append(f"  • {len(events)} bad-weather hour(s) — see appendix.")
    return "\n".join(lines)


def _window(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "as reported"
    return f"{_fmt_dt(rows[0].get('date_utc'))} – {_fmt_dt(rows[-1].get('date_utc'))} UTC"


def _port_narratives(
    rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    port: str,
) -> dict[str, str]:
    facts = compact_wx_facts(rows, extra={"port": port, "bad_weather_events": events[:12], "event_count": len(events)})
    return {
        "summary_block": llm_section(
            "Port Weather section 2.1 Summary. 3–5 bullets for a vessel alongside / at anchorage.",
            facts,
            _summary(rows, port),
        ),
        "outlook_block": llm_section(
            "Section 3.1 Forecast outlook while in port. 2–4 bullets; cargo/mooring implications.",
            facts,
            _outlook(rows, events),
        ),
        "precautions_block": llm_section(
            "Section 3.2 Precautions alongside / at anchorage. 3–6 actionable bullets.",
            facts,
            _precautions(events, rows),
        ),
    }


def write_port_weather_report(
    *,
    voyage_number: str,
    vessel_id: str,
    vessel_name: str,
    port_name: str,
    lat: float,
    lon: float,
    arrived_at: str,
    wx: dict[str, Any],
    out_dir: Path | None = None,
    stamp: str | None = None,
) -> tuple[Path, Path]:
    """PDF → incoming/ (report sender); txt stays next to it in the voyage folder."""
    stamp = stamp or _stamp()
    track = track_from_wx(lat, lon, wx)
    rows = build_passage_weather_rows(track)
    events = extract_bad_weather_events(track)
    lim = weather_limits_from()
    generated_at = _utc_now().isoformat()
    vessel_display = vessel_name or vessel_id or voyage_number
    port = port_name or "—"
    reports_root = Path(out_dir or vpm_settings.reports_out_dir)
    base = voyage_report_dir(reports_root, vessel_id, voyage_number, PORT_WEATHER_REPORT)
    incoming_dir(base)
    narratives = _port_narratives(rows, events, port)
    charts = weather_series_charts(base / "charts", rows, stem=f"port_{stamp}")

    ctx = {
        "voyage_number": voyage_number,
        "vessel_id": vessel_id or "",
        "vessel_name": vessel_name or "",
        "vessel_display": vessel_display,
        "port_name": port,
        "position": format_latlon_dms(lat, lon),
        "lat": lat,
        "lon": lon,
        "arrived_at": _fmt_dt(arrived_at),
        "report_date": _utc_now().strftime("%d %b %Y"),
        "generated_at": generated_at,
        "weather_window": _window(rows),
        "interval_hours": settings.interval_hours,
        "forecast_table": format_passage_weather_table(rows),
        "highlight_note": "  * Highlighted rows: BF ≥ 5 and/or significant wave height ≥ 1.0 m",
        "summary_block": narratives["summary_block"],
        "outlook_block": narratives["outlook_block"],
        "precautions_block": narratives["precautions_block"],
        "bad_weather_block": format_bad_weather_block(events),
        "event_count": len(events),
        "provider": track.get("provider") or "",
        "wind_limit_kn": lim["max_wind_kn"],
        "wave_limit_m": lim["max_wave_m"],
        "swell_limit_m": lim["max_swell_m"],
    }
    body = fill_template(settings.template, ctx)
    txt_path = base / f"port_weather_{stamp}.txt"
    txt_path.write_text(body, encoding="utf-8")
    pdf_path = write_text_pdf(
        base,
        f"port_weather_{stamp}.pdf",
        body,
        voyage_number=voyage_number,
        for_send=True,
        images=charts,
    )
    return pdf_path, txt_path
