"""Weather + storm-aware route alternatives — 4 objectives from MD spec.

Hard rules: (1) routes must stay on water; (2) origin/destination lat/lon are
immutable. Soft rules (weather limits, optionally storm buffers) may be loosened
when no land-safe alternate remains — never land or endpoints.
"""

from __future__ import annotations

from typing import Any

from vpm_agents.config import settings
from vpm_agents.tools.geo import six_hour_waypoints
from vpm_agents.tools.land_mask import score_route_land
from vpm_agents.tools.storm_normalize import normalize_active_storms, storms_for_optimizer
from vpm_agents.tools.storm_proximity import score_route_storms


def pin_route_endpoints(
    waypoints: list[Any],
    start: dict[str, float] | list[float],
    end: dict[str, float] | list[float],
) -> list[dict[str, Any]]:
    """Force first/last WP to exact origin/destination (hard rule)."""
    def _ll(p: dict[str, float] | list[float]) -> tuple[float, float]:
        if isinstance(p, dict):
            return float(p["lat"]), float(p["lon"])
        return float(p[0]), float(p[1])

    slat, slon = _ll(start)
    elat, elon = _ll(end)
    out: list[dict[str, Any]] = []
    for i, p in enumerate(waypoints):
        if isinstance(p, dict):
            row = dict(p)
            lat, lon = float(p["lat"]), float(p["lon"])
        else:
            lat, lon = float(p[0]), float(p[1])
            row = {"lat": lat, "lon": lon}
        if i == 0:
            row["lat"], row["lon"] = slat, slon
        elif i == len(waypoints) - 1:
            row["lat"], row["lon"] = elat, elon
        row.setdefault("seq", i)
        out.append(row)
    if len(out) < 2:
        return [{"lat": slat, "lon": slon, "seq": 0}, {"lat": elat, "lon": elon, "seq": 1}]
    out[0]["lat"], out[0]["lon"] = slat, slon
    out[-1]["lat"], out[-1]["lon"] = elat, elon
    return out


def endpoints_match(
    waypoints: list[Any],
    start: dict[str, float] | list[float],
    end: dict[str, float] | list[float],
    *,
    tol_deg: float = 1e-5,
) -> bool:
    """True when first/last WP match origin/destination within tol."""
    if not waypoints or len(waypoints) < 2:
        return False

    def _ll(p: Any) -> tuple[float, float]:
        if isinstance(p, dict):
            return float(p["lat"]), float(p["lon"])
        return float(p[0]), float(p[1])

    s0, s1 = _ll(start), _ll(waypoints[0])
    e0, e1 = _ll(end), _ll(waypoints[-1])
    return (
        abs(s0[0] - s1[0]) <= tol_deg
        and abs(s0[1] - s1[1]) <= tol_deg
        and abs(e0[0] - e1[0]) <= tol_deg
        and abs(e0[1] - e1[1]) <= tol_deg
    )


def score_route_weather(
    plan: list[dict],
    wx_points: list[dict],
    *,
    max_wind_kn: float,
    max_wave_m: float,
    max_swell_m: float,
) -> dict[str, Any]:
    """Score a 6h plan against weather limits; count violations."""
    violations: list[dict] = []
    for i, p in enumerate(plan):
        wx = wx_points[i] if i < len(wx_points) else {}
        reasons = []
        w, wave, swell = wx.get("windKn"), wx.get("waveM"), wx.get("swellM")
        if w is not None and float(w) > max_wind_kn:
            reasons.append(f"wind {w} > {max_wind_kn} kn")
        if wave is not None and float(wave) > max_wave_m:
            reasons.append(f"wave {wave} > {max_wave_m} m")
        if swell is not None and float(swell) > max_swell_m:
            reasons.append(f"swell {swell} > {max_swell_m} m")
        if reasons:
            violations.append(
                {
                    "seq": p.get("seq", i),
                    "eta_utc": p.get("eta_utc"),
                    "lat": p.get("lat"),
                    "lon": p.get("lon"),
                    "reasons": reasons,
                }
            )
    n = max(len(plan), 1)
    return {
        "violation_count": len(violations),
        "violations": violations,
        "weather_score": round(100 * (1 - len(violations) / n), 1),
        "within_limits": len(violations) == 0,
    }


