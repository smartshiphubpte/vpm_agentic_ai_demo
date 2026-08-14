"""Marine unit helpers — Beaufort, compass labels, lat/lon formatting."""

from __future__ import annotations

import math

_COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def beaufort_from_kn(wind_kn: float | None) -> int | None:
    if wind_kn is None:
        return None
    w = float(wind_kn)
    thresholds = (1, 4, 7, 11, 17, 22, 28, 34, 41, 48, 56, 64)
    for bf, limit in enumerate(thresholds):
        if w < limit:
            return bf
    return 12


def compass_label(deg: float | None) -> str:
    if deg is None:
        return "—"
    d = float(deg) % 360
    idx = int((d + 11.25) / 22.5) % 16
    return f"{_COMPASS[idx]} ({d:.0f}°)"


def format_latlon_dms(lat: float | None, lon: float | None) -> str:
    if lat is None or lon is None:
        return "—"
    return f"{_dms(lat, 'N', 'S')}, {_dms(lon, 'E', 'W')}"


def _dms(val: float, pos: str, neg: str) -> str:
    hemi = pos if val >= 0 else neg
    v = abs(float(val))
    deg = int(v)
    minutes = (v - deg) * 60
    return f"{deg}°{minutes:05.2f}'{hemi}"


def current_factor_kn(
    current_speed_kn: float | None,
    current_dir_deg: float | None,
    course_deg: float | None,
) -> float | None:
    """Signed current component along course (+ = following)."""
    if current_speed_kn is None or current_dir_deg is None or course_deg is None:
        return None
    diff = math.radians(float(current_dir_deg) - float(course_deg))
    return round(float(current_speed_kn) * math.cos(diff), 1)


def kmh_to_kn(speed_kmh: float | None) -> float | None:
    if speed_kmh is None:
        return None
    return round(float(speed_kmh) * 0.539957, 2)
