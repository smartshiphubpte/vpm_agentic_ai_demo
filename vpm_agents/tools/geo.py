"""Deterministic route math — 6-hour waypoint walk along a master route."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """True course (0–360°) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlmb = math.radians(lon2 - lon1)
    x = math.sin(dlmb) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlmb)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def route_length_nm(points: list[list[float]]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        total += haversine_nm(a[0], a[1], b[0], b[1])
    return round(total, 1)


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_nm = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r_nm * math.asin(min(1.0, math.sqrt(a)))


def interpolate(a: list[float], b: list[float], t: float) -> list[float]:
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]


def six_hour_waypoints(
    master: list[list[float]],
    speed_kn: float,
    start: datetime | None = None,
    horizon_hours: float | None = None,
    interval_h: float = 6.0,
) -> list[dict]:
    """Walk the master route at CP speed; emit a point every interval_h hours."""
    if len(master) < 2:
        raise ValueError("master route needs at least 2 points")
    if speed_kn <= 0:
        raise ValueError("cp_speed_kn must be > 0")

    start = start or datetime.now(timezone.utc)
    step_nm = speed_kn * interval_h

    points: list[dict] = []
    seq = 0
    points.append(
        {
            "seq": seq,
            "lat": master[0][0],
            "lon": master[0][1],
            "eta_utc": start.isoformat(),
        }
    )
    seq += 1

    leg_i = 0
    progress_on_leg = 0.0
    elapsed_h = 0.0
    max_h = horizon_hours if horizon_hours is not None else float("inf")

    while leg_i < len(master) - 1 and elapsed_h + interval_h <= max_h + 1e-9:
        a, b = master[leg_i], master[leg_i + 1]
        leg_nm = haversine_nm(a[0], a[1], b[0], b[1])
        if leg_nm < 1e-6:
            leg_i += 1
            progress_on_leg = 0.0
            continue

        remain = leg_nm - progress_on_leg
        if remain >= step_nm:
            progress_on_leg += step_nm
            t = progress_on_leg / leg_nm
            lat, lon = interpolate(a, b, t)
            elapsed_h += interval_h
            points.append(
                {
                    "seq": seq,
                    "lat": round(lat, 5),
                    "lon": round(lon, 5),
                    "eta_utc": (start + timedelta(hours=elapsed_h)).isoformat(),
                }
            )
            seq += 1
        else:
            leftover_nm = step_nm - remain
            leg_i += 1
            progress_on_leg = 0.0
            while leftover_nm > 0 and leg_i < len(master) - 1:
                a, b = master[leg_i], master[leg_i + 1]
                leg_nm = haversine_nm(a[0], a[1], b[0], b[1])
                if leg_nm < 1e-6:
                    leg_i += 1
                    continue
                if leftover_nm < leg_nm:
                    progress_on_leg = leftover_nm
                    t = progress_on_leg / leg_nm
                    lat, lon = interpolate(a, b, t)
                    elapsed_h += interval_h
                    points.append(
                        {
                            "seq": seq,
                            "lat": round(lat, 5),
                            "lon": round(lon, 5),
                            "eta_utc": (start + timedelta(hours=elapsed_h)).isoformat(),
                        }
                    )
                    seq += 1
                    leftover_nm = 0
                else:
                    leftover_nm -= leg_nm
                    leg_i += 1
                    progress_on_leg = 0.0
            if leftover_nm > 0:
                break

    last = master[-1]
    if points[-1]["lat"] != last[0] or points[-1]["lon"] != last[1]:
        if horizon_hours is None or elapsed_h < max_h:
            cur = [points[-1]["lat"], points[-1]["lon"]]
            rem = haversine_nm(cur[0], cur[1], last[0], last[1])
            eta = start + timedelta(hours=elapsed_h + rem / speed_kn)
            points.append(
                {
                    "seq": seq,
                    "lat": last[0],
                    "lon": last[1],
                    "eta_utc": eta.isoformat(),
                }
            )
    return points


def nearest_master_index(master: list[list[float]], lat: float, lon: float) -> int:
    best_i, best_d = 0, float("inf")
    for i, p in enumerate(master):
        d = haversine_nm(lat, lon, p[0], p[1])
        if d < best_d:
            best_i, best_d = i, d
    return best_i


def remaining_route(master: list[list[float]], lat: float, lon: float) -> list[list[float]]:
    i = nearest_master_index(master, lat, lon)
    return [[lat, lon]] + master[i + 1 :]


def min_distance_to_route(lat: float, lon: float, route: list[list[float]]) -> float:
    """Minimum great-circle distance (NM) from point to any route vertex.

    ponytail: vertex scan is fine for typical voyage WPs; upgrade to segment
    distance if storms sit between waypoints often.
    """
    if not route:
        return float("inf")
    return min(haversine_nm(lat, lon, p[0], p[1]) for p in route)
