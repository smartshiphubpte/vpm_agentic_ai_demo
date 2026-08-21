"""Map parsed pre-voyage registry record → VPM DB rows (GeoJSON routes)."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_FULL = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _month_num(raw: str) -> int | None:
    """1–12 from numeric month, JUN, Sept, September, etc."""
    m = raw.strip().lower()
    if m.isdigit():
        n = int(m)
        return n if 1 <= n <= 12 else None
    if m in _MONTH_FULL:
        return _MONTH_FULL[m]
    if len(m) >= 3:
        return _MONTHS.get(m[:3])
    return None


def _parse_ts(raw: Any) -> datetime | None:
    """ISO or Pre-Dep Excel: '30-JUL-2026 1730LT (UTC+7)' → aware UTC datetime."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, datetime):
        dt = raw
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    s = str(raw).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    m = re.match(
        r"(?i)(\d{1,2})[-/]([A-Za-z]+|\d{1,2})[-/](\d{4})\s+(\d{3,4})\s*(?:LT)?\s*"
        r"\(UTC\s*([+-]\d{1,2})(?::(\d{2}))?\)",
        s,
    )
    if m:
        day = int(m.group(1))
        month = _month_num(m.group(2))
        if month:
            year = int(m.group(3))
            hm = m.group(4).zfill(4)
            hour, minute = int(hm[:2]), int(hm[2:])
            off_h, off_m = int(m.group(5)), int(m.group(6) or 0)
            from datetime import timedelta

            local = datetime(year, month, day, hour, minute)
            utc = local - timedelta(hours=off_h, minutes=off_m if off_h >= 0 else -off_m)
            return utc.replace(tzinfo=timezone.utc)
    m = re.match(
        r"(?i)(\d{1,2})[-/]([A-Za-z]+|\d{1,2})[-/](\d{4})\s+(\d{1,2}):(\d{2})\s*(?:\(UTC\s*)?([+-]\d{1,2})\)?",
        s,
    )
    if not m:
        return None
    month = _month_num(m.group(2))
    if not month:
        return None
    from datetime import timedelta

    local = datetime(int(m.group(3)), month, int(m.group(1)), int(m.group(4)), int(m.group(5)))
    off = int(m.group(6))
    return (local - timedelta(hours=off)).replace(tzinfo=timezone.utc)


