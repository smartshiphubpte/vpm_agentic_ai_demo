"""Normalize VoyagePM storm map-layer / registry into a common storm track shape."""

from __future__ import annotations

from typing import Any


def _max_rad34(rad34: dict | None) -> float | None:
    if not isinstance(rad34, dict):
        return None
    vals = [float(rad34[k]) for k in ("ne", "se", "sw", "nw") if rad34.get(k) is not None]
    return max(vals) if vals else None


def normalize_active_storms(raw: Any) -> list[dict[str, Any]]:
    """Flatten map-layer or registry rows into storms with center + progression track.

    Accepts:
      - map-layer: ``{"storms": [{stormId, positions, dangerCorridorRadiusNm, ...}]}``
      - list of map-layer entries or flat ``{id, lat, lon, ...}`` registry rows
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        storms = raw.get("storms") or raw.get("data") or []
        if not storms and ("lat" in raw or "positions" in raw or "stormId" in raw or "id" in raw):
            storms = [raw]
    elif isinstance(raw, list):
        storms = raw
    else:
        return []

    out: list[dict[str, Any]] = []
    for s in storms:
        if not isinstance(s, dict):
            continue
        entry = _normalize_one(s)
        if entry:
            out.append(entry)
    return out


def storms_for_optimizer(storms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """VO-facing ``{storm_id, track:[{valid_time, lat, lon, radius_nm}]}`` payload."""
    payload = []
    for s in storms:
        track = []
        for p in s.get("positions") or []:
            if p.get("lat") is None or p.get("lon") is None:
                continue
            track.append(
                {
                    "valid_time": p.get("valid_time") or p.get("validAtIso"),
                    "lat": float(p["lat"]),
                    "lon": float(p["lon"]),
                    "radius_nm": float(p.get("radius_nm") or s.get("radius_nm") or 0),
                }
            )
        if not track and s.get("lat") is not None and s.get("lon") is not None:
            track.append(
                {
                    "valid_time": s.get("valid_time"),
                    "lat": float(s["lat"]),
                    "lon": float(s["lon"]),
                    "radius_nm": float(s.get("radius_nm") or 0),
                }
            )
        if track:
            payload.append({"storm_id": s.get("id"), "track": track})
    return payload


def _normalize_one(s: dict[str, Any]) -> dict[str, Any] | None:
    sid = s.get("stormId") or s.get("storm_id") or s.get("id")
    name = s.get("displayName") or s.get("stormName") or s.get("storm_name") or s.get("name") or sid
    corridor = s.get("dangerCorridorRadiusNm")
    positions_in = s.get("positions") or s.get("track") or []

    positions: list[dict[str, Any]] = []
    for p in positions_in:
        if not isinstance(p, dict):
            continue
        lat, lon = p.get("lat"), p.get("lon")
        if lat is None or lon is None:
            continue
        rad34 = _max_rad34(p.get("rad34Nm"))
        radius = p.get("radius_nm")
        if radius is None:
            radius = corridor if corridor is not None else rad34
        if radius is None:
            radius = 0
        positions.append(
            {
                "lat": float(lat),
                "lon": float(lon),
                "radius_nm": float(radius),
                "valid_time": p.get("valid_time") or p.get("validAtIso") or p.get("timeUtc"),
                "is_present": bool(p.get("isPresent") or p.get("trackPhase") == "live"),
                "track_phase": p.get("trackPhase") or p.get("track_phase"),
                "winds": p.get("winds") or p.get("wind_kn"),
                "label": p.get("label"),
            }
        )

    # Flat registry / mock point (no positions array)
    if not positions and s.get("lat") is not None and s.get("lon") is not None:
        radius = float(corridor if corridor is not None else s.get("radius_nm") or 0)
        positions.append(
            {
                "lat": float(s["lat"]),
                "lon": float(s["lon"]),
                "radius_nm": radius,
                "valid_time": s.get("valid_time") or s.get("scrapedAt"),
                "is_present": True,
                "track_phase": "live",
                "winds": s.get("wind_kn") or s.get("winds"),
                "label": "Current",
            }
        )

    if not positions:
        return None

    present = next((p for p in positions if p.get("is_present")), positions[0])
    radius_nm = float(corridor) if corridor is not None else float(present.get("radius_nm") or 0)

    return {
        "id": sid,
        "name": name,
        "lat": present["lat"],
        "lon": present["lon"],
        "radius_nm": radius_nm,
        "wind_kn": present.get("winds") or s.get("wind_kn"),
        "category": s.get("type") or s.get("category") or s.get("status"),
        "status": s.get("status"),
        "positions": positions,  # progressions: past / live / forecast
        "danger_corridor_radius_nm": radius_nm,
    }
