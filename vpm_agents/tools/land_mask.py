"""Ship hard-rule: detect landfall on a route (stdlib-only).

ponytail: inland-shrunk continent rings so ports/straits stay water, but
great-circle legs that cut continents still fail. Ceiling: misses thin
peninsulas / tiny islands, ~1–2° coast slack; upgrade to GSHHS/land-mask
grid for coastal metre-class routing.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

from vpm_agents.tools.geo import haversine_nm, interpolate

# Closed rings (lat, lon). Intentionally inset from coasts so Singapore, Suez,
# Channel, Malacca, etc. remain water while Kansas→Pacific chord still hits land.
_LAND_RINGS: tuple[tuple[tuple[float, float], ...], ...] = (
    # Africa (inland)
    (
        (32.0, 0.0), (30.0, 25.0), (22.0, 33.0), (10.0, 40.0), (0.0, 35.0),
        (-15.0, 30.0), (-28.0, 25.0), (-28.0, 20.0), (-15.0, 15.0), (0.0, 12.0),
        (10.0, 0.0), (20.0, -5.0), (28.0, -5.0), (32.0, 0.0),
    ),
    # Europe (inland — Channel/Med/Atlantic coasts stay water)
    (
        (62.0, 8.0), (62.0, 30.0), (55.0, 38.0), (50.0, 28.0), (48.0, 12.0),
        (48.0, 4.0), (52.0, 4.0), (62.0, 8.0),
    ),
    # Asia mainland (inland; Malay tip / Singapore excluded)
    (
        (70.0, 60.0), (70.0, 150.0), (55.0, 140.0), (45.0, 125.0), (35.0, 115.0),
        (28.0, 110.0), (22.0, 105.0), (20.0, 100.0), (22.0, 90.0), (25.0, 75.0),
        (28.0, 60.0), (35.0, 55.0), (50.0, 55.0), (70.0, 60.0),
    ),
    # East China inland — Vietnam→Qingdao chord must hit land; Taiwan Strait / HK stay water
    (
        (40.5, 112.0), (39.0, 118.5), (36.5, 118.0), (33.0, 117.5), (29.0, 117.0),
        (26.0, 116.5), (24.0, 115.5), (23.2, 113.5), (24.0, 111.0), (28.0, 111.0),
        (34.0, 111.0), (40.5, 112.0),
    ),
    # Fujian–Zhejiang–Jiangsu inland of the real coast (strait ~24.5N 119.5E stays water)
    (
        (25.8, 118.0), (26.8, 119.6), (28.3, 120.9), (29.6, 121.4), (30.9, 121.3),
        (32.2, 120.6), (33.8, 120.0), (35.0, 119.4),
        (35.0, 117.6), (31.0, 117.8), (27.2, 117.4), (25.8, 117.2), (25.8, 118.0),
    ),
    # India inland
    (
        (26.0, 72.0), (26.0, 85.0), (20.0, 85.0), (15.0, 78.0), (18.0, 74.0),
        (26.0, 72.0),
    ),
    # Arabia inland
    (
        (28.0, 38.0), (28.0, 48.0), (20.0, 50.0), (18.0, 45.0), (22.0, 40.0),
        (28.0, 38.0),
    ),
    # Australia inland
    (
        (-15.0, 130.0), (-18.0, 140.0), (-30.0, 145.0), (-34.0, 140.0), (-30.0, 120.0),
        (-20.0, 120.0), (-15.0, 130.0),
    ),
    # North America inland
    (
        (55.0, -120.0), (55.0, -80.0), (45.0, -70.0), (35.0, -80.0), (30.0, -95.0),
        (35.0, -110.0), (45.0, -120.0), (55.0, -120.0),
    ),
    # South America inland
    (
        (0.0, -70.0), (-5.0, -50.0), (-20.0, -50.0), (-35.0, -65.0), (-20.0, -70.0),
        (-5.0, -75.0), (0.0, -70.0),
    ),
    # Greenland inland
    (
        (75.0, -50.0), (75.0, -30.0), (68.0, -35.0), (68.0, -50.0), (75.0, -50.0),
    ),
    # Antarctica
    (
        (-70.0, -180.0), (-70.0, 180.0), (-90.0, 180.0), (-90.0, -180.0), (-70.0, -180.0),
    ),
)


def _normalize_lon(lon: float) -> float:
    x = (lon + 180.0) % 360.0 - 180.0
    return 180.0 if x == -180.0 and lon > 0 else x


def _point_in_ring(lat: float, lon: float, ring: tuple[tuple[float, float], ...]) -> bool:
    inside = False
    n = len(ring)
    if n < 3:
        return False
    lon = _normalize_lon(lon)
    j = n - 1
    for i in range(n):
        lat_i, lon_i = ring[i][0], _normalize_lon(ring[i][1])
        lat_j, lon_j = ring[j][0], _normalize_lon(ring[j][1])
        if abs(lon_i - lon_j) > 180.0:
            j = i
            continue
        intersects = ((lat_i > lat) != (lat_j > lat)) and (
            lon < (lon_j - lon_i) * (lat - lat_i) / (lat_j - lat_i + 1e-15) + lon_i
        )
        if intersects:
            inside = not inside
        j = i
    return inside


@lru_cache(maxsize=1)
def _rings() -> tuple[tuple[tuple[float, float], ...], ...]:
    return _LAND_RINGS


def is_land(lat: float, lon: float) -> bool:
    """True if (lat, lon) is over a coarse inland landmass."""
    if not math.isfinite(lat) or not math.isfinite(lon):
        return False
    if lat <= -70.0:
        return True
    lon = _normalize_lon(lon)
    return any(_point_in_ring(lat, lon, ring) for ring in _rings())


def is_water(lat: float, lon: float) -> bool:
    return not is_land(lat, lon)


def distance_to_land_nm(lat: float, lon: float) -> float:
    """0 if on land; else min NM to any land-ring edge."""
    return _distance_to_land_cached(round(lat, 3), round(lon, 3))


@lru_cache(maxsize=50000)
def _distance_to_land_cached(lat: float, lon: float) -> float:
    if is_land(lat, lon):
        return 0.0
    best = float("inf")
    lon = _normalize_lon(lon)
    for ring in _rings():
        n = len(ring)
        for i in range(n - 1):
            a, b = ring[i], ring[i + 1]
            if abs(_normalize_lon(a[1]) - _normalize_lon(b[1])) > 180.0:
                continue
            best = min(best, haversine_nm(lat, lon, a[0], a[1]))
            edge_nm = haversine_nm(a[0], a[1], b[0], b[1])
            steps = max(1, int(math.ceil(edge_nm / 90.0)))
            for s in range(1, steps + 1):
                t = s / (steps + 1)
                la = a[0] + (b[0] - a[0]) * t
                lo = _normalize_lon(a[1] + (b[1] - a[1]) * t)
                best = min(best, haversine_nm(lat, lon, la, lo))
            best = min(best, haversine_nm(lat, lon, b[0], b[1]))
    return best if best < float("inf") else 1e9


def is_navigable(lat: float, lon: float, clearance_nm: float = 0.0) -> bool:
    """Hard rule: on water and at least clearance_nm from landmass."""
    if not is_water(lat, lon):
        return False
    if clearance_nm <= 0:
        return True
    return distance_to_land_nm(lat, lon) >= clearance_nm


def _sample_leg(
    a: list[float] | tuple[float, float],
    b: list[float] | tuple[float, float],
    step_nm: float,
) -> list[list[float]]:
    dist = haversine_nm(a[0], a[1], b[0], b[1])
    if dist < 1e-6:
        return [[float(a[0]), float(a[1])]]
    n = max(1, int(math.ceil(dist / max(step_nm, 1.0))))
    return [interpolate([a[0], a[1]], [b[0], b[1]], i / n) for i in range(n + 1)]


def leg_is_navigable(
    a: list[float] | tuple[float, float] | dict[str, float],
    b: list[float] | tuple[float, float] | dict[str, float],
    *,
    clearance_nm: float = 0.0,
    sample_nm: float = 20.0,
) -> bool:
    """True when every sample on the leg is navigable (hard no-land rule)."""
    def _ll(p: Any) -> list[float]:
        if isinstance(p, dict):
            return [float(p["lat"]), float(p["lon"])]
        return [float(p[0]), float(p[1])]

    aa, bb = _ll(a), _ll(b)
    for lat, lon in _sample_leg(aa, bb, sample_nm):
        if not is_navigable(lat, lon, clearance_nm):
            return False
    return True


def score_route_land(
    waypoints: list[Any],
    *,
    sample_nm: float = 25.0,
    clearance_nm: float = 0.0,
) -> dict[str, Any]:
    """Hard-rule score: land or under-clearance samples along the polyline."""
    pts: list[list[float]] = []
    for p in waypoints:
        if isinstance(p, dict):
            pts.append([float(p["lat"]), float(p["lon"])])
        else:
            pts.append([float(p[0]), float(p[1])])

    violations: list[dict] = []
    seen: set[tuple[float, float]] = set()
    if len(pts) == 1:
        legs = [(pts[0], pts[0])]
    else:
        legs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]

    for i, (a, b) in enumerate(legs):
        for lat, lon in _sample_leg(a, b, sample_nm):
            key = (round(lat, 3), round(lon, 3))
            if key in seen:
                continue
            seen.add(key)
            reasons: list[str] = []
            if is_land(lat, lon):
                reasons.append("crosses landmass")
            elif clearance_nm > 0 and distance_to_land_nm(lat, lon) < clearance_nm:
                reasons.append(f"within {clearance_nm} NM of land")
            if reasons:
                violations.append(
                    {
                        "leg": i,
                        "lat": round(lat, 5),
                        "lon": round(lon, 5),
                        "reasons": reasons,
                    }
                )

    return {
        "violation_count": len(violations),
        "violations": violations[:50],
        "sea_clear": len(violations) == 0,
        "sample_nm": sample_nm,
        "clearance_nm": clearance_nm,
    }


def _ring_centroid(ring: tuple[tuple[float, float], ...]) -> tuple[float, float]:
    lats = [p[0] for p in ring[:-1]] or [ring[0][0]]
    lons = [_normalize_lon(p[1]) for p in ring[:-1]] or [_normalize_lon(ring[0][1])]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def _offset_seaward(
    lat: float,
    lon: float,
    clat: float,
    clon: float,
    clearance_nm: float,
) -> tuple[float, float] | None:
    """Push from ring centroid through (lat,lon) out to clearance beyond the edge."""
    # Direction centroid → edge point, then continue past edge into water
    br_lat = lat - clat
    br_lon = _normalize_lon(lon) - _normalize_lon(clon)
    # Also try pure outward search if vector is tiny
    for extra in (clearance_nm, clearance_nm + 30.0, clearance_nm + 60.0, clearance_nm + 120.0):
        # unit-ish step in degrees
        dist_c = haversine_nm(clat, clon, lat, lon) or 1.0
        scale = (dist_c + extra) / dist_c
        nlat = clat + br_lat * scale
        nlon = _normalize_lon(clon + br_lon * scale)
        if is_navigable(nlat, nlon, clearance_nm):
            return round(nlat, 5), round(nlon, 5)
    # Cardinal fallback from edge point
    return nudge_off_land(lat, lon, max_nm=max(120.0, clearance_nm + 80.0), clearance_nm=clearance_nm)


def coast_edge_nodes(
    *,
    clearance_nm: float,
    bbox: tuple[float, float, float, float] | None = None,
    edge_step_nm: float = 120.0,
) -> list[dict[str, float]]:
    """Land-ring edge/vertex nodes offset seaward — waypoints that hug coast clear of land.

    bbox = (min_lat, max_lat, min_lon, max_lon) with padding applied by caller; if set,
    only keep nodes inside it (keeps the graph small).
    """
    out: list[dict[str, float]] = []
    seen: set[tuple[float, float]] = set()
    for ring in _rings():
        if ring[0] == ring[-1] and len(ring) > 1:
            verts = ring[:-1]
        else:
            verts = ring
        if len(verts) < 3:
            continue
        clat, clon = _ring_centroid(tuple(verts) + (verts[0],))
        # vertices + edge samples
        samples: list[tuple[float, float]] = list(verts)
        for i in range(len(verts)):
            a, b = verts[i], verts[(i + 1) % len(verts)]
            edge_nm = haversine_nm(a[0], a[1], b[0], b[1])
            n = max(1, int(math.ceil(edge_nm / max(edge_step_nm, 30.0))))
            for s in range(1, n):
                t = s / n
                samples.append((a[0] + (b[0] - a[0]) * t, _normalize_lon(a[1] + (b[1] - a[1]) * t)))
        for la, lo in samples:
            placed = _offset_seaward(la, lo, clat, clon, clearance_nm)
            if not placed:
                continue
            nlat, nlon = placed
            if bbox is not None:
                min_la, max_la, min_lo, max_lo = bbox
                if not (min_la <= nlat <= max_la and min_lo <= nlon <= max_lo):
                    # handle lon wrap loosely: skip if clearly outside
                    if not (min_la <= nlat <= max_la):
                        continue
                    # lon band may wrap; keep simple non-wrap filter
                    if min_lo <= max_lo and not (min_lo <= nlon <= max_lo):
                        continue
            key = (round(nlat, 2), round(nlon, 2))
            if key in seen:
                continue
            seen.add(key)
            out.append({"lat": nlat, "lon": nlon})
    return out


def nudge_off_land(
    lat: float,
    lon: float,
    *,
    max_nm: float = 120.0,
    clearance_nm: float = 0.0,
) -> tuple[float, float]:
    """Push a point into navigable water (optional clearance) by offset search."""
    if is_navigable(lat, lon, clearance_nm):
        return lat, lon
    for step_nm in (10.0, 20.0, 40.0, 60.0, 90.0, max_nm, max_nm + clearance_nm):
        dlat = step_nm / 60.0
        dlon = step_nm / (60.0 * max(0.2, math.cos(math.radians(lat))))
        for s_lat, s_lon in (
            (dlat, 0), (-dlat, 0), (0, dlon), (0, -dlon),
            (dlat, dlon), (dlat, -dlon), (-dlat, dlon), (-dlat, -dlon),
        ):
            nlat, nlon = lat + s_lat, _normalize_lon(lon + s_lon)
            if is_navigable(nlat, nlon, clearance_nm):
                return nlat, nlon
    return lat, lon
