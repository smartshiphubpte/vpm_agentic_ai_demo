"""Storm distance to active voyage routes — center + edge buffers, full track."""

from __future__ import annotations

from typing import Any

from vpm_agents.config import settings
from vpm_agents.tools.geo import haversine_nm
from vpm_agents.tools.route_json import parse_route_points
from vpm_agents.tools.storm_normalize import normalize_active_storms


def point_violates_storm(
    lat: float,
    lon: float,
    storm_lat: float,
    storm_lon: float,
    radius_nm: float,
    *,
    center_buffer_nm: float | None = None,
    edge_buffer_nm: float | None = None,
) -> dict[str, Any]:
    """True if point is within center buffer of storm center OR within edge buffer of storm edge."""
    center_buffer_nm = (
        center_buffer_nm if center_buffer_nm is not None else settings.storm_center_buffer_nm
    )
    edge_buffer_nm = edge_buffer_nm if edge_buffer_nm is not None else settings.storm_edge_buffer_nm
    radius_nm = float(radius_nm or 0)
    dist_center = haversine_nm(lat, lon, storm_lat, storm_lon)
    dist_edge = dist_center - radius_nm
    within_center = dist_center <= center_buffer_nm
    within_edge = dist_edge <= edge_buffer_nm
    return {
        "distance_to_center_nm": round(dist_center, 1),
        "distance_to_edge_nm": round(dist_edge, 1),
        "radius_nm": radius_nm,
        "center_buffer_nm": center_buffer_nm,
        "edge_buffer_nm": edge_buffer_nm,
        "within_center_buffer": within_center,
        "within_edge_buffer": within_edge,
        "violates": within_center or within_edge,
    }


