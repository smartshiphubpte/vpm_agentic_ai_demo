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
        r"(?i)(\d{1,2})[-/]([A-Za-z]{3}|\d{1,2})[-/](\d{4})\s+(\d{3,4})\s*(?:LT)?\s*"
        r"\(UTC\s*([+-]\d{1,2})(?::(\d{2}))?\)",
        s,
    )
    if not m:
        return None
    day = int(m.group(1))
    mon_raw = m.group(2)
    month = _MONTHS.get(mon_raw[:3].lower()) if mon_raw.isalpha() else int(mon_raw)
    if not month:
        return None
    year = int(m.group(3))
    hm = m.group(4).zfill(4)
    hour, minute = int(hm[:2]), int(hm[2:])
    off_h, off_m = int(m.group(5)), int(m.group(6) or 0)
    from datetime import timedelta

    local = datetime(year, month, day, hour, minute)
    utc = local - timedelta(hours=off_h, minutes=off_m if off_h >= 0 else -off_m)
    return utc.replace(tzinfo=timezone.utc)


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
    etd = _parse_ts("30-JUL-2026 1730LT (UTC+7)")
    assert etd is not None and etd.hour == 10 and etd.minute == 30 and etd.day == 30
    print("prevoyage_db.mapper self-check ok")
