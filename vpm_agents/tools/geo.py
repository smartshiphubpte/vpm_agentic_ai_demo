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


def _merc_y(lat: float) -> float:
    """Web Mercator Y (unit sphere) — same chord Leaflet draws between two WPs."""
    lat = max(-89.999, min(89.999, float(lat)))
    return math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0))


def _inv_merc_y(y: float) -> float:
    return math.degrees(2.0 * math.atan(math.exp(y)) - math.pi / 2.0)


def interpolate(a: list[float], b: list[float], t: float) -> list[float]:
    """Point at fraction t along the straight segment A→B (on the master-route line)."""
    t = max(0.0, min(1.0, float(t)))
    lat1, lon1 = float(a[0]), float(a[1])
    lat2, lon2 = float(b[0]), float(b[1])
    if t <= 1e-12:
        return [lat1, lon1]
    if t >= 1.0 - 1e-12:
        return [lat2, lon2]
    lon = lon1 + (lon2 - lon1) * t
    try:
        lat = _inv_merc_y(_merc_y(lat1) + (_merc_y(lat2) - _merc_y(lat1)) * t)
    except (ValueError, OverflowError):
        lat = lat1 + (lat2 - lat1) * t
    return [lat, lon]


def closest_on_segment(
    lat: float, lon: float, a: list[float], b: list[float]
) -> tuple[float, float, float, float]:
    """Project P onto segment AB. Returns (lat, lon, t, dist_nm)."""
    ax, ay = float(a[1]), _merc_y(a[0])
    bx, by = float(b[1]), _merc_y(b[0])
    px, py = float(lon), _merc_y(lat)
    dx, dy = bx - ax, by - ay
    len2 = dx * dx + dy * dy
    if len2 < 1e-18:
        qlat, qlon, t = float(a[0]), float(a[1]), 0.0
    else:
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len2))
        qlat, qlon = interpolate(a, b, t)
    return qlat, qlon, t, haversine_nm(lat, lon, qlat, qlon)


def snap_to_master(
    master: list[list[float]], lat: float, lon: float
) -> tuple[int, float, float, float]:
    """Nearest point on the master polyline. Returns (leg_index, t, lat, lon)."""
    best: tuple[float, int, float, float, float] | None = None
    for i in range(len(master) - 1):
        qlat, qlon, t, d = closest_on_segment(lat, lon, master[i], master[i + 1])
        if best is None or d < best[0]:
            best = (d, i, t, qlat, qlon)
    if best is None:
        return 0, 0.0, float(master[0][0]), float(master[0][1])
    _d, i, t, qlat, qlon = best
    return i, t, qlat, qlon


def six_hour_waypoints(
    master: list[list[float]],
    speed_kn: float,
    start: datetime | None = None,
    horizon_hours: float | None = None,
    interval_h: float = 6.0,
) -> list[dict]:
    """Walk the master polyline at CP speed; every sample sits on a master segment."""
    if len(master) < 2:
        raise ValueError("master route needs at least 2 points")
    if speed_kn <= 0:
        raise ValueError("cp_speed_kn must be > 0")

    start = start or datetime.now(timezone.utc)
    step_nm = speed_kn * interval_h

    def _pt(seq: int, lat: float, lon: float, hours: float) -> dict:
        return {
            "seq": seq,
            "lat": lat,
            "lon": lon,
            "eta_utc": (start + timedelta(hours=hours)).isoformat(),
        }

    points: list[dict] = [_pt(0, float(master[0][0]), float(master[0][1]), 0.0)]
    seq = 1
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
            lat, lon = interpolate(a, b, progress_on_leg / leg_nm)
            elapsed_h += interval_h
            points.append(_pt(seq, lat, lon, elapsed_h))
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
                    lat, lon = interpolate(a, b, progress_on_leg / leg_nm)
                    elapsed_h += interval_h
                    points.append(_pt(seq, lat, lon, elapsed_h))
                    seq += 1
                    leftover_nm = 0
                else:
                    leftover_nm -= leg_nm
                    leg_i += 1
                    progress_on_leg = 0.0
            if leftover_nm > 0:
                break

    last = master[-1]
    if haversine_nm(points[-1]["lat"], points[-1]["lon"], last[0], last[1]) > 1e-4:
        if horizon_hours is None or elapsed_h < max_h:
            rem = haversine_nm(points[-1]["lat"], points[-1]["lon"], last[0], last[1])
            points.append(_pt(seq, float(last[0]), float(last[1]), elapsed_h + rem / speed_kn))
    return points


def nearest_master_index(master: list[list[float]], lat: float, lon: float) -> int:
    best_i, best_d = 0, float("inf")
    for i, p in enumerate(master):
        d = haversine_nm(lat, lon, p[0], p[1])
        if d < best_d:
            best_i, best_d = i, d
    return best_i


def remaining_route(master: list[list[float]], lat: float, lon: float) -> list[list[float]]:
    """Master polyline from the snap of (lat,lon) onto a segment through destination."""
    if len(master) < 2:
        return [[lat, lon]] + list(master)
    i, t, qlat, qlon = snap_to_master(master, lat, lon)
    if t >= 1.0 - 1e-9:
        rest = master[i + 1 :]
        return rest if rest else [master[-1]]
    return [[qlat, qlon]] + master[i + 1 :]


def min_distance_to_route(lat: float, lon: float, route: list[list[float]]) -> float:
    """Minimum great-circle distance (NM) from point to any route vertex.

    ponytail: vertex scan is fine for typical voyage WPs; upgrade to segment
    distance if storms sit between waypoints often.
    """
    if not route:
        return float("inf")
    return min(haversine_nm(lat, lon, p[0], p[1]) for p in route)


if __name__ == "__main__":
    a, b = [0.0, 0.0], [0.0, 10.0]
    mid = interpolate(a, b, 0.5)
    assert abs(mid[0]) < 1e-9 and abs(mid[1] - 5.0) < 1e-9
    master = [[0.0, 0.0], [0.0, 10.0], [5.0, 10.0]]
    qlat, qlon, t, _d = closest_on_segment(1.0, 5.0, master[0], master[1])
    assert 0.4 < t < 0.6 and abs(qlat) < 0.05
    rem = remaining_route(master, 1.0, 4.0)
    assert rem[0][1] > 0 and rem[-1] == master[-1]
    pts = six_hour_waypoints(master, 10.0, interval_h=6.0, horizon_hours=48)
    for p in pts:
        _i, _t, slat, slon = snap_to_master(master, p["lat"], p["lon"])
        assert haversine_nm(p["lat"], p["lon"], slat, slon) < 0.05, "6h WP left the master line"
    print("geo self-check ok")