def _weather_relax_tiers(base: dict[str, float]) -> list[dict[str, float]]:
    """Progressively looser weather caps — land rule is never in this list."""
    wind0 = float(base["max_wind_kn"])
    wave0 = float(base["max_wave_m"])
    swell0 = float(base["max_swell_m"])
    return [
        {"max_wind_kn": wind0, "max_wave_m": wave0, "max_swell_m": swell0},
        {"max_wind_kn": wind0 * 1.15, "max_wave_m": wave0 * 1.15, "max_swell_m": swell0 * 1.15},
        {"max_wind_kn": wind0 * 1.35, "max_wave_m": wave0 * 1.35, "max_swell_m": swell0 * 1.35},
        {"max_wind_kn": wind0 * 1.6, "max_wave_m": wave0 * 1.6, "max_swell_m": swell0 * 1.6},
    ]


def _with_weather_score(row: dict[str, Any], limits: dict[str, float]) -> dict[str, Any]:
    score = score_route_weather(
        row["six_hour_plan"],
        (row.get("weather") or {}).get("points") or [],
        max_wind_kn=float(limits["max_wind_kn"]),
        max_wave_m=float(limits["max_wave_m"]),
        max_swell_m=float(limits["max_swell_m"]),
    )
    return {**row, "weather_score": score}


