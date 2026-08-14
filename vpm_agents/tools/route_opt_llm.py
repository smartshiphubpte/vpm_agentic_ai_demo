"""LLM-based local route optimize — no voyagepm_be.

Loads RouteOptimizeLLMAgent.md as the system brief; user payload carries
waypoints + weather + storms + objective. Parses JSON waypoints from the reply.
"""

from __future__ import annotations

import json
import re
from typing import Any

from vpm_agents.config import settings
from vpm_agents.core.llm import chat_detail
from vpm_agents.core.spec_loader import load_agent_spec
from vpm_agents.tools.geo import haversine_nm
from vpm_agents.tools.land_mask import nudge_off_land
from vpm_agents.tools.route_opt_conventional import optimize_conventional
from vpm_agents.tools.storm_normalize import normalize_active_storms

_SPEC_NAME = "RouteOptimizeLLMAgent"
_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)


def _log(msg: str) -> None:
    print(f"[RouteOptimizeLLM] {msg}", flush=True)


def _as_pts(waypoints: list) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for p in waypoints:
        if isinstance(p, dict):
            out.append({"lat": float(p["lat"]), "lon": float(p["lon"])})
        else:
            out.append({"lat": float(p[0]), "lon": float(p[1])})
    return out


def _system_prompt() -> str:
    spec = load_agent_spec(_SPEC_NAME)
    # Full MD is the agent brief (role + hard rules + schema).
    return spec.body.strip()


def _compact_storms(storms: list[dict]) -> list[dict]:
    """Keep token use small — id/name + up to 4 track points each."""
    out: list[dict] = []
    for s in normalize_active_storms(storms or []):
        positions = (s.get("positions") or [])[:4]
        out.append(
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "lat": s.get("lat"),
                "lon": s.get("lon"),
                "radius_nm": s.get("radius_nm"),
                "positions": [
                    {
                        "lat": p.get("lat"),
                        "lon": p.get("lon"),
                        "radius_nm": p.get("radius_nm"),
                        "track_phase": p.get("track_phase"),
                    }
                    for p in positions
                ],
            }
        )
    return out[:12]


def _parse_waypoints_detail(
    text: str, origin: dict, dest: dict
) -> tuple[list[dict[str, float]] | None, str | None]:
    if not text:
        return None, "empty_response"
    blob = text.strip()
    if "```" in blob:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", blob)
        if m:
            blob = m.group(1).strip()
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        m = _JSON_OBJ.search(blob)
        if not m:
            return None, "invalid_json"
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None, "invalid_json"
    raw = data.get("waypoints") if isinstance(data, dict) else None
    if not isinstance(raw, list) or len(raw) < 2:
        return None, "missing_waypoints"
    pts = _as_pts(raw)
    pts[0] = {"lat": origin["lat"], "lon": origin["lon"]}
    pts[-1] = {"lat": dest["lat"], "lon": dest["lon"]}
    # Soft land nudge on intermediates only
    fixed = [pts[0]]
    for p in pts[1:-1]:
        lat, lon = nudge_off_land(
            p["lat"],
            p["lon"],
            max_nm=max(120.0, settings.land_clearance_nm + 80.0),
            clearance_nm=settings.land_clearance_nm,
        )
        fixed.append({"lat": round(lat, 5), "lon": round(lon, 5)})
    fixed.append(pts[-1])
    from vpm_agents.tools.land_mask import score_route_land

    if not score_route_land(fixed, clearance_nm=0.0)["sea_clear"]:
        return None, "crosses_land"
    return fixed, None


def _parse_waypoints(text: str, origin: dict, dest: dict) -> list[dict[str, float]] | None:
    path, _ = _parse_waypoints_detail(text, origin, dest)
    return path


