"""Merge waypoint plans + weather into one track JSON."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SHIP = "🚢"
WAYPOINT = "📍"


def build_voyage_track(
    voyage_number: str,
    plan: list[dict],
    weather: dict[str, Any],
    *,
    noon: dict[str, Any] | None = None,
    vessel_name: str = "",
    provider: str = "",
) -> dict[str, Any]:
    """One JSON: lat/lon, ETA, weather at each point; ship marker on current position."""
    wx_points = weather.get("points") or []
    hard = weather.get("hardRegions") or []

    track: list[dict[str, Any]] = []
    # Current ship position from noon (if provided)
    if noon and noon.get("lat") is not None:
        track.append(
            {
                "seq": 0,
                "lat": noon["lat"],
                "lon": noon["lon"],
                "eta_utc": noon.get("observed_at") or datetime.now(timezone.utc).isoformat(),
                "marker": SHIP,
                "is_current_position": True,
                "weather": _weather_at_index(wx_points, 0),
            }
        )

    for i, p in enumerate(plan):
        # skip duplicate if plan starts at same point as noon
        if noon and i == 0 and abs(p["lat"] - noon["lat"]) < 0.01 and abs(p["lon"] - noon["lon"]) < 0.01:
            if track and track[0]["is_current_position"]:
                track[0]["eta_utc"] = p.get("eta_utc", track[0]["eta_utc"])
                track[0]["weather"] = _weather_at_index(wx_points, i) or track[0]["weather"]
            continue
        wx_idx = i if not noon else i
        track.append(
            {
                "seq": p.get("seq", i + 1),
                "lat": p["lat"],
                "lon": p["lon"],
                "eta_utc": p.get("eta_utc"),
                "marker": WAYPOINT if not (noon and i == 0) else SHIP,
                "is_current_position": False,
                "weather": _weather_at_index(wx_points, wx_idx),
            }
        )

    return {
        "voyage_number": voyage_number,
        "vessel_name": vessel_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider or weather.get("provider", ""),
        "noon": noon,
        "track": track,
        "hard_regions": hard,
        "point_count": len(track),
    }


def _weather_at_index(wx_points: list[dict], idx: int) -> dict[str, Any] | None:
    if idx >= len(wx_points):
        return None
    w = wx_points[idx]
    return {
        "windKn": w.get("windKn"),
        "windDirDeg": w.get("windDirDeg"),
        "pressureHpa": w.get("pressureHpa"),
        "tempC": w.get("tempC"),
        "waveM": w.get("waveM"),
        "waveDirDeg": w.get("waveDirDeg"),
        "swellM": w.get("swellM"),
        "swellDirDeg": w.get("swellDirDeg"),
        "currentKn": w.get("currentKn"),
        "currentDirDeg": w.get("currentDirDeg"),
        "validTime": w.get("validTime") or w.get("valid_time"),
        "hard": w.get("hard", False),
    }


def format_track_block(track: dict[str, Any], limit: int = 40) -> str:
    lines = []
    for p in track.get("track", [])[:limit]:
        wx = p.get("weather") or {}
        lines.append(
            f"  {p.get('marker', '')} #{p.get('seq', '?'):03d}  "
            f"{p['lat']:.4f},{p['lon']:.4f}  ETA {p.get('eta_utc', '?')}  "
            f"wind={wx.get('windKn', '?')}kn wave={wx.get('waveM', '?')}m"
        )
    if len(track.get("track", [])) > limit:
        lines.append(f"  ... ({len(track['track']) - limit} more)")
    return "\n".join(lines) if lines else "  (none)"
