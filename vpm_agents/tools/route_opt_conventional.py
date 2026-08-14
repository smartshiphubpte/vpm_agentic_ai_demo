"""Local route optimize via Dijkstra / A* (no voyagepm_be).

Hard rules:
  - Never place intermediate nodes on land or within VPM_LAND_CLEARANCE_NM of land.
  - Never connect two nodes with a leg that crosses land / under-clearance water.
  - Land-ring edges become seaward coast nodes so paths go *around* continents.
  - Origin/destination lat/lon stay fixed (endpoints pinned after search).
"""

from __future__ import annotations

import heapq
import math
from functools import lru_cache
from typing import Any

from vpm_agents.config import settings
from vpm_agents.tools.geo import haversine_nm
from vpm_agents.tools.land_mask import (
    coast_edge_nodes,
    is_navigable,
    leg_is_navigable,
    nudge_off_land,
    score_route_land,
)
from vpm_agents.tools.storm_normalize import normalize_active_storms
from vpm_agents.tools.storm_proximity import point_violates_storm

_OFFSETS_NM = (0.0, 50.0, 100.0, 160.0, -50.0, -100.0, -160.0)
_MAX_LINK_NM = 1200.0
_K_NEAREST = 14
_MAX_COAST_NODES = 64


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
        sample_nm=25.0,
    )


def _storm_penalty(lat: float, lon: float, storms: list[dict]) -> float:
    if not storms:
        return 0.0
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
                pen += 800.0 + max(
                    0.0, settings.storm_center_buffer_nm - check["distance_to_center_nm"]
                )
            else:
                d = check["distance_to_center_nm"]
                soft = settings.storm_center_buffer_nm * 1.5
                if d < soft:
                    pen += (soft - d) * 0.15
    return pen


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
) -> float:
    dist = haversine_nm(a["lat"], a["lon"], b["lat"], b["lon"])
    mid_lat = (a["lat"] + b["lat"]) / 2.0
    mid_lon = (a["lon"] + b["lon"]) / 2.0
    storm = (
        _storm_penalty(a["lat"], a["lon"], storms)
        + _storm_penalty(b["lat"], b["lon"], storms)
        + _storm_penalty(mid_lat, mid_lon, storms)
    ) / 3.0
    obj = (objective or "shortest").lower()
    if obj in ("shortest",):
        return dist + storm * 0.05
    if obj in ("fastest",):
        return dist * (1.0 + 0.08 * wx_f) + storm * 0.1
    if obj in ("fuel", "lowest-fuel"):
        return dist * (1.0 + 0.25 * wx_f + min(1.0, storm / 500.0) * 0.35) + storm * 0.2
    return dist + storm * 4.0 + wx_f * dist * 0.2


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

    # Corridor fans at intermediates
    for i in range(1, len(master) - 1):
        prev, cur, nxt = master[i - 1], master[i], master[min(i + 1, len(master) - 1)]
        br = _bearing_deg(prev, nxt)
        perp = (br + 90.0) % 360.0
        for nm in _OFFSETS_NM:
            _add(_offset_point(cur, perp, nm, clearance_nm))

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
            if not _terminal_leg_ok(nodes[terminal], nodes[j], max(0.0, clearance_nm * 0.5)):
                if not _leg_ok_cached(
                    round(nodes[terminal]["lat"], 4),
                    round(nodes[terminal]["lon"], 4),
                    round(nodes[j]["lat"], 4),
                    round(nodes[j]["lon"], 4),
                    0.0,
                ):
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
        25.0,
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

    while pq:
        _f, g, i = heapq.heappop(pq)
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
            ng = g + _edge_cost(nodes[i], nodes[j], objective, storms, wx_f)
            if ng + 1e-9 < best_g.get(j, float("inf")):
                best_g[j] = ng
                parent[j] = i
                heapq.heappush(pq, (ng + h(j), ng, j))
    return None


def _metrics(path: list[dict[str, float]], objective: str) -> dict[str, float]:
    dist = 0.0
    for i in range(len(path) - 1):
        dist += haversine_nm(
            path[i]["lat"], path[i]["lon"], path[i + 1]["lat"], path[i + 1]["lon"]
        )
    obj = (objective or "").lower()
    fuel_rate = 0.18 if obj in ("fuel", "lowest-fuel") else 0.22
    speed = 14.5 if obj == "fastest" else 12.0
    return {
        "distanceNm": round(dist, 1),
        "fuelMt": round(dist * fuel_rate, 1),
        "etaHours": round(dist / speed, 1) if speed else 0.0,
    }


def _simplify(path: list[dict[str, float]], clearance_nm: float) -> list[dict[str, float]]:
    if len(path) <= 2:
        return path
    out = [path[0]]
    i = 0
    while i < len(path) - 1:
        best = i + 1
        for j in range(len(path) - 1, i + 1, -1):
            if _leg_ok_cached(
                round(path[i]["lat"], 4),
                round(path[i]["lon"], 4),
                round(path[j]["lat"], 4),
                round(path[j]["lon"], 4),
                clearance_nm if i > 0 else 0.0,
            ):
                best = j
                break
        out.append(path[best])
        i = best
    return out


def optimize_conventional(
    objective: str,
    waypoints: list,
    weather: dict | None = None,
    storms: list | None = None,
    *,
    algo: str | None = None,
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

    nodes, start_i, goal_i = _build_nodes(master, clearance)
    adj = _neighbors(nodes, clearance)
    _ensure_terminal_links(nodes, adj, start_i, goal_i, clearance)

    path = _search(nodes, adj, start_i, goal_i, objective, storms_n, wx_f, use_astar=use_astar)

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
        path = _search(nodes, adj, start_i, goal_i, objective, storms_n, wx_f, use_astar=use_astar)
        clearance_check = soft
    else:
        clearance_check = clearance

    if path is None:
        path = _sea_safe_fallback(master, clearance)
        clearance_check = 0.0

    path = _simplify(path, clearance_check)
    path[0] = {"lat": master[0]["lat"], "lon": master[0]["lon"]}
    path[-1] = {"lat": master[-1]["lat"], "lon": master[-1]["lon"]}

    # Final hard gate: replace land-crossing result with coast fallback
    if not score_route_land(path, sample_nm=15.0, clearance_nm=0.0)["sea_clear"]:
        repair = _search(nodes, adj, start_i, goal_i, "safest", storms_n, wx_f, use_astar=True)
        if repair and score_route_land(repair, sample_nm=15.0, clearance_nm=0.0)["sea_clear"]:
            path = _simplify(repair, 0.0)
        else:
            path = _sea_safe_fallback(master, clearance)
        path[0] = {"lat": master[0]["lat"], "lon": master[0]["lon"]}
        path[-1] = {"lat": master[-1]["lat"], "lon": master[-1]["lon"]}

    wps = [{"lat": p["lat"], "lon": p["lon"], "seq": i} for i, p in enumerate(path)]
    m = _metrics(path, objective)
    sea = score_route_land(wps, sample_nm=15.0, clearance_nm=0.0)["sea_clear"]
    return {
        "objective": objective,
        "waypoints": wps,
        "distanceNm": m["distanceNm"],
        "fuelMt": m["fuelMt"],
        "etaHours": m["etaHours"],
        "weatherAware": bool(weather),
        "stormAware": bool(storms_n),
        "provider": f"local-{algo_s}-{objective}",
        "land_clearance_nm": clearance,
        "sea_clear": sea,
    }