def _metrics(path: list[dict[str, float]], objective: str) -> dict[str, float]:
    dist = 0.0
    for i in range(len(path) - 1):
        dist += haversine_nm(path[i]["lat"], path[i]["lon"], path[i + 1]["lat"], path[i + 1]["lon"])
    obj = (objective or "").lower()
    fuel_rate = 0.18 if obj in ("fuel", "lowest-fuel") else 0.22
    speed = 14.5 if obj == "fastest" else 12.0
    return {
        "distanceNm": round(dist, 1),
        "fuelMt": round(dist * fuel_rate, 1),
        "etaHours": round(dist / speed, 1) if speed else 0.0,
    }


def optimize_llm(
    objective: str,
    waypoints: list,
    weather: dict | None = None,
    storms: list | None = None,
) -> dict[str, Any]:
    """Ask the LLM for an optimized polyline; fall back to conventional on failure."""
    master = _as_pts(waypoints)
    if len(master) < 2:
        raise ValueError("need >=2 waypoints")
    origin, dest = master[0], master[-1]
    storms_n = normalize_active_storms(storms or [])

    eff = settings.effective_llm_provider
    cfg = (settings.llm_provider or "openai").strip().lower()
    if eff != cfg:
        _log(
            f"auto provider: {cfg} → {eff} "
            f"(Gemini key in OPENAI_API_KEY or GEMINI_API_KEY only — set VPM_LLM_PROVIDER=gemini)"
        )

    # Cap waypoint payload size for the model
    max_wp = 40
    if len(master) > max_wp:
        step = max(1, len(master) // max_wp)
        slim = [master[i] for i in range(0, len(master) - 1, step)] + [master[-1]]
    else:
        slim = master

    user_payload = {
        "optimize_for": objective,
        "waypoints": slim,
        "weather": weather or {},
        "storms": _compact_storms(storms_n),
        "buffers": {
            "center_buffer_nm": settings.storm_center_buffer_nm,
            "edge_buffer_nm": settings.storm_edge_buffer_nm,
            "land_clearance_nm": settings.land_clearance_nm,
        },
        "weather_limits": {
            "max_wind_kn": settings.weather_wind_threshold_kn,
            "max_wave_m": settings.weather_wave_threshold_m,
            "max_swell_m": settings.weather_swell_threshold_m,
        },
    }
    temp = 0.2
    try:
        temp = float(load_agent_spec(_SPEC_NAME).get("temperature", 0.2))
    except Exception:
        pass

    text, chat_err = chat_detail(
        [
            {"role": "system", "content": _system_prompt()},
            {
                "role": "user",
                "content": (
                    "Optimize this voyage route for the given objective. "
                    "Return JSON only.\n\n"
                    + json.dumps(user_payload, default=str)
                ),
            },
        ],
        temperature=temp,
        json_mode=True,
    )
    path, parse_err = _parse_waypoints_detail(text or "", origin, dest)
    if not path:
        llm_error = chat_err or parse_err or "unknown"
        _log(
            f"{objective} fallback → conventional ({settings.route_opt_algo}): {llm_error}"
        )
        if chat_err and settings.effective_llm_provider != "gemini" and "AuthenticationError" in chat_err:
            _log(
                f"{objective} hint: Gemini keys need VPM_LLM_PROVIDER=gemini + GEMINI_API_KEY"
            )
        return {
            **optimize_conventional(objective, master, weather, storms),
            "provider": f"local-llm-fallback-{objective}",
            "llm_fallback": True,
            "llm_error": llm_error,
        }

    _log(f"{objective} ok model={settings.llm_model} waypoints={len(path)}")

    wps = [{"lat": p["lat"], "lon": p["lon"], "seq": i} for i, p in enumerate(path)]
    m = _metrics(path, objective)
    return {
        "objective": objective,
        "waypoints": wps,
        "distanceNm": m["distanceNm"],
        "fuelMt": m["fuelMt"],
        "etaHours": m["etaHours"],
        "weatherAware": bool(weather),
        "stormAware": bool(storms_n),
        "provider": f"local-llm-{settings.llm_model}-{objective}",
        "llm_fallback": False,
    }
