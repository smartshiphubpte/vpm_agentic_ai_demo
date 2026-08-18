"""Transactional upsert into VPM voyages + master_routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from prevoyage_db.config import TenantDbConfig, settings
from prevoyage_db.connect import connect
from prevoyage_db.log import log
from prevoyage_db.mapper import build_master_route_row, build_voyage_row, json_dumps, waypoints_to_geojson
from prevoyage_db.vessel_lookup import lookup_vessel_id

_IDENT = __import__("re").compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _table(schema: str, table: str) -> str:
    if not _IDENT.match(schema) or not _IDENT.match(table):
        raise ValueError(f"unsafe schema/table: {schema}.{table}")
    return f'"{schema}"."{table}"'


def ingest_prevoyage(
    tenant: TenantDbConfig, record: dict[str, Any], *, dry_run: bool | None = None
) -> dict[str, Any]:
    """Write one pre-voyage record. Returns {voyage_id, master_route_id, action}."""
    waypoints = record.get("master_waypoints") or []
    if len(waypoints) < 2:
        raise ValueError("master_waypoints needs >= 2 points")

    vessel_id = lookup_vessel_id(tenant, record)
    speed = float(record.get("cp_speed_kn") or 0)
    names = record.get("waypoint_names") or []
    route_geojson = waypoints_to_geojson(waypoints, speed_kn=speed, names=names)
    voyage_row = build_voyage_row(record, vessel_id=vessel_id, route_geojson=route_geojson)
    voyage_row["routeType"] = voyage_row.get("routeType") or settings.default_route_type

    if dry_run if dry_run is not None else settings.dry_run:
        log(
            tenant.key,
            f"DRY-RUN voy={voyage_row['voyageNumber']} vesselId={vessel_id} "
            f"wps={len(route_geojson)} schema={tenant.vpm_schema}",
        )
        return {
            "voyage_id": None,
            "master_route_id": None,
            "action": "dry_run",
            "vessel_id": vessel_id,
        }

    voyages = _table(tenant.vpm_schema, settings.voyages_table)
    master = _table(tenant.vpm_schema, settings.master_routes_table)
    now = datetime.now(timezone.utc)
    actor = settings.actor

    with connect(tenant.vpm_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id FROM {voyages}
                WHERE "voyageNumber" = %s AND "vesselId" = %s
                ORDER BY id DESC LIMIT 1
                """,
                (voyage_row["voyageNumber"], voyage_row["vesselId"]),
            )
            existing = cur.fetchone()
            action = "insert"

            if existing:
                voyage_id = int(existing[0])
                action = "update"
                cur.execute(
                    f"""
                    UPDATE {voyages} SET
                        vessel = %s,
                        departure = %s,
                        destination = %s,
                        etd = %s,
                        route = %s::jsonb,
                        "routeType" = %s,
                        "cpSpeed" = %s,
                        "cpCons" = %s,
                        "vesselCondition" = %s,
                        displacement = %s,
                        "cargoWeight" = %s,
                        "maxDraftOnDeparture" = %s
                    WHERE id = %s
                    """,
                    (
                        voyage_row["vessel"],
                        voyage_row["departure"],
                        voyage_row["destination"],
                        voyage_row["etd"],
                        json_dumps(voyage_row["route"]),
                        voyage_row["routeType"],
                        voyage_row["cpSpeed"],
                        voyage_row.get("cpCons"),
                        voyage_row.get("vesselCondition"),
                        voyage_row.get("displacement"),
                        voyage_row.get("cargoWeight"),
                        voyage_row.get("maxDraftOnDeparture"),
                        voyage_id,
                    ),
                )
            else:
                cur.execute(
                    f"""
                    INSERT INTO {voyages} (
                        "voyageNumber", vessel, "vesselId", departure, destination,
                        etd, route, "routeType", "cpSpeed", "cpCons", "vesselCondition",
                        displacement, "cargoWeight", "maxDraftOnDeparture"
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        voyage_row["voyageNumber"],
                        voyage_row["vessel"],
                        voyage_row["vesselId"],
                        voyage_row["departure"],
                        voyage_row["destination"],
                        voyage_row["etd"],
                        json_dumps(voyage_row["route"]),
                        voyage_row["routeType"],
                        voyage_row["cpSpeed"],
                        voyage_row.get("cpCons"),
                        voyage_row.get("vesselCondition"),
                        voyage_row.get("displacement"),
                        voyage_row.get("cargoWeight"),
                        voyage_row.get("maxDraftOnDeparture"),
                    ),
                )
                voyage_id = int(cur.fetchone()[0])

            mr = build_master_route_row(
                record,
                voyage_id=voyage_id,
                route_geojson=route_geojson,
                actor=actor,
                now=now,
            )

            cur.execute(
                f"""
                SELECT id FROM {master}
                WHERE "voyageId" = %s AND "voyageNumber" = %s AND is_active = true
                ORDER BY id DESC LIMIT 1
                """,
                (voyage_id, mr["voyageNumber"]),
            )
            mr_existing = cur.fetchone()
            master_route_id: int | None = None

            if mr_existing:
                master_route_id = int(mr_existing[0])
                cur.execute(
                    f"""
                    UPDATE {master} SET
                        "vesselName" = %s,
                        route = %s::jsonb,
                        "intRoute" = %s::jsonb,
                        "avgSpeed" = %s,
                        "from" = %s,
                        "fuelConsPerDay" = %s,
                        "lastUpdatedBy" = %s,
                        "lastUpdatedAt" = %s,
                        is_active = true
                    WHERE id = %s
                    """,
                    (
                        mr["vesselName"],
                        json_dumps(mr["route"]),
                        json_dumps(mr["intRoute"]),
                        mr["avgSpeed"],
                        mr["from"],
                        mr.get("fuelConsPerDay"),
                        mr["lastUpdatedBy"],
                        mr["lastUpdatedAt"],
                        master_route_id,
                    ),
                )
            else:
                cur.execute(
                    f"""
                    INSERT INTO {master} (
                        "voyageId", "voyageNumber", "vesselName", route, "intRoute",
                        "avgSpeed", "from", "fuelConsPerDay", is_active,
                        "createdBy", "lastUpdatedBy", "createdAt", "lastUpdatedAt"
                    ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, true, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        mr["voyageId"],
                        mr["voyageNumber"],
                        mr["vesselName"],
                        json_dumps(mr["route"]),
                        json_dumps(mr["intRoute"]),
                        mr["avgSpeed"],
                        mr["from"],
                        mr.get("fuelConsPerDay"),
                        mr["createdBy"],
                        mr["lastUpdatedBy"],
                        mr["createdAt"],
                        mr["lastUpdatedAt"],
                    ),
                )
                master_route_id = int(cur.fetchone()[0])

        conn.commit()

    log(
        tenant.key,
        f"{action} voyage_id={voyage_id} master_route_id={master_route_id} "
        f"voy={record.get('voyage_number')} vessel_id={vessel_id}",
    )
    return {
        "voyage_id": voyage_id,
        "master_route_id": master_route_id,
        "action": action,
        "vessel_id": vessel_id,
    }
