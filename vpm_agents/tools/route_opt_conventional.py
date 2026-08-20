"""Local conventional route optimize: A*/Dijkstra on a sea-only graph.

Hard rule: land is impassable (points and legs). Storms/weather are costs, not walls
(safest tries storm keep-out first, then soft). Shortest = NM; fastest = time with
weather slowdown; fuel = burn with sea-state; safest = storm/weather over distance.
"""

from __future__ import annotations

import heapq
import math
import time
from functools import lru_cache
from typing import Any

from vpm_agents.config import settings
from vpm_agents.tools.agent_log import progress
from vpm_agents.tools.geo import haversine_nm
from vpm_agents.tools.land_mask import (
    coast_edge_nodes,
    distance_to_land_nm,
    is_navigable,
    leg_is_navigable,
    nudge_off_land,
    score_route_land,
)
from vpm_agents.tools.storm_normalize import normalize_active_storms
from vpm_agents.tools.storm_proximity import point_violates_storm

_OFFSETS_NM = (0.0, 40.0, 80.0, 140.0, 220.0, 300.0, -40.0, -80.0, -140.0, -220.0, -300.0)
_MAX_LINK_NM = 1200.0
_K_NEAREST = 14
_MAX_COAST_NODES = 64
# voyagepm_be route_optimization_common/search_fan.py defaults
_FAN_HOURS = (6.0, 12.0, 18.0, 24.0)
_FAN_SPEED_KTS = 12.0
_FAN_HALF_ARC = 90
_FAN_STEP = 30
_MIN_KTS = 0.1
_DEFAULT_SPEED_KN = 12.0


def _as_pts(waypoints: list) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for p in waypoints:
        if isinstance(p, dict):
            out.append({"lat": float(p["lat"]), "lon": float(p["lon"])})
        else:
            out.append({"lat": float(p[0]), "lon": float(p[1])})
    return out


def _bearing_deg(a: dict[str, float], b: dict[str, float]) -> float:
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlon = math.radians(b["lon"] - a["lon"])
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _offset_point(
    p: dict[str, float],
    bearing_deg: float,
    nm: float,
    clearance_nm: float,
) -> dict[str, float] | None:
    if abs(nm) < 1e-9:
        lat, lon = p["lat"], p["lon"]
    else:
        r = 3440.065
        lat1 = math.radians(p["lat"])
        lon1 = math.radians(p["lon"])
        br = math.radians(bearing_deg)
        d = nm / r
        lat2 = math.asin(
            math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(br)
        )
        lon2 = lon1 + math.atan2(
            math.sin(br) * math.sin(d) * math.cos(lat1),
            math.cos(d) - math.sin(lat1) * math.sin(lat2),
        )
        lon = (math.degrees(lon2) + 540.0) % 360.0 - 180.0
        lat = math.degrees(lat2)
    lat, lon = nudge_off_land(
        lat, lon, max_nm=max(120.0, clearance_nm + 80.0), clearance_nm=clearance_nm
    )
    if not is_navigable(lat, lon, clearance_nm):
        return None
    return {"lat": round(lat, 5), "lon": round(lon, 5)}


@lru_cache(maxsize=20000)
def _leg_ok_cached(
    a_lat: float,
    a_lon: float,
    b_lat: float,
    b_lon: float,
    clearance_nm: float,
) -> bool:
    return leg_is_navigable(
        {"lat": a_lat, "lon": a_lon},
        {"lat": b_lat, "lon": b_lon},
        clearance_nm=clearance_nm,
        sample_nm=12.0,
    )


def _storm_check(lat: float, lon: float, storms: list[dict]) -> tuple[bool, float]:
    """Return (hard_violates, caution_penalty). BE: hard keep-out + safest caution ring."""
    if not storms:
        return False, 0.0
    violates = False
    pen = 0.0
    for s in storms:
        positions = s.get("positions") or [
            {"lat": s["lat"], "lon": s["lon"], "radius_nm": s.get("radius_nm") or 0}
        ]
        for pos in positions:
            check = point_violates_storm(
                lat,
                lon,
                float(pos["lat"]),
                float(pos["lon"]),
                float(
                    pos.get("radius_nm")
                    if pos.get("radius_nm") is not None
                    else s.get("radius_nm") or 0
                ),
                center_buffer_nm=settings.storm_center_buffer_nm,
                edge_buffer_nm=settings.storm_edge_buffer_nm,
            )
            if check["violates"]:
                violates = True
                pen += 800.0 + max(
                    0.0, settings.storm_center_buffer_nm - check["distance_to_center_nm"]
                )
            else:
                d = check["distance_to_center_nm"]
                soft = settings.storm_center_buffer_nm * 1.5
                if d < soft:
                    pen += (soft - d) * 0.15
    return violates, pen