def score_route_storms(
    route: list[list[float]] | list[dict],
    storms: list[dict[str, Any]],
    *,
    center_buffer_nm: float | None = None,
    edge_buffer_nm: float | None = None,
) -> dict[str, Any]:
    """Score route vs all storm centers/progressions; list clearance violations."""
    pts = _as_latlon_list(route)
    storms = normalize_active_storms(storms)
    violations: list[dict[str, Any]] = []
    closest_nm = float("inf")

    for i, (rlat, rlon) in enumerate(pts):
        for s in storms:
            positions = s.get("positions") or [
                {"lat": s["lat"], "lon": s["lon"], "radius_nm": s.get("radius_nm") or 0}
            ]
            for pos in positions:
                check = point_violates_storm(
                    rlat,
                    rlon,
                    float(pos["lat"]),
                    float(pos["lon"]),
                    float(pos.get("radius_nm") if pos.get("radius_nm") is not None else s.get("radius_nm") or 0),
                    center_buffer_nm=center_buffer_nm,
                    edge_buffer_nm=edge_buffer_nm,
                )
                closest_nm = min(closest_nm, check["distance_to_center_nm"])
                if check["violates"]:
                    violations.append(
                        {
                            "route_seq": i,
                            "route_lat": rlat,
                            "route_lon": rlon,
                            "storm_id": s.get("id"),
                            "storm_name": s.get("name"),
                            "storm_lat": pos["lat"],
                            "storm_lon": pos["lon"],
                            "track_phase": pos.get("track_phase"),
                            "valid_time": pos.get("valid_time"),
                            **check,
                        }
                    )

    n = max(len(pts), 1)
    # Dedup by (seq, storm) keeping first
    seen: set[tuple] = set()
    uniq: list[dict] = []
    for v in violations:
        key = (v["route_seq"], v.get("storm_id"), v.get("storm_lat"), v.get("storm_lon"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(v)

    return {
        "violation_count": len(uniq),
        "violations": uniq,
        "storm_clear": len(uniq) == 0,
        "closest_storm_nm": round(closest_nm, 1) if closest_nm < float("inf") else None,
        "storm_score": round(100 * (1 - min(len(uniq), n) / n), 1),
        "center_buffer_nm": center_buffer_nm
        if center_buffer_nm is not None
        else settings.storm_center_buffer_nm,
        "edge_buffer_nm": edge_buffer_nm if edge_buffer_nm is not None else settings.storm_edge_buffer_nm,
    }


def assess_storm_route_proximity(
    storms: list[dict[str, Any]],
    voyage_number: str,
    voyage_rec: dict[str, Any],
    threshold_nm: float | None = None,
    *,
    center_buffer_nm: float | None = None,
    edge_buffer_nm: float | None = None,
) -> list[dict[str, Any]]:
    """For each storm (all progressions), flag if route enters center or edge buffer."""
    # threshold_nm kept as alias for center buffer (older callers / selfcheck)
    if center_buffer_nm is None and threshold_nm is not None:
        center_buffer_nm = threshold_nm
    center_buffer_nm = (
        center_buffer_nm if center_buffer_nm is not None else settings.storm_center_buffer_nm
    )
    edge_buffer_nm = edge_buffer_nm if edge_buffer_nm is not None else settings.storm_edge_buffer_nm

    route = _best_route(voyage_rec)
    if len(route) < 2:
        return []

    storms = normalize_active_storms(storms)
    alerts: list[dict[str, Any]] = []
    for s in storms:
        score = score_route_storms(
            route,
            [s],
            center_buffer_nm=center_buffer_nm,
            edge_buffer_nm=edge_buffer_nm,
        )
        hit = not score["storm_clear"]
        closest = score["closest_storm_nm"]
        reason = None
        if hit and score["violations"]:
            v0 = score["violations"][0]
            if v0.get("within_center_buffer"):
                reason = f"within {v0['distance_to_center_nm']:.0f} NM of center (limit {center_buffer_nm:.0f} NM)"
            else:
                reason = (
                    f"within {v0['distance_to_edge_nm']:.0f} NM of edge "
                    f"(limit {edge_buffer_nm:.0f} NM, radius {v0['radius_nm']:.0f} NM)"
                )
        alerts.append(
            {
                "storm_id": s.get("id"),
                "storm_name": s.get("name"),
                "storm_lat": s.get("lat"),
                "storm_lon": s.get("lon"),
                "radius_nm": s.get("radius_nm"),
                "wind_kn": s.get("wind_kn"),
                "category": s.get("category"),
                "progression_count": len(s.get("positions") or []),
                "positions": s.get("positions") or [],
                "distance_to_route_nm": closest,
                "center_buffer_nm": center_buffer_nm,
                "edge_buffer_nm": edge_buffer_nm,
                "threshold_nm": center_buffer_nm,  # back-compat
                "route_encounter_likely": hit,
                "storm_score": score,
                "alert": (
                    f"LIKELY TO ENCOUNTER STORM — {s.get('name', s.get('id'))} {reason}"
                    if hit and reason
                    else None
                ),
            }
        )
    return alerts


def assess_all_voyages(
    storms: list[dict[str, Any]],
    voyages: dict[str, dict[str, Any]],
    threshold_nm: float | None = None,
    *,
    center_buffer_nm: float | None = None,
    edge_buffer_nm: float | None = None,
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for voy_no, rec in voyages.items():
        prox = assess_storm_route_proximity(
            storms,
            voy_no,
            rec,
            threshold_nm,
            center_buffer_nm=center_buffer_nm,
            edge_buffer_nm=edge_buffer_nm,
        )
        likely = [p for p in prox if p.get("route_encounter_likely")]
        if likely:
            out[voy_no] = likely
    return out


def _best_route(rec: dict[str, Any]) -> list[list[float]]:
    for key in ("noon_seven_day_plan", "six_hour_plan", "master_waypoints"):
        raw = rec.get(key)
        if not raw:
            continue
        try:
            return parse_route_points(raw)
        except ValueError:
            continue
    return []


def _as_latlon_list(route: list) -> list[list[float]]:
    if not route:
        return []
    if isinstance(route[0], dict):
        return [[float(p["lat"]), float(p["lon"])] for p in route if p.get("lat") is not None]
    return [[float(p[0]), float(p[1])] for p in route]
