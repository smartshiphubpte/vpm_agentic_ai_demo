"""Resolve vessel id from client DB (ship name / mapping name / IMO)."""

from __future__ import annotations

import re
from typing import Any

from prevoyage_db.config import TenantDbConfig, settings
from prevoyage_db.connect import connect

_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _qident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return f'"{name}"'


def _qualify_table(schema: str, table: str) -> str:
    if not _IDENT.match(schema) or not _IDENT.match(table):
        raise ValueError(f"unsafe schema/table: {schema}.{table}")
    return f'"{schema}"."{table}"'


def lookup_vessel_id(tenant: TenantDbConfig, record: dict[str, Any]) -> str:
    """Return client ship.id for this pre-voyage row."""
    raw_id = str(record.get("vessel_id") or "").strip()
    vessel_name = str(record.get("vessel_name") or "").strip()

    tbl = _qualify_table(tenant.client_schema, settings.vessel_table)
    id_col = _qident(settings.vessel_id_column)
    name_col = _qident(settings.vessel_name_column)
    map_col = _qident(settings.vessel_mapping_column)
    imo_col = _qident(settings.vessel_imo_column)

    with connect(tenant.client_url) as conn:
        with conn.cursor() as cur:
            if raw_id.isdigit():
                cur.execute(
                    f"SELECT {id_col}::text FROM {tbl} WHERE {id_col}::text = %s LIMIT 1",
                    (raw_id,),
                )
                row = cur.fetchone()
                if row:
                    return str(row[0])

            if raw_id:
                cur.execute(
                    f"SELECT {id_col}::text FROM {tbl} WHERE {imo_col}::text = %s LIMIT 1",
                    (raw_id,),
                )
                row = cur.fetchone()
                if row:
                    return str(row[0])

            if vessel_name:
                cur.execute(
                    f"""
                    SELECT {id_col}::text FROM {tbl}
                    WHERE lower(trim({name_col}::text)) = lower(%s)
                       OR lower(trim({map_col}::text)) = lower(%s)
                    ORDER BY {id_col} ASC
                    LIMIT 1
                    """,
                    (vessel_name, vessel_name),
                )
                row = cur.fetchone()
                if row:
                    return str(row[0])

            if raw_id:
                cur.execute(
                    f"""
                    SELECT {id_col}::text FROM {tbl}
                    WHERE lower(trim({name_col}::text)) = lower(%s)
                       OR lower(trim({map_col}::text)) = lower(%s)
                    ORDER BY {id_col} ASC
                    LIMIT 1
                    """,
                    (raw_id, raw_id),
                )
                row = cur.fetchone()
                if row:
                    return str(row[0])

    hint = vessel_name or raw_id or "?"
    raise LookupError(
        f"tenant={tenant.key} vessel not found in {tenant.client_schema}.{settings.vessel_table}: {hint}"
    )
