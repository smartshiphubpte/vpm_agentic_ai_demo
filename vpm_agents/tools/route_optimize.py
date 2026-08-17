"""Weather + storm-aware route alternatives — 4 objectives from MD spec.

Hard rules: (1) routes must stay on water; (2) origin/destination lat/lon are
immutable. Soft rules (weather limits, optionally storm buffers) may be loosened
when no land-safe alternate remains — never land or endpoints.
"""

from __future__ import annotations

import time
from typing import Any

from vpm_agents.config import settings
from vpm_agents.tools.agent_log import progress
from vpm_agents.tools.geo import route_length_nm, six_hour_waypoints
from vpm_agents.tools.land_mask import nudge_off_land, score_route_land
from vpm_agents.tools.route_opt_conventional import ensure_sea_route
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
    return {**row, "weather_score": score, "weather_along": summarize_weather_along(
        (row.get("weather") or {}).get("points") or [], score
    )}


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


def _nudge_plan_off_land(plan: list[dict]) -> list[dict]:
    """6h samples sit on the track; nudge any inland sample seaward (endpoints stay)."""
    if len(plan) < 2:
        return plan
    out: list[dict] = []
    last = len(plan) - 1
    for i, p in enumerate(plan):
        row = dict(p)
        if i not in (0, last):
            lat, lon = nudge_off_land(float(row["lat"]), float(row["lon"]))
            row["lat"], row["lon"] = lat, lon
        out.append(row)
    return out


def voyage_metrics(
    distance_nm: float,
    speed_kn: float,
    fuel_mt_day: float | None,
) -> dict[str, Any]:
    """Fixed-speed voyage totals. fuelMt is None when consumption was not given."""
    sog = max(0.1, float(speed_kn))
    hours = float(distance_nm) / sog
    fuel = round(hours / 24.0 * float(fuel_mt_day), 1) if fuel_mt_day is not None else None
    return {
        "distanceNm": round(float(distance_nm), 1),
        "etaHours": round(hours, 1),
        "days": round(hours / 24.0, 2),
        "fuelMt": fuel,
        "speedKn": sog,
        "fuelMtDay": fuel_mt_day,
    }


def summarize_weather_along(wx_points: list[dict], score: dict[str, Any] | None = None) -> str:
    """One-line wind/wave/swell along the 6h samples."""
    def _nums(key: str) -> list[float]:
        out: list[float] = []
        for p in wx_points or []:
            v = p.get(key)
            if v is not None:
                try:
                    out.append(float(v))
                except (TypeError, ValueError):
                    pass
        return out

    def _rng(vals: list[float], unit: str) -> str:
        if not vals:
            return f"— {unit}"
        return f"{min(vals):.1f}–{max(vals):.1f} {unit} (avg {sum(vals)/len(vals):.1f})"

    winds, waves, swells = _nums("windKn"), _nums("waveM"), _nums("swellM")
    n_v = (score or {}).get("violation_count", 0)
    limits = "within limits" if not n_v else f"{n_v} limit exceedance(s)"
    if not winds and not waves and not swells:
        return f"weather not sampled; {limits}"
    return (
        f"wind {_rng(winds, 'kn')}; wave {_rng(waves, 'm')}; "
        f"swell {_rng(swells, 'm')}; {limits}"
    )


def format_alternatives_block(routes: dict[str, Any]) -> str:
    """Pre-departure text for all four routes (omit fuel when unknown)."""
    lines: list[str] = []
    for r in routes.values():
        met = r.get("voyage") or r.get("route") or {}
        dist = met.get("distanceNm", r.get("route", {}).get("distanceNm"))
        days = met.get("days")
        hours = met.get("etaHours", r.get("route", {}).get("etaHours"))
        fuel = met.get("fuelMt", r.get("route", {}).get("fuelMt"))
        wx = r.get("weather_along") or summarize_weather_along(
            (r.get("weather") or {}).get("points") or [], r.get("weather_score"),
        )
        lines.append(f"## {r.get('label', r.get('id'))} ({r.get('id')})")
        lines.append(f"  distance: {dist} NM")
        if fuel is not None:
            lines.append(f"  fuel consumption: {fuel} MT")
        if days is not None:
            lines.append(f"  journey: {days} days ({hours} h at {met.get('speedKn', '—')} kn)")
        else:
            lines.append(f"  journey: {hours} h")
        lines.append(f"  weather: {wx}")
        lines.append(
            f"  sea: {'clear' if r.get('sea_clear', True) else 'LAND'}  "
            f"storm: {'clear' if r.get('avoids_storms') else 'encounter'}"
        )
        lines.append("")
    return "\n".join(lines).rstrip() if lines else "  (none)"


