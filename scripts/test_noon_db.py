#!/usr/bin/env python3
"""List or start a client-DB noon voyage in the local registry."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prevoyage_db.config import load_tenants  # noqa: E402
from prevoyage_db.connect import connect  # noqa: E402
from prevoyage_db.noon import fetch_noon_rows  # noqa: E402
from prevoyage_db.vessel_lookup import lookup_vessel_id  # noqa: E402
from vpm_agents.config import settings  # noqa: E402
from vpm_agents.tools.geo import six_hour_waypoints  # noqa: E402
from vpm_agents.tools.noon_source import row_from_db  # noqa: E402
from vpm_agents.tools.voyage_registry import VoyageRegistry, compact_voyage_number  # noqa: E402


def _tenant(key: str):
    tenants = load_tenants()
    t = tenants.get(key.lower())
    if not t:
        raise SystemExit(f"unknown tenant {key!r}; configured: {', '.join(tenants)}")
    return t


def cmd_list(tenant_key: str, voyage_number: str) -> int:
    tenant = _tenant(tenant_key)
    voy = compact_voyage_number(voyage_number)
    registry = VoyageRegistry()
    rec = registry.get(voy) or {}
    vessel_id = None
    if rec:
        try:
            vessel_id = lookup_vessel_id(tenant, rec)
        except Exception as e:
            print(f"vessel lookup skipped: {e}")
    else:
        print(f"not in registry yet: {voy} (querying DB by voyage number only)")

    raw_rows = fetch_noon_rows(tenant, voyage_number=voy, vessel_id=vessel_id)
    print(f"voyage={voy} vessel_id={vessel_id} rows={len(raw_rows)}")
    for raw in raw_rows:
        mapped = row_from_db(raw, registry_voyage=voy)
        if not mapped:
            print(f"  skip id={raw.get('id')} (bad lat/lon)")
            continue
        print(
            json.dumps(
                {
                    "noon_id": mapped["noon_id"],
                    "report_type": mapped["report_type"],
                    "observed_at": mapped["observed_at"],
                    "lat": mapped["lat"],
                    "lon": mapped["lon"],
                    "processed": registry.is_noon_processed(mapped["noon_id"]),
                },
                default=str,
            )
        )
    return 0


def _vpm_voyage(tenant, voy: str) -> dict:
    vs = tenant.vpm_schema
    keys = list(dict.fromkeys([voy, voy[1:] if voy.startswith("V") else f"V{voy}"]))
    with connect(tenant.vpm_url) as conn:
        conn.execute("SET default_transaction_read_only = ON")
        with conn.cursor() as cur:
            cur.execute(
                f'''
                SELECT "voyageNumber", "vesselId", vessel, departure, destination,
                       "cpSpeed", "cpCons", route
                FROM "{vs}"."voyages"
                WHERE replace(upper(coalesce("voyageNumber",'')), ' ', '') = ANY(%s)
                ORDER BY id DESC LIMIT 1
                ''',
                (keys,),
            )
            row = cur.fetchone()
    if not row:
        return {}
    return {
        "voyage_number": row[0],
        "vessel_id": str(row[1] or ""),
        "vessel_name": str(row[2] or "").strip(),
        "source_port": str(row[3] or "").strip(),
        "dest_port": str(row[4] or "").strip(),
        "cp_speed_kn": float(row[5] or 0) or None,
        "cp_consumption_mt_day": float(row[6]) if row[6] is not None else None,
        "route": row[7],
    }


def _geojson_waypoints(route) -> list[list[float]]:
    if not isinstance(route, list):
        return []
    out = []
    for feat in route:
        if not isinstance(feat, dict):
            continue
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) >= 2:
            lon, lat = float(coords[0]), float(coords[1])
            out.append([lat, lon])
    return out


def cmd_start(tenant_key: str, voyage_number: str) -> int:
    """Hydrate registry from VPM + noon DB so the noon service can replay the voyage."""
    tenant = _tenant(tenant_key)
    voy = compact_voyage_number(voyage_number)
    vpm = _vpm_voyage(tenant, voy)
    vessel_id = str(vpm.get("vessel_id") or "").strip() or None
    rec_lookup = {
        "vessel_id": vessel_id or "",
        "vessel_name": vpm.get("vessel_name") or "",
    }
    if rec_lookup["vessel_name"] or rec_lookup["vessel_id"]:
        try:
            vessel_id = lookup_vessel_id(tenant, rec_lookup)
        except Exception as e:
            print(f"vessel lookup skipped: {e}")

    raw_rows = fetch_noon_rows(tenant, voyage_number=voy, vessel_id=vessel_id)
    mapped = [row_from_db(r, registry_voyage=voy) for r in raw_rows]
    mapped = [m for m in mapped if m]
    if not mapped:
        print(f"no Departure/Noon/Arrival rows for {voy}", file=sys.stderr)
        return 1

    master = _geojson_waypoints(vpm.get("route"))
    if len(master) < 2:
        master = [[m["lat"], m["lon"]] for m in mapped]
    speed = float(vpm.get("cp_speed_kn") or mapped[0].get("avg_speed_kn") or 12.0)
    plan = six_hour_waypoints(master, speed, interval_h=settings.waypoint_interval_hours)
    first = mapped[0]
    now = datetime.now(timezone.utc).isoformat()

    registry = VoyageRegistry()
    registry.forget_voyage_noons(voy)
    registry.upsert(
        voy,
        {
            "voyage_number": voy,
            "vessel_id": str(vessel_id or vpm.get("vessel_id") or ""),
            "vessel_name": vpm.get("vessel_name") or first.get("vessel_name") or "",
            "source_port": vpm.get("source_port") or "Departure",
            "dest_port": vpm.get("dest_port") or "Arrival",
            "cp_speed_kn": speed,
            "cp_consumption_mt_day": vpm.get("cp_consumption_mt_day"),
            "master_waypoints": master,
            "six_hour_plan": plan,
            "ingested_at": now,
            "last_noon": None,
            "noon_history": [],
            "eov_status": None,
            "noon_seven_day_plan": None,
            "weather_due_at": now,
            "weather_plan_key": "six_hour_plan",
        },
    )

    from vpm_agents.tools import job_bus

    key = f"routeopt:{voy}:pre_voyage"
    queued = job_bus.enqueue(
        key, {"kind": "routeopt", "voyage_number": voy, "trigger": "pre_voyage"}
    )
    types = [m["report_type"] for m in mapped]
    print(
        json.dumps(
            {
                "started": voy,
                "vessel_id": vessel_id,
                "vessel_name": vpm.get("vessel_name"),
                "reports": types,
                "master_wps": len(master),
                "six_hour": len(plan),
                "routeopt_queued": queued,
                "poll_seconds": settings.noon_poll_seconds,
            },
            indent=2,
            default=str,
        )
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Show or start DB noon rows for a voyage")
    p.add_argument("--tenant", default="orion")
    p.add_argument("--start", action="store_true", help="Put voyage in registry and queue pre-voyage route-opt")
    p.add_argument("voyage_number", help="e.g. V2603L3")
    args = p.parse_args()
    if args.start:
        return cmd_start(args.tenant, args.voyage_number)
    return cmd_list(args.tenant, args.voyage_number)


if __name__ == "__main__":
    raise SystemExit(main())
