"""Transactional upsert into VPM voyages, master_routes, and suggested_routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from prevoyage_db.config import TenantDbConfig, settings
from prevoyage_db.connect import connect
from prevoyage_db.log import log
from prevoyage_db.mapper import (
    build_master_route_row,
    build_voyage_row,
    build_vo_comparison_metadata,
    json_dumps,
    plan_to_int_route,
    suggested_route_geojson,
    waypoints_to_geojson,
    _parse_ts,
)
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


def _lookup_voyage_id(cur, voyages: str, voyage_number: str, vessel_id: str | None) -> int | None:
    if vessel_id:
        cur.execute(
            f"""
            SELECT id FROM {voyages}
            WHERE "voyageNumber" = %s AND "vesselId" = %s
            ORDER BY id DESC LIMIT 1
            """,
            (voyage_number, str(vessel_id)),
        )
        row = cur.fetchone()
        if row:
            return int(row[0])
    cur.execute(
        f"""
        SELECT id FROM {voyages}
        WHERE "voyageNumber" = %s
        ORDER BY id DESC LIMIT 1
        """,
        (voyage_number,),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def _copy_commercial(cur, table: str, voyage_id: int) -> dict[str, Any]:
    cur.execute(
        f"""
        SELECT "foCons", "mgoCons", "foPrice", "mgoPrice", "hireRate"
        FROM {table}
        WHERE "voyageId" = %s
        ORDER BY is_active DESC, id DESC
        LIMIT 1
        """,
        (voyage_id,),
    )
    row = cur.fetchone()
    if not row:
        return {}
    keys = ("foCons", "mgoCons", "foPrice", "mgoPrice", "hireRate")
    return {k: v for k, v in zip(keys, row) if v is not None}


def ingest_suggested_routes(
    tenant: TenantDbConfig, record: dict[str, Any], *, dry_run: bool | None = None
) -> dict[str, Any]:
    """Publish 4 objective routes into suggested_routes (one active = suggested)."""
    routes = record.get("routes") or []
    if not routes:
        raise ValueError("suggested_routes job has no routes")
    voy_no = str(record.get("voyage_number") or "").strip()
    if not voy_no:
        raise ValueError("voyage_number required")

    # Voyage number is enough; skip client-DB vessel lookup (second TCP that can timeout).
    vessel_id = str(record.get("vessel_id") or "") or None
    speed = float(record.get("cp_speed_kn") or 0)
    cons = record.get("cp_consumption_mt_day")
    etd = record.get("etd")
    suggested_id = str(record.get("suggested_id") or "")
    ids_present = [str(a.get("id") or "") for a in routes]
    if suggested_id not in ids_present:
        suggested_id = ids_present[0]
    actor = settings.actor
    now = datetime.now(timezone.utc)

    if dry_run if dry_run is not None else settings.dry_run:
        log(tenant.key, f"DRY-RUN suggested_routes voy={voy_no} n={len(routes)}")
        return {"voyage_id": None, "action": "dry_run", "n": len(routes)}

    voyages = _table(tenant.vpm_schema, settings.voyages_table)
    master = _table(tenant.vpm_schema, settings.master_routes_table)
    suggested = _table(tenant.vpm_schema, settings.suggested_routes_table)

    ids: list[int] = []
    with connect(tenant.vpm_url) as conn:
        with conn.cursor() as cur:
            voyage_id = _lookup_voyage_id(cur, voyages, voy_no, vessel_id)
            if voyage_id is None:
                raise ValueError(f"voyage {voy_no} not in {voyages} — ingest pre-voyage first")
            commercial = _copy_commercial(cur, master, voyage_id)
            if len(commercial) < 5:
                commercial = {**_copy_commercial(cur, suggested, voyage_id), **commercial}

            cur.execute(
                f"""
                SELECT vessel FROM {voyages} WHERE id = %s
                """,
                (voyage_id,),
            )
            vrow = cur.fetchone()
            vessel_name = (
                str(record.get("vessel_name") or "").strip()
                or (str(vrow[0]).strip() if vrow and vrow[0] else "")
            )

            cur.execute(
                f"""
                SELECT etd FROM {voyages} WHERE id = %s
                """,
                (voyage_id,),
            )
            erow = cur.fetchone()
            from_ts = _parse_ts(etd) or (erow[0] if erow else None) or now

            cur.execute(
                f"""
                UPDATE {suggested}
                SET is_active = false, "lastUpdatedAt" = %s, "lastUpdatedBy" = %s
                WHERE "voyageId" = %s AND "voyageNumber" = %s AND is_active = true
                """,
                (now, actor, voyage_id, voy_no),
            )

            # BE writes explicit MAX(id)+1; the serial can lag and collide (id 619).
            cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {suggested}")
            next_id = int(cur.fetchone()[0]) + 1

            for alt in routes:
                oid = str(alt.get("id") or "")
                wps = alt.get("waypoints") or []
                voy = alt.get("voyage") or {}
                meta = build_vo_comparison_metadata(
                    voy, alt.get("weather_points") or [], etd=from_ts
                )
                route_gj = suggested_route_geojson(wps, objective=oid, metadata=meta)
                intro = plan_to_int_route(
                    alt.get("six_hour_plan") or [],
                    speed_kn=float(voy.get("speedKn") or speed or 12),
                )
                is_active = oid == suggested_id
                cur.execute(
                    f"""
                    INSERT INTO {suggested} (
                        id, route, "intRoute",
                        "foCons", "mgoCons", "foPrice", "mgoPrice", "hireRate",
                        "avgSpeed", "voyageId", "voyageNumber", "vesselName",
                        "from", "createdBy", "lastUpdatedBy", "createdAt", "lastUpdatedAt",
                        "fuelConsPerDay", is_active
                    ) VALUES (
                        %s, %s::jsonb, %s::jsonb,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s
                    )
                    """,
                    (
                        next_id,
                        json_dumps(route_gj),
                        json_dumps(intro),
                        commercial.get("foCons"),
                        commercial.get("mgoCons"),
                        commercial.get("foPrice"),
                        commercial.get("mgoPrice"),
                        commercial.get("hireRate"),
                        speed or voy.get("speedKn"),
                        voyage_id,
                        voy_no,
                        vessel_name,
                        from_ts,
                        actor,
                        actor,
                        now,
                        now,
                        str(cons) if cons is not None else "",
                        is_active,
                    ),
                )
                ids.append(next_id)
                next_id += 1

            seq = f"{tenant.vpm_schema}.suggested_routes_id_seq"
            cur.execute(
                f"SELECT setval(%s, (SELECT COALESCE(MAX(id), 1) FROM {suggested}), true)",
                (seq,),
            )
        conn.commit()

    log(
        tenant.key,
        f"suggested_routes voy={voy_no} voyage_id={voyage_id} "
        f"n={len(ids)} suggested={suggested_id} ids={ids} "
        f"commercial={'copied' if commercial else 'missing'}",
    )
    return {
        "voyage_id": voyage_id,
        "action": "insert",
        "ids": ids,
        "suggested_id": suggested_id,
        "commercial_copied": sorted(commercial),
    }