def waypoints_to_geojson(
    waypoints: list[Any],
    *,
    speed_kn: float,
    names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """BE master_routes.route format — GeoJSON Point features, [lon, lat]."""
    out: list[dict[str, Any]] = []
    names = names or []
    for i, wp in enumerate(waypoints):
        if isinstance(wp, (list, tuple)) and len(wp) >= 2:
            lat, lon = float(wp[0]), float(wp[1])
        elif isinstance(wp, dict):
            lat, lon = float(wp["lat"]), float(wp["lon"])
        else:
            continue
        if abs(lat) > 90.0 or abs(lon) > 180.0:
            raise ValueError(
                f"master route point {i + 1} is not decimal degrees (lat={lat}, lon={lon})"
            )
        out.append(
            {
                "type": "Feature",
                "properties": {
                    "_id": str(uuid.uuid4()),
                    "seqId": i + 1,
                    "speed": speed_kn,
                    **({"name": names[i]} if i < len(names) and names[i] else {}),
                },
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
            }
        )
    return out


def build_voyage_row(record: dict[str, Any], *, vessel_id: str, route_geojson: list[dict]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    etd = _parse_ts(record.get("etd")) or now
    eta = _parse_ts(record.get("eta"))
    row: dict[str, Any] = {
        "voyageNumber": str(record["voyage_number"]),
        "vessel": str(record.get("vessel_name") or record.get("vessel_id") or "").strip(),
        "vesselId": str(vessel_id),
        "departure": str(record.get("source_port") or "").strip(),
        "destination": str(record.get("dest_port") or "").strip(),
        "etd": etd,
        "route": route_geojson,
        "routeType": record.get("route_type") or "captainsRoute",
        "cpSpeed": float(record.get("cp_speed_kn") or 0),
    }
    if eta is not None:
        row["eta"] = eta
    cons = record.get("cp_consumption_mt_day")
    if cons is not None:
        row["cpCons"] = float(cons)
    cond = (record.get("condition") or "").strip()
    if cond:
        row["vesselCondition"] = cond
    disp = record.get("displacement")
    if disp is not None:
        row["displacement"] = float(disp)
    cargo = record.get("cargo_weight")
    if cargo is not None:
        row["cargoWeight"] = float(cargo)
    draft = record.get("max_draft_on_departure")
    if draft is not None:
        row["maxDraftOnDeparture"] = float(draft)
    return row


def build_master_route_row(
    record: dict[str, Any],
    *,
    voyage_id: int,
    route_geojson: list[dict],
    actor: str,
    now: datetime,
) -> dict[str, Any]:
    etd = _parse_ts(record.get("etd")) or now
    row: dict[str, Any] = {
        "voyageId": voyage_id,
        "voyageNumber": str(record["voyage_number"]),
        "vesselName": str(record.get("vessel_name") or "").strip(),
        "route": route_geojson,
        "intRoute": [],
        "avgSpeed": float(record.get("cp_speed_kn") or 0),
        "from": etd,
        "is_active": True,
        "createdBy": actor,
        "lastUpdatedBy": actor,
        "createdAt": now,
        "lastUpdatedAt": now,
    }
    cons = record.get("cp_consumption_mt_day")
    if cons is not None:
        row["fuelConsPerDay"] = str(cons)
    return row


_VO_TYPE = {
    "fastest": "fastest",
    "shortest": "shortest",
    "fuel": "lowest-fuel",
    "lowest-fuel": "lowest-fuel",
    "safest": "safest",
}


def vo_published_route_type(objective: str) -> str:
    return _VO_TYPE.get((objective or "").strip().lower(), "safest")


def _beaufort_from_kn(kn: float) -> int:
    for i, cap in enumerate((1, 4, 7, 11, 17, 22, 28, 34, 41, 48, 56, 64)):
        if kn < cap:
            return i
    return 12


def _douglas(wave_m: float) -> int:
    for i, cap in enumerate((0.1, 0.5, 1.25, 2.5, 4.0, 6.0, 9.0, 14.0)):
        if wave_m < cap:
            return i + 1
    return 9


def _iso_z(raw: Any) -> str:
    dt = _parse_ts(raw)
    if dt is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _eta_label(hours: float | None) -> str | None:
    if hours is None:
        return None
    h = float(hours)
    if h <= 0:
        return None
    rounded = round(h * 10) / 10
    if rounded < 24:
        return f"{rounded:.1f}h"
    days = int(rounded // 24)
    rem = round(rounded - days * 24, 1)
    return f"{days}d" if rem <= 0 else f"{days}d {rem:.1f}h"


def _arrival_eta_label(etd: Any, hours: float | None) -> str | None:
    if hours is None:
        return None
    start = _parse_ts(etd)
    if start is None:
        return None
    from datetime import timedelta

    arr = start + timedelta(hours=float(hours))
    return arr.astimezone(timezone.utc).strftime("%d %b %Y %H:%M UTC")


def build_vo_comparison_metadata(
    voyage: dict[str, Any],
    weather_points: list[dict[str, Any]] | None,
    *,
    etd: Any = None,
) -> dict[str, Any]:
    """Same field names the GUI reads from voComparisonMetadata on the first WP."""
    dist = voyage.get("distanceNm")
    hours = voyage.get("etaHours")
    fuel = voyage.get("fuelMt")
    speed = voyage.get("speedKn")
    winds, waves, swells, press = [], [], [], []
    for p in weather_points or []:
        if p.get("windKn") is not None:
            winds.append(float(p["windKn"]))
        if p.get("waveM") is not None:
            waves.append(float(p["waveM"]))
        if p.get("swellM") is not None:
            swells.append(float(p["swellM"]))
        if p.get("pressureHpa") is not None:
            press.append(float(p["pressureHpa"]))
    avg_w = sum(winds) / len(winds) if winds else None
    avg_wv = sum(waves) / len(waves) if waves else None
    avg_sw = sum(swells) / len(swells) if swells else None
    avg_p = sum(press) / len(press) if press else None
    dash = "—"
    if avg_w is None and avg_wv is None and avg_sw is None and avg_p is None:
        weather = None
        safety, risk = None, None
    else:
        bf = _beaufort_from_kn(avg_w) if avg_w is not None else None
        weather = {
            "wind": str(bf) if bf is not None else dash,
            "windSub": f"({avg_w:.1f} kts)" if avg_w is not None else dash,
            "wave": f"{avg_wv:.2f} m" if avg_wv is not None else dash,
            "seaState": f"Sea State {_douglas(avg_wv)}" if avg_wv is not None else dash,
            "swell": f"{avg_sw:.2f} m" if avg_sw is not None else dash,
            "pressure": str(int(round(avg_p))) if avg_p is not None else dash,
            "pressureSub": "(avg along route)" if avg_p is not None else dash,
        }
        levels = []
        if bf is not None:
            levels.append("high" if bf > 6 else "moderate" if bf > 4 else "low")
        if avg_wv is not None:
            levels.append("high" if avg_wv >= 5 else "moderate" if avg_wv >= 4 else "low")
        if avg_sw is not None:
            levels.append("high" if avg_sw >= 4.5 else "moderate" if avg_sw >= 3.5 else "low")
        if "high" in levels:
            safety, risk = "Low", "High"
        elif "moderate" in levels:
            safety, risk = "Medium", "Moderate"
        else:
            safety, risk = "High", "Low"
    meta: dict[str, Any] = {
        "distanceLabel": f"{float(dist):.1f} NM" if dist is not None else None,
        "etaLabel": _eta_label(float(hours) if hours is not None else None),
        "arrivalEtaLabel": _arrival_eta_label(etd, float(hours) if hours is not None else None),
        "speedLabel": f"{float(speed):.2f} kts" if speed else None,
        "fuelLabel": f"{float(fuel):.1f}" if fuel is not None else None,
        "weather": weather,
        "safetyLabel": safety,
        "riskLabel": risk,
    }
    return meta


def suggested_route_geojson(
    waypoints: list[Any],
    *,
    objective: str,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """VO suggested_routes.route: Point features, [lon,lat], index/stoppage + publish props."""
    out: list[dict[str, Any]] = []
    vo_type = vo_published_route_type(objective)
    for i, wp in enumerate(waypoints or []):
        if isinstance(wp, (list, tuple)) and len(wp) >= 2:
            lat, lon = float(wp[0]), float(wp[1])
        elif isinstance(wp, dict) and wp.get("lat") is not None:
            lat, lon = float(wp["lat"]), float(wp["lon"])
        else:
            continue
        props: dict[str, Any] = {"index": i, "stoppage": 0}
        if i == 0:
            props["voPublishSource"] = "voyageOptimization"
            props["voPublishedRouteType"] = vo_type
            props["voComparisonMetadata"] = metadata
        out.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": props,
            }
        )
    return out


def plan_to_int_route(
    plan: list[dict[str, Any]],
    *,
    speed_kn: float,
) -> list[dict[str, Any]]:
    """VO suggested_routes.intRoute: 6h points with time/speed/bearing/distToGo."""
    from vpm_agents.tools.geo import haversine_nm, initial_bearing_deg

    pts: list[tuple[float, float, str]] = []
    for p in plan or []:
        if p.get("lat") is None or p.get("lon") is None:
            continue
        pts.append((float(p["lat"]), float(p["lon"]), str(p.get("eta_utc") or "")))
    if not pts:
        return []
    remain = [0.0] * len(pts)
    acc = 0.0
    for i in range(len(pts) - 1, 0, -1):
        acc += haversine_nm(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1])
        remain[i - 1] = acc
    out: list[dict[str, Any]] = []
    for i, (lat, lon, eta) in enumerate(pts):
        if i + 1 < len(pts):
            brg = initial_bearing_deg(lat, lon, pts[i + 1][0], pts[i + 1][1])
        elif i > 0:
            brg = initial_bearing_deg(pts[i - 1][0], pts[i - 1][1], lat, lon)
        else:
            brg = 0.0
        out.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "time": _iso_z(eta) if eta else _iso_z(None),
                    "speed": float(speed_kn),
                    "bearing": brg,
                    "distToGo": remain[i],
                },
            }
        )
    return out