def _filter_soft(
    candidates: dict[str, Any],
    *,
    limits: dict[str, float],
    require_weather: bool,
    require_storm_clear: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for oid, r in candidates.items():
        row = _with_weather_score(r, limits)
        if require_weather and not row["weather_score"]["within_limits"]:
            continue
        if require_storm_clear and not row.get("avoids_storms"):
            continue
        out[oid] = row
    return out


def optimize_route_alternatives(
    backend: Any,
    token: str,
    master: list[list[float]],
    speed_kn: float,
    weather_summary: dict | None,
    spec: dict[str, Any],
    storms: list[dict] | None = None,
) -> dict[str, Any]:
    """Run each objective from agent MD; attach weather + storm-scored 6h plans.

    Land crossing is always rejected. If soft weather/storm rules leave no
    alternate, weather limits are loosened — the no-land rule is never loosened.
    """
    base_limits = {
        "max_wind_kn": float((spec.get("weather_limits") or {}).get("max_wind_kn", 35)),
        "max_wave_m": float((spec.get("weather_limits") or {}).get("max_wave_m", 4.0)),
        "max_swell_m": float((spec.get("weather_limits") or {}).get("max_swell_m", 3.0)),
    }
    horizon = float(spec.get("horizon_hours", 168))
    interval = float(spec.get("waypoint_interval_hours", 6))
    reject_weather = bool(spec.get("reject_if_limits_exceeded", False))
    reject_storm = bool(spec.get("reject_if_storm_encounter", False))

    storms_n = normalize_active_storms(storms or [])
    storms_vo = storms_for_optimizer(storms_n)

    master_pts = [{"lat": p[0], "lon": p[1]} for p in master]
    objectives = spec.get("objectives") or [
        {"id": "fastest", "optimize_for": "fastest", "label": "Fastest"},
        {"id": "shortest", "optimize_for": "shortest", "label": "Shortest distance"},
        {"id": "fuel", "optimize_for": "fuel", "label": "Least fuel"},
        {"id": "safest", "optimize_for": "safest", "label": "Safest"},
    ]

    sea_routes: dict[str, Any] = {}
    rejected_land: list[str] = []
    rejected_endpoints: list[str] = []
    origin, dest = master_pts[0], master_pts[-1]
    for obj in objectives:
        oid = obj["id"]
        opt_key = obj.get("optimize_for", oid)
        opt = backend.optimize_route(
            token, opt_key, master_pts, weather=weather_summary, storms=storms_vo or None
        )
        opt_wps = pin_route_endpoints(opt.get("waypoints") or master_pts, origin, dest)
        opt = {**opt, "waypoints": opt_wps}
        if not endpoints_match(opt_wps, origin, dest):
            rejected_endpoints.append(oid)
            continue
        opt_master = [[p["lat"], p["lon"]] for p in opt_wps]
        land = score_route_land(opt_master, clearance_nm=settings.land_clearance_nm)
        if not land["sea_clear"]:
            rejected_land.append(oid)
            continue
        plan = six_hour_waypoints(opt_master, speed_kn, horizon_hours=horizon, interval_h=interval)
        plan_land = score_route_land(plan, clearance_nm=settings.land_clearance_nm)
        if not plan_land["sea_clear"]:
            rejected_land.append(oid)
            continue
        wx = backend.weather_along_route(token, [{"lat": p["lat"], "lon": p["lon"]} for p in plan])
        storm_score = score_route_storms(
            plan,
            storms_n,
            center_buffer_nm=settings.storm_center_buffer_nm,
            edge_buffer_nm=settings.storm_edge_buffer_nm,
        )
        sea_routes[oid] = {
            "id": oid,
            "label": obj.get("label", oid),
            "optimize_for": opt_key,
            "route": opt,
            "six_hour_plan": plan,
            "weather": wx,
            "land_score": land,
            "storm_score": storm_score,
            "avoids_storms": storm_score["storm_clear"],
            "sea_clear": True,
            "endpoints_fixed": True,
        }

    tiers = _weather_relax_tiers(base_limits)
    routes: dict[str, Any] = {}
    applied_limits = dict(base_limits)
    weather_relaxed = False

    if sea_routes:
        # 1) Prefer nominal weather (+ optional storm hard-filter).
        routes = _filter_soft(
            sea_routes,
            limits=tiers[0],
            require_weather=True,
            require_storm_clear=reject_storm,
        )
        if routes:
            applied_limits = dict(tiers[0])
        else:
            # 2) Loosen weather only (never land). Keep storm reject if configured.
            for limits in tiers[1:]:
                routes = _filter_soft(
                    sea_routes,
                    limits=limits,
                    require_weather=True,
                    require_storm_clear=reject_storm,
                )
                if routes:
                    applied_limits = dict(limits)
                    weather_relaxed = True
                    break
            # 3) Still empty: keep every sea-clear route; score at nominal for ranking.
            if not routes:
                routes = _filter_soft(
                    sea_routes,
                    limits=base_limits,
                    require_weather=False,
                    require_storm_clear=False,
                )
                applied_limits = dict(base_limits)
                weather_relaxed = True

    preferred = spec.get("preferred", "safest")
    suggested = _pick_suggested(routes, preferred)

    return {
        "routes": routes,
        "suggested_id": suggested["id"] if suggested else None,
        "weather_limits": base_limits,
        "weather_limits_applied": {
            "max_wind_kn": round(float(applied_limits["max_wind_kn"]), 2),
            "max_wave_m": round(float(applied_limits["max_wave_m"]), 3),
            "max_swell_m": round(float(applied_limits["max_swell_m"]), 3),
        },
        "weather_relaxed": weather_relaxed,
        "hard_rules": {"no_landmass": True, "fixed_endpoints": True},
        "rejected_for_land": rejected_land,
        "rejected_for_endpoints": rejected_endpoints,
        "storm_buffers": {
            "center_buffer_nm": settings.storm_center_buffer_nm,
            "edge_buffer_nm": settings.storm_edge_buffer_nm,
        },
        "storms_considered": [
            {"id": s.get("id"), "name": s.get("name"), "progression_count": len(s.get("positions") or [])}
            for s in storms_n
        ],
        "objective_count": len(routes),
    }


def _pick_suggested(routes: dict[str, Any], preferred: str) -> dict | None:
    """Prefer storm-clear sea routes; among those, preferred objective, else best weather score."""
    if not routes:
        return None
    clear = {k: v for k, v in routes.items() if v.get("avoids_storms")}
    pool = clear or routes
    if preferred in pool:
        return pool[preferred]
    return max(
        pool.values(),
        key=lambda r: (
            1 if r.get("avoids_storms") else 0,
            r["weather_score"]["weather_score"],
            r.get("storm_score", {}).get("storm_score", 0),
        ),
    )
