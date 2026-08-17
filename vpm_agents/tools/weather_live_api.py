"""Live marine weather via Open-Meteo (forecast + marine APIs).

No API key. Returns the same shape as MockBackend / voyagepm_be:
  {points: [{lat, lon, windKn, waveM, swellM, validTime}], hardRegions, provider}
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from vpm_agents.config import settings
from vpm_agents.tools.weather_report import weather_hard_reason, weather_limits_from

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
_PROVIDER = "Open-Meteo"
# ponytail: Open-Meteo multi-location URL length — chunk if routes get huge; upgrade: their bulk API
_CHUNK = 40
_MS_TO_KN = 1.943844  # only if unit forgotten


def fetch_weather_along_route(waypoints: list) -> dict[str, Any]:
    """Forecast wind + waves at each waypoint (nearest hour to ETA, else stepped)."""
    if not waypoints:
        return {"points": [], "hardRegions": [], "provider": _PROVIDER}

    points: list[dict[str, Any]] = []
    hard: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    interval_h = float(settings.waypoint_interval_hours)
    limits = weather_limits_from()

    # No agent kill-on-slow — only socket wait. Time is not a calc constraint.
    with httpx.Client(timeout=300.0, follow_redirects=True) as client:
        for start in range(0, len(waypoints), _CHUNK):
            chunk = waypoints[start : start + _CHUNK]
            lats = ",".join(str(float(p["lat"])) for p in chunk)
            lons = ",".join(str(float(p["lon"])) for p in chunk)
            forecast_by_i = _fetch_hourly(
                client,
                FORECAST_URL,
                lats,
                lons,
                hourly="wind_speed_10m,wind_direction_10m,surface_pressure,temperature_2m",
                extra={"wind_speed_unit": "kn"},
            )
            wave_by_i = _fetch_hourly(
                client,
                MARINE_URL,
                lats,
                lons,
                hourly=(
                    "wave_height,wave_direction,swell_wave_height,swell_wave_direction,"
                    "ocean_current_velocity,ocean_current_direction"
                ),
            )

            for j, p in enumerate(chunk):
                idx = start + j
                when = _target_time(p, now, idx, interval_h)
                fc = forecast_by_i.get(j)
                mc = wave_by_i.get(j)
                wind = _nearest(fc, when, "wind_speed_10m")
                wind_dir = _nearest(fc, when, "wind_direction_10m")
                pressure = _nearest(fc, when, "surface_pressure")
                temp_c = _nearest(fc, when, "temperature_2m")
                wave = _nearest(mc, when, "wave_height")
                wave_dir = _nearest(mc, when, "wave_direction")
                swell = _nearest(mc, when, "swell_wave_height")
                swell_dir = _nearest(mc, when, "swell_wave_direction")
                cur_vel = _nearest(mc, when, "ocean_current_velocity")
                cur_dir = _nearest(mc, when, "ocean_current_direction")
                if wind is not None and wind > 200:  # mis-unit guard
                    wind = round(wind * _MS_TO_KN, 1)
                wp = {
                    "lat": float(p["lat"]),
                    "lon": float(p["lon"]),
                    "windKn": round(float(wind), 1) if wind is not None else None,
                    "windDirDeg": round(float(wind_dir), 0) if wind_dir is not None else None,
                    "pressureHpa": round(float(pressure), 0) if pressure is not None else None,
                    "tempC": round(float(temp_c), 1) if temp_c is not None else None,
                    "waveM": round(float(wave), 1) if wave is not None else None,
                    "waveDirDeg": round(float(wave_dir), 0) if wave_dir is not None else None,
                    "swellM": round(float(swell), 1) if swell is not None else None,
                    "swellDirDeg": round(float(swell_dir), 0) if swell_dir is not None else None,
                    "currentKn": round(float(cur_vel) * 0.539957, 2) if cur_vel is not None else None,
                    "currentDirDeg": round(float(cur_dir), 0) if cur_dir is not None else None,
                    "validTime": when.isoformat(),
                }
                points.append(wp)
                reason = weather_hard_reason(wp, limits)
                if reason:
                    hard.append({"index": idx, "reason": reason, "sample": wp})

    return {"points": points, "hardRegions": hard, "provider": _PROVIDER}


def fetch_weather_point(lat: float, lon: float) -> dict[str, Any]:
    wx = fetch_weather_along_route([{"lat": lat, "lon": lon}])
    pt = (wx.get("points") or [{}])[0]
    return {**pt, "provider": _PROVIDER}


def _target_time(p: dict, now: datetime, index: int, interval_h: float) -> datetime:
    raw = p.get("eta_utc") or p.get("eta") or p.get("validTime")
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    return now + timedelta(hours=interval_h * index)


def _fetch_hourly(
    client: httpx.Client,
    url: str,
    lats: str,
    lons: str,
    *,
    hourly: str,
    extra: dict[str, str] | None = None,
) -> dict[int, dict[str, Any]]:
    """Map chunk-local index → {time: [...], <var>: [...]} hourly block."""
    params: dict[str, Any] = {
        "latitude": lats,
        "longitude": lons,
        "hourly": hourly,
        "timezone": "UTC",
    }
    if extra:
        params.update(extra)
    try:
        r = client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return {}

    rows = data if isinstance(data, list) else [data]
    out: dict[int, dict[str, Any]] = {}
    for i, row in enumerate(rows):
        if isinstance(row, dict) and isinstance(row.get("hourly"), dict):
            out[i] = row["hourly"]
    return out


def _nearest(hourly: dict[str, Any] | None, when: datetime, key: str) -> float | None:
    if not hourly:
        return None
    times = hourly.get("time") or []
    vals = hourly.get(key) or []
    if not times or not vals or len(times) != len(vals):
        return None
    target = when.replace(minute=0, second=0, microsecond=0)
    best_i, best_d = 0, None
    for i, t in enumerate(times):
        try:
            dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        d = abs((dt - target).total_seconds())
        if best_d is None or d < best_d:
            best_d, best_i = d, i
    v = vals[best_i]
    return None if v is None else float(v)


def _hard_reason(wp: dict[str, Any], limits: dict[str, float] | None = None) -> str | None:
    return weather_hard_reason(wp, limits)


if __name__ == "__main__":
    # Offline unit check — nearest-hour picker
    hourly = {
        "time": ["2026-08-12T00:00", "2026-08-12T06:00", "2026-08-12T12:00"],
        "wind_speed_10m": [10.0, 40.0, 20.0],
    }
    when = datetime(2026, 8, 12, 5, 30, tzinfo=timezone.utc)
    assert _nearest(hourly, when, "wind_speed_10m") == 40.0
    assert _hard_reason({"windKn": 40, "waveM": 1.0, "swellM": 1.0}) == "wind"
    print("weather_live_api self-check ok")