def optimize_route_alternatives(
    backend: Any,
    token: str,
    master: list[list[float]],
    speed_kn: float,
    weather_summary: dict | None,
    spec: dict[str, Any],
    storms: list[dict] | None = None,
    *,
    fuel_mt_day: float | None = None,
) -> dict[str, Any]:
    """Run each objective; land crossing is the only hard reject."""

    base_limits = {
        "max_wind_kn": float((spec.get("weather_limits") or {}).get("max_wind_kn", 35)),
        "max_wave_m": float((spec.get("weather_limits") or {}).get("max_wave_m", 4.0)),
        "max_swell_m": float((spec.get("weather_limits") or {}).get("max_swell_m", 3.0)),
    }
    reject_weather = bool(spec.get("reject_if_limits_exceeded", False))
    reject_storm = bool(spec.get("reject_if_storm_encounter", False))
    interval = float(spec.get("waypoint_interval_hours", 6))
    full_voyage = bool(spec.get("full_voyage", True))
    horizon = None if full_voyage else float(spec.get("horizon_hours", 168))

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
    t_all = time.monotonic()
    progress(
        "RouteOptimize",
        f"start {len(objectives)} objectives method={settings.route_opt_method} "
        f"algo={settings.route_opt_algo} storms={len(storms_n)} wps={len(master_pts)}",
    )
    for obj in objectives:
        oid = obj["id"]
        opt_key = obj.get("optimize_for", oid)
        t_obj = time.monotonic()
        progress("RouteOptimize", f"{oid} search start")
        opt = backend.optimize_route(
            token, opt_key, master_pts, weather=weather_summary, storms=storms_vo or None,
            speed_kn=speed_kn, fuel_mt_day=fuel_mt_day,
        )
        opt_wps = pin_route_endpoints(opt.get("waypoints") or master_pts, origin, dest)
        opt_wps = ensure_sea_route(opt_wps, origin=origin, dest=dest)
        opt = {**opt, "waypoints": opt_wps}
        if not endpoints_match(opt_wps, origin, dest):
            opt_wps = pin_route_endpoints(opt_wps, origin, dest)
            opt_wps = ensure_sea_route(opt_wps, origin=origin, dest=dest)
            opt = {**opt, "waypoints": opt_wps}
        # Hard land = landmass only. 25 NM standoff is scored, not a drop reason.
        land = score_route_land(opt_wps, sample_nm=8.0, clearance_nm=0.0)
        if not land["sea_clear"]:
            progress("RouteOptimize", f"{oid} land after search — retry without storm keep-out")
            opt = backend.optimize_route(
                token, opt_key, master_pts, weather=weather_summary, storms=None,
                speed_kn=speed_kn, fuel_mt_day=fuel_mt_day,
            )
            opt_wps = ensure_sea_route(
                pin_route_endpoints(opt.get("waypoints") or master_pts, origin, dest),
                origin=origin,
                dest=dest,
            )
            opt = {**opt, "waypoints": opt_wps}
            land = score_route_land(opt_wps, sample_nm=8.0, clearance_nm=0.0)
        if not land["sea_clear"]:
            progress("RouteOptimize", f"{oid} still land — sea-safe fallback from master")
            opt_wps = ensure_sea_route(master_pts, origin=origin, dest=dest)
            opt = {**opt, "waypoints": opt_wps}
            land = score_route_land(opt_wps, sample_nm=8.0, clearance_nm=0.0)
        if not land["sea_clear"]:
            rejected_land.append(oid)
            progress(
                "RouteOptimize",
                f"{oid} could not find a sea-clear path (not published)",
                elapsed_s=time.monotonic() - t_obj,
            )
            continue
        opt_master = [[p["lat"], p["lon"]] for p in opt_wps]
        dist_nm = route_length_nm(opt_master)
        voy = voyage_metrics(dist_nm, speed_kn, fuel_mt_day)
        opt = {**opt, "waypoints": opt_wps, "sea_clear": True, **voy}
        plan = six_hour_waypoints(opt_master, speed_kn, horizon_hours=horizon, interval_h=interval)
        plan = _nudge_plan_off_land(plan)
        standoff = score_route_land(
            opt_wps, sample_nm=8.0, clearance_nm=settings.land_clearance_nm
        )
        progress("RouteOptimize", f"{oid} weather along {len(plan)} 6h points")
        t_wx = time.monotonic()
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
            "voyage": voy,
            "six_hour_plan": plan,
            "weather": wx,
            "land_score": land,
            "land_standoff": standoff,
            "storm_score": storm_score,
            "avoids_storms": storm_score["storm_clear"],
            "sea_clear": True,
            "endpoints_fixed": True,
        }
        progress(
            "RouteOptimize",
            f"{oid} ok dist={opt.get('distanceNm')} sea_clear=True "
            f"storm_clear={storm_score['storm_clear']} "
            f"standoff={'yes' if standoff['sea_clear'] else 'relaxed'} "
            f"wx={time.monotonic() - t_wx:.1f}s",
            elapsed_s=time.monotonic() - t_obj,
        )

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
    progress(
        "RouteOptimize",
        f"all objectives done kept={len(routes)} rejected_land={rejected_land or 'none'} "
        f"suggested={suggested['id'] if suggested else None}",
        elapsed_s=time.monotonic() - t_all,
    )

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
        "speed_kn": speed_kn,
        "fuel_mt_day": fuel_mt_day,
        "alternatives_block": format_alternatives_block(routes),
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