def _storm_penalty(lat: float, lon: float, storms: list[dict]) -> float:
    return _storm_check(lat, lon, storms)[1]


def _leg_storm_blocked(a: dict[str, float], b: dict[str, float], storms: list[dict]) -> bool:
    if not storms:
        return False
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        lat = a["lat"] * (1.0 - t) + b["lat"] * t
        lon = a["lon"] * (1.0 - t) + b["lon"] * t
        if _storm_check(lat, lon, storms)[0]:
            return True
    return False


def _weather_factor(weather: dict | None) -> float:
    if not weather or not isinstance(weather, dict):
        return 0.0
    wind = weather.get("maxWindKn") or weather.get("max_wind_kn") or weather.get("windKn")
    wave = weather.get("maxWaveM") or weather.get("max_wave_m") or weather.get("waveM")
    score = 0.0
    if wind is not None:
        score += max(0.0, float(wind) - 20.0) / 40.0
    if wave is not None:
        score += max(0.0, float(wave) - 2.0) / 4.0
    return min(1.0, score)


def _edge_cost(
    a: dict[str, float],
    b: dict[str, float],
    objective: str,
    storms: list[dict],
    wx_f: float,
    *,
    storm_soft: bool,
    speed_kn: float,
    fuel_mt_day: float | None,
) -> float:
    dist = haversine_nm(a["lat"], a["lon"], b["lat"], b["lon"])
    mid_lat = (a["lat"] + b["lat"]) / 2.0
    mid_lon = (a["lon"] + b["lon"]) / 2.0
    storm = (
        _storm_penalty(a["lat"], a["lon"], storms)
        + _storm_penalty(b["lat"], b["lon"], storms)
        + _storm_penalty(mid_lat, mid_lon, storms)
    ) / 3.0
    sog = max(_MIN_KTS, float(speed_kn) or _DEFAULT_SPEED_KN)
    hours = dist / sog
    obj = (objective or "shortest").lower()
    if obj == "shortest":
        return dist
    if obj == "fastest":
        # weather slows the ship — extra time beats extra NM
        return hours * (1.0 + wx_f * 1.5)
    if obj in ("fuel", "lowest-fuel"):
        rate = float(fuel_mt_day) if fuel_mt_day is not None else 1.0
        return hours * rate * (1.0 + wx_f * 2.0)
    # safest: distance is cheap vs storm/weather exposure
    return dist * 0.35 + (storm * 3.0 if storm_soft else 0.0) + wx_f * dist * 2.0


def _route_length_nm(master: list[dict[str, float]]) -> float:
    return sum(
        haversine_nm(master[i]["lat"], master[i]["lon"], master[i + 1]["lat"], master[i + 1]["lon"])
        for i in range(len(master) - 1)
    )


def _dist_to_route_nm(p: dict[str, float], master: list[dict[str, float]]) -> float:
    best = float("inf")
    for q in master:
        best = min(best, haversine_nm(p["lat"], p["lon"], q["lat"], q["lon"]))
    for i in range(len(master) - 1):
        a, b = master[i], master[i + 1]
        mid = {"lat": (a["lat"] + b["lat"]) / 2, "lon": (a["lon"] + b["lon"]) / 2}
        best = min(best, haversine_nm(p["lat"], p["lon"], mid["lat"], mid["lon"]))
    return best


def _densify_master(master: list[dict[str, float]], step_nm: float = 90.0) -> list[dict[str, float]]:
    if len(master) < 2:
        return list(master)
    out = [master[0]]
    for i in range(len(master) - 1):
        a, b = master[i], master[i + 1]
        dist = haversine_nm(a["lat"], a["lon"], b["lat"], b["lon"])
        n = max(1, int(math.ceil(dist / max(step_nm, 20.0))))
        for k in range(1, n):
            t = k / n
            out.append(
                {
                    "lat": a["lat"] + (b["lat"] - a["lat"]) * t,
                    "lon": a["lon"] + (b["lon"] - a["lon"]) * t,
                }
            )
        out.append(b)
    return out