def json_dumps(value: Any) -> str:
    return json.dumps(value, default=str)


if __name__ == "__main__":
    rec = {
        "voyage_number": "VTEST",
        "vessel_name": "MV Test",
        "vessel_id": "123",
        "source_port": "Singapore",
        "dest_port": "Hong Kong",
        "cp_speed_kn": 12.0,
        "cp_consumption_mt_day": 28.5,
        "master_waypoints": [[1.0, 103.0], [22.0, 114.0]],
        "waypoint_names": ["A", "B"],
        "etd": "2026-08-17T00:00:00+00:00",
    }
    geo = waypoints_to_geojson(rec["master_waypoints"], speed_kn=12.0, names=rec["waypoint_names"])
    assert geo[0]["geometry"]["coordinates"] == [103.0, 1.0]
    voy = build_voyage_row(rec, vessel_id="99", route_geojson=geo)
    assert voy["voyageNumber"] == "VTEST" and voy["cpSpeed"] == 12.0
    assert voy["etd"].isoformat() == "2026-08-17T00:00:00+00:00"
    etd = _parse_ts("30-JUL-2026 1730LT (UTC+7)")
    assert etd is not None and etd.hour == 10 and etd.minute == 30 and etd.day == 30
    sept = _parse_ts("15-Sept-2026 16:00 -3")
    assert sept is not None and sept.month == 9 and sept.day == 15 and sept.hour == 19
    assert _month_num("Sept") == 9 and _month_num("September") == 9
    meta = build_vo_comparison_metadata(
        {"distanceNm": 100.0, "etaHours": 10.0, "fuelMt": 20.0, "speedKn": 10.0},
        [{"windKn": 10.0, "waveM": 1.3, "swellM": 0.7, "pressureHpa": 1015}],
        etd="2026-08-17T00:00:00+00:00",
    )
    assert meta["distanceLabel"] == "100.0 NM" and meta["fuelLabel"] == "20.0"
    feats = suggested_route_geojson(
        [{"lat": 1.0, "lon": 103.0}, {"lat": 2.0, "lon": 104.0}],
        objective="fuel",
        metadata=meta,
    )
    assert feats[0]["properties"]["voPublishedRouteType"] == "lowest-fuel"
    assert feats[0]["geometry"]["coordinates"] == [103.0, 1.0]
    intro = plan_to_int_route(
        [{"lat": 1.0, "lon": 103.0, "eta_utc": "2026-08-17T00:00:00+00:00"},
         {"lat": 2.0, "lon": 104.0, "eta_utc": "2026-08-17T06:00:00+00:00"}],
        speed_kn=12.0,
    )
    assert intro[0]["properties"]["distToGo"] > 0 and "bearing" in intro[0]["properties"]
    print("prevoyage_db.mapper self-check ok")