def _voyage_bbox(
    master: list[dict[str, float]], pad_deg: float | None = None
) -> tuple[float, float, float, float]:
    lats = [p["lat"] for p in master]
    lons = [p["lon"] for p in master]
    if pad_deg is None:
        # If the master already cuts land, open a wide corridor so coast nodes
        # can route *around* the continent (e.g. Cape of Good Hope).
        pad_deg = 35.0 if not score_route_land(master, sample_nm=30.0)["sea_clear"] else 10.0
    return (min(lats) - pad_deg, max(lats) + pad_deg, min(lons) - pad_deg, max(lons) + pad_deg)


def _build_nodes(
    master: list[dict[str, float]], clearance_nm: float
) -> tuple[list[dict[str, float]], int, int]:
    origin = {"lat": master[0]["lat"], "lon": master[0]["lon"]}
    dest = {"lat": master[-1]["lat"], "lon": master[-1]["lon"]}
    nodes: list[dict[str, float]] = [origin, dest]
    seen = {
        (round(origin["lat"], 3), round(origin["lon"], 3)),
        (round(dest["lat"], 3), round(dest["lon"], 3)),
    }

    def _add(p: dict[str, float] | None, *, require_clearance: bool = True) -> None:
        if not p:
            return
        key = (round(p["lat"], 3), round(p["lon"], 3))
        if key in seen:
            return
        clr = clearance_nm if require_clearance else 0.0
        if require_clearance and not is_navigable(p["lat"], p["lon"], clr):
            return
        seen.add(key)
        nodes.append({"lat": p["lat"], "lon": p["lon"]})

    # Corridor fans at densified master points (2-point land chords get sea lanes)
    spine = _densify_master(master, step_nm=90.0)
    for i in range(1, len(spine) - 1):
        prev, cur, nxt = spine[i - 1], spine[i], spine[min(i + 1, len(spine) - 1)]
        br = _bearing_deg(prev, nxt)
        perp = (br + 90.0) % 360.0
        for nm in _OFFSETS_NM:
            _add(_offset_point(cur, perp, nm, clearance_nm))

    # BE search_fan: ±90° of dest heading at 6/12/18/24h * 12 kts
    anchors = master[:-1]
    if len(anchors) > 12:
        step = max(1, len(anchors) // 12)
        anchors = anchors[::step]
    for cur in anchors:
        heading = _bearing_deg(cur, dest)
        for rel in range(-_FAN_HALF_ARC, _FAN_HALF_ARC + 1, _FAN_STEP):
            br = (heading + rel) % 360.0
            for hours in _FAN_HOURS:
                _add(_offset_point(cur, br, hours * _FAN_SPEED_KTS, clearance_nm))

    # Coast-edge nodes near this voyage (wide bbox when master crosses land)
    bbox = _voyage_bbox(master)
    coast = coast_edge_nodes(clearance_nm=clearance_nm, bbox=bbox, edge_step_nm=150.0)
    coast.sort(key=lambda p: _dist_to_route_nm(p, master))
    # When avoiding land, keep more coast nodes even if farther from the chord
    land_block = not score_route_land(master, sample_nm=30.0)["sea_clear"]
    route_nm = max(800.0, _route_length_nm(master) * (1.2 if land_block else 0.6))
    coast_cap = _MAX_COAST_NODES * 2 if land_block else _MAX_COAST_NODES
    added_coast = 0
    for p in coast:
        if _dist_to_route_nm(p, master) > route_nm and not land_block:
            continue
        before = len(nodes)
        _add(p)
        if len(nodes) > before:
            added_coast += 1
        if added_coast >= coast_cap:
            break

    return nodes, 0, 1


def _neighbors(
    nodes: list[dict[str, float]],
    clearance_nm: float,
    max_link_nm: float = _MAX_LINK_NM,
    k: int = _K_NEAREST,
) -> list[list[int]]:
    """Each node links to up to k nearest navigable neighbors (undirected)."""
    n = len(nodes)
    adj: list[list[int]] = [[] for _ in range(n)]
    for i in range(n):
        cands: list[tuple[float, int]] = []
        for j in range(n):
            if i == j:
                continue
            d = haversine_nm(nodes[i]["lat"], nodes[i]["lon"], nodes[j]["lat"], nodes[j]["lon"])
            if 1e-3 < d <= max_link_nm:
                cands.append((d, j))
        cands.sort()
        linked = 0
        for _d, j in cands:
            if linked >= k:
                break
            if _leg_ok_cached(
                round(nodes[i]["lat"], 4),
                round(nodes[i]["lon"], 4),
                round(nodes[j]["lat"], 4),
                round(nodes[j]["lon"], 4),
                clearance_nm,
            ):
                if j not in adj[i]:
                    adj[i].append(j)
                if i not in adj[j]:
                    adj[j].append(i)
                linked += 1
    return adj


def _ensure_terminal_links(
    nodes: list[dict[str, float]],
    adj: list[list[int]],
    start_i: int,
    goal_i: int,
    clearance_nm: float,
) -> None:
    """Ports may sit inside the clearance band — link terminals to nearest sea nodes."""
    n = len(nodes)
    for terminal in (start_i, goal_i):
        cands: list[tuple[float, int]] = []
        for j in range(n):
            if j == terminal:
                continue
            d = haversine_nm(
                nodes[terminal]["lat"], nodes[terminal]["lon"], nodes[j]["lat"], nodes[j]["lon"]
            )
            if 1e-3 < d <= _MAX_LINK_NM * 1.5:
                cands.append((d, j))
        cands.sort()
        for _d, j in cands[:30]:
            if j in (start_i, goal_i) and j != terminal:
                if not score_route_land(
                    [nodes[terminal], nodes[j]], sample_nm=10.0, clearance_nm=0.0
                )["sea_clear"]:
                    continue
            if not _terminal_leg_ok(nodes[terminal], nodes[j], max(0.0, clearance_nm * 0.5)):
                continue
            if j not in adj[terminal]:
                adj[terminal].append(j)
            if terminal not in adj[j]:
                adj[j].append(terminal)
            if len(adj[terminal]) >= _K_NEAREST:
                break


def _terminal_leg_ok(
    terminal: dict[str, float],
    other: dict[str, float],
    clearance_nm: float,
) -> bool:
    """Leg OK if every sample except the terminal itself is off land (and clear)."""
    from vpm_agents.tools.land_mask import _sample_leg, is_land

    samples = _sample_leg(
        [terminal["lat"], terminal["lon"]],
        [other["lat"], other["lon"]],
        12.0,
    )
    for i, (lat, lon) in enumerate(samples):
        if i == 0:
            continue
        if is_land(lat, lon):
            return False
        if clearance_nm > 0 and not is_navigable(lat, lon, clearance_nm):
            return False
    return True


def _sea_safe_fallback(
    master: list[dict[str, float]], clearance_nm: float
) -> list[dict[str, float]]:
    """Greedy via coast nodes when A* fails — never prefer a land-cutting chord."""
    if score_route_land(master, sample_nm=20.0)["sea_clear"]:
        path = [master[0]]
        for p in master[1:-1]:
            lat, lon = nudge_off_land(p["lat"], p["lon"], max_nm=250.0, clearance_nm=clearance_nm)
            if is_navigable(lat, lon, clearance_nm):
                path.append({"lat": round(lat, 5), "lon": round(lon, 5)})
        path.append(master[-1])
        if score_route_land(path, sample_nm=15.0)["sea_clear"]:
            return path

    bbox = _voyage_bbox(master, pad_deg=40.0)
    soft = max(5.0, clearance_nm * 0.5)
    coast = coast_edge_nodes(clearance_nm=soft, bbox=bbox, edge_step_nm=100.0)
    nodes = [master[0], master[-1]] + coast
    path = [master[0]]
    used = {0}
    cur = 0
    dest_i = 1
    guard = 0
    while cur != dest_i and guard < len(nodes) + 8:
        guard += 1
        best_j = None
        best_key = None
        for j, p in enumerate(nodes):
            if j in used:
                continue
            d_leg = haversine_nm(nodes[cur]["lat"], nodes[cur]["lon"], p["lat"], p["lon"])
            if d_leg < 1e-3 or d_leg > _MAX_LINK_NM * 1.5:
                continue
            clr = 0.0 if cur == 0 else soft
            if not _leg_ok_cached(
                round(nodes[cur]["lat"], 4),
                round(nodes[cur]["lon"], 4),
                round(p["lat"], 4),
                round(p["lon"], 4),
                clr,
            ):
                continue
            d_goal = haversine_nm(p["lat"], p["lon"], master[-1]["lat"], master[-1]["lon"])
            key = (d_goal + d_leg * 0.15, d_leg)
            if best_key is None or key < best_key:
                best_key = key
                best_j = j
        if best_j is None:
            break
        used.add(best_j)
        path.append(nodes[best_j])
        cur = best_j
    if path[-1]["lat"] != master[-1]["lat"] or path[-1]["lon"] != master[-1]["lon"]:
        path.append(master[-1])
    path[0] = master[0]
    path[-1] = master[-1]
    return path


def _search(
    nodes: list[dict[str, float]],
    adj: list[list[int]],
    start_i: int,
    goal_i: int,
    objective: str,
    storms: list[dict],
    wx_f: float,
    *,
    use_astar: bool,
    hard_storm: bool = False,
    speed_kn: float = _DEFAULT_SPEED_KN,
    fuel_mt_day: float | None = None,
) -> list[dict[str, float]] | None:
    goal = nodes[goal_i]

    def h(i: int) -> float:
        if not use_astar:
            return 0.0
        p = nodes[i]
        return haversine_nm(p["lat"], p["lon"], goal["lat"], goal["lon"])

    pq: list[tuple[float, float, int]] = [(h(start_i), 0.0, start_i)]
    best_g: dict[int, float] = {start_i: 0.0}
    parent: dict[int, int | None] = {start_i: None}
    t0 = time.monotonic()
    last_hb = t0
    expanded = 0

    while pq:
        _f, g, i = heapq.heappop(pq)
        expanded += 1
        now = time.monotonic()
        if now - last_hb >= 15.0:
            progress(
                "RouteOptimize",
                f"search in progress objective={objective} expanded={expanded} "
                f"frontier={len(pq)} visited={len(best_g)}",
                phase="search",
                elapsed_s=now - t0,
            )
            last_hb = now
        if g > best_g.get(i, float("inf")) + 1e-9:
            continue
        if i == goal_i:
            path_i: list[int] = []
            cur: int | None = i
            while cur is not None:
                path_i.append(cur)
                cur = parent[cur]
            path_i.reverse()
            return [nodes[k] for k in path_i]
        for j in adj[i]:
            if hard_storm and _leg_storm_blocked(nodes[i], nodes[j], storms):
                continue
            ng = g + _edge_cost(
                nodes[i], nodes[j], objective, storms, wx_f,
                storm_soft=not hard_storm, speed_kn=speed_kn, fuel_mt_day=fuel_mt_day,
            )
            if ng + 1e-9 < best_g.get(j, float("inf")):
                best_g[j] = ng
                parent[j] = i
                heapq.heappush(pq, (ng + h(j), ng, j))
    return None


def _metrics(
    path: list[dict[str, float]],
    speed_kn: float,
    fuel_mt_day: float | None,
) -> dict[str, Any]:
    dist = 0.0
    for i in range(len(path) - 1):
        dist += haversine_nm(
            path[i]["lat"], path[i]["lon"], path[i + 1]["lat"], path[i + 1]["lon"]
        )
    sog = max(_MIN_KTS, float(speed_kn) or _DEFAULT_SPEED_KN)
    hours = dist / sog if sog else 0.0
    fuel = round(hours / 24.0 * float(fuel_mt_day), 1) if fuel_mt_day is not None else None
    return {
        "distanceNm": round(dist, 1),
        "fuelMt": fuel,
        "etaHours": round(hours, 1),
        "days": round(hours / 24.0, 2),
    }


def _simplify(path: list[dict[str, float]], clearance_nm: float) -> list[dict[str, float]]:
    if len(path) <= 2:
        return path
    out = [path[0]]
    i = 0
    while i < len(path) - 1:
        best = i + 1
        for j in range(len(path) - 1, i + 1, -1):
            d = haversine_nm(path[i]["lat"], path[i]["lon"], path[j]["lat"], path[j]["lon"])
            if d > 380.0:
                continue
            if _leg_ok_cached(
                round(path[i]["lat"], 4),
                round(path[i]["lon"], 4),
                round(path[j]["lat"], 4),
                round(path[j]["lon"], 4),
                max(0.0, clearance_nm),
            ):
                best = j
                break
        out.append(path[best])
        i = best
    return out


def _seaward_midpoint(
    a: dict[str, float],
    b: dict[str, float],
    clearance_nm: float,
    offset_nm: float,
) -> dict[str, float] | None:
    mid = {"lat": (a["lat"] + b["lat"]) / 2.0, "lon": (a["lon"] + b["lon"]) / 2.0}
    br = _bearing_deg(a, b)
    best = None
    best_d = -1.0
    for rel in (90.0, 270.0):
        p = _offset_point(mid, (br + rel) % 360.0, offset_nm, max(5.0, clearance_nm * 0.4))
        if not p:
            continue
        d = distance_to_land_nm(p["lat"], p["lon"])
        if d > best_d:
            best_d = d
            best = p
    return best


def _repair_land_legs(
    path: list[dict[str, float]],
    clearance_nm: float,
    *,
    depth: int = 0,
) -> list[dict[str, float]]:
    """Insert seaward waypoints on any leg the land mask still flags (used during build)."""
    if depth > 8 or len(path) < 2:
        return path
    out = [path[0]]
    grew = False
    for b in path[1:]:
        a = out[-1]
        if score_route_land([a, b], sample_nm=8.0, clearance_nm=0.0)["sea_clear"]:
            out.append(b)
            continue
        grew = True
        placed = False
        for nm in (40.0, 80.0, 120.0, 180.0, 260.0):
            p = _seaward_midpoint(a, b, clearance_nm, nm)
            if not p:
                continue
            ok_a = score_route_land([a, p], sample_nm=8.0, clearance_nm=0.0)["sea_clear"]
            ok_b = score_route_land([p, b], sample_nm=8.0, clearance_nm=0.0)["sea_clear"]
            if ok_a and ok_b:
                out.append(p)
                out.append(b)
                placed = True
                break
        if not placed:
            out.append(b)
    if grew:
        return _repair_land_legs(out, clearance_nm, depth=depth + 1)
    return out


def ensure_sea_route(
    waypoints: list,
    *,
    origin: dict[str, float] | list | None = None,
    dest: dict[str, float] | list | None = None,
    clearance_nm: float | None = None,
) -> list[dict[str, float]]:
    """Force a sea-only polyline. Endpoints stay pinned. Land crossing is repaired, not skipped."""
    path = _as_pts(waypoints)
    if len(path) < 2:
        return path

    def _ll(p: dict[str, float] | list) -> dict[str, float]:
        if isinstance(p, dict):
            return {"lat": float(p["lat"]), "lon": float(p["lon"])}
        return {"lat": float(p[0]), "lon": float(p[1])}

    start = _ll(origin) if origin is not None else path[0]
    end = _ll(dest) if dest is not None else path[-1]
    clr = float(clearance_nm if clearance_nm is not None else settings.land_clearance_nm)
    path[0], path[-1] = start, end
    path = _repair_land_legs(path, clr)
    if not score_route_land(path, sample_nm=8.0, clearance_nm=0.0)["sea_clear"]:
        path = _sea_safe_fallback(path, clr)
        path = _repair_land_legs(path, clr)
    path[0], path[-1] = start, end
    return [{"lat": p["lat"], "lon": p["lon"], "seq": i} for i, p in enumerate(path)]


def optimize_conventional(
    objective: str,
    waypoints: list,
    weather: dict | None = None,
    storms: list | None = None,
    *,
    algo: str | None = None,
    speed_kn: float | None = None,
    fuel_mt_day: float | None = None,
) -> dict[str, Any]:
    """Return VO-shaped optimize result using Dijkstra or A* with land hard rules."""
    master = _as_pts(waypoints)
    if len(master) < 2:
        raise ValueError("need >=2 waypoints")
    clearance = float(settings.land_clearance_nm)
    storms_n = normalize_active_storms(storms or [])
    wx_f = _weather_factor(weather)
    algo_s = (algo or settings.route_opt_algo or "astar").strip().lower()
    use_astar = algo_s != "dijkstra"
    sog = float(speed_kn) if speed_kn else _DEFAULT_SPEED_KN
    t0 = time.monotonic()
    progress(
        "RouteOptimize",
        f"conventional {objective} algo={algo_s} wps={len(master)} speed={sog}kn",
        phase="search",
    )

    nodes, start_i, goal_i = _build_nodes(master, clearance)
    adj = _neighbors(nodes, clearance)
    _ensure_terminal_links(nodes, adj, start_i, goal_i, clearance)
    obj = (objective or "shortest").lower()
    # Land is the only hard wall. Safest may try storm keep-out first.
    prefer_storm_out = obj == "safest" and bool(storms_n)

    def _kw(hard_storm: bool) -> dict:
        return {
            "use_astar": use_astar,
            "hard_storm": hard_storm,
            "speed_kn": sog,
            "fuel_mt_day": fuel_mt_day,
        }

    def _run_search() -> list[dict[str, float]] | None:
        if prefer_storm_out:
            found = _search(
                nodes, adj, start_i, goal_i, objective, storms_n, wx_f, **_kw(True),
            )
            if found is not None:
                return found
        return _search(
            nodes, adj, start_i, goal_i, objective, storms_n, wx_f, **_kw(False),
        )

    path = _run_search()

    if path is None:
        # Soften clearance and widen coast coverage
        soft = max(0.0, clearance * 0.4)
        bbox = _voyage_bbox(master, pad_deg=40.0)
        extra = coast_edge_nodes(clearance_nm=max(5.0, soft), bbox=bbox, edge_step_nm=100.0)
        extra.sort(key=lambda p: _dist_to_route_nm(p, master))
        seen = {(round(p["lat"], 3), round(p["lon"], 3)) for p in nodes}
        for p in extra[: _MAX_COAST_NODES * 2]:
            key = (round(p["lat"], 3), round(p["lon"], 3))
            if key in seen:
                continue
            if is_navigable(p["lat"], p["lon"], soft):
                seen.add(key)
                nodes.append(p)
        adj = _neighbors(nodes, soft, max_link_nm=_MAX_LINK_NM * 1.2, k=16)
        _ensure_terminal_links(nodes, adj, start_i, goal_i, soft)
        path = _run_search()
        clearance_check = soft
    else:
        clearance_check = clearance

    if path is None:
        path = _sea_safe_fallback(master, clearance)
        clearance_check = 0.0

    path = _simplify(path, clearance_check)
    path = _repair_land_legs(path, clearance)
    if len(path) < 6:
        dense = _densify_master(path, step_nm=90.0)
        keep = [dense[0]]
        for p in dense[1:-1]:
            if is_navigable(p["lat"], p["lon"], 0.0):
                keep.append(p)
        keep.append(dense[-1])
        if score_route_land(keep, sample_nm=8.0, clearance_nm=0.0)["sea_clear"]:
            path = keep
    path = _repair_land_legs(path, clearance)
    path[0] = {"lat": master[0]["lat"], "lon": master[0]["lon"]}
    path[-1] = {"lat": master[-1]["lat"], "lon": master[-1]["lon"]}

    # Never keep a 2-point land chord (origin–dest shortcut)
    if not score_route_land(path, sample_nm=12.0, clearance_nm=0.0)["sea_clear"]:
        repair = _search(
            nodes, adj, start_i, goal_i, objective, storms_n, wx_f, **_kw(prefer_storm_out),
        ) or _search(
            nodes, adj, start_i, goal_i, objective, storms_n, wx_f, **_kw(False),
        )
        if repair and score_route_land(repair, sample_nm=12.0, clearance_nm=0.0)["sea_clear"]:
            path = _simplify(repair, 0.0)
        else:
            path = _sea_safe_fallback(master, clearance)
        path[0] = {"lat": master[0]["lat"], "lon": master[0]["lon"]}
        path[-1] = {"lat": master[-1]["lat"], "lon": master[-1]["lon"]}

    path = _repair_land_legs(path, clearance)
    path[0] = {"lat": master[0]["lat"], "lon": master[0]["lon"]}
    path[-1] = {"lat": master[-1]["lat"], "lon": master[-1]["lon"]}

    wps = [{"lat": p["lat"], "lon": p["lon"], "seq": i} for i, p in enumerate(path)]
    m = _metrics(path, sog, fuel_mt_day)
    sea = score_route_land(wps, sample_nm=8.0, clearance_nm=0.0)["sea_clear"]
    progress(
        "RouteOptimize",
        f"conventional {objective} done dist={m['distanceNm']}NM wps={len(wps)} sea_clear={sea}",
        phase="search",
        elapsed_s=time.monotonic() - t0,
    )
    return {
        "objective": objective,
        "waypoints": wps,
        "distanceNm": m["distanceNm"],
        "fuelMt": m["fuelMt"],
        "etaHours": m["etaHours"],
        "days": m["days"],
        "weatherAware": bool(weather),
        "stormAware": bool(storms_n),
        "provider": f"local-{algo_s}-{objective}",
        "land_clearance_nm": clearance,
        "sea_clear": sea,
        "speedKn": sog,
        "fuelMtDay": fuel_mt_day,
    }
