"""Read noon / departure / arrival rows from the client noon table."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from prevoyage_db.config import TenantDbConfig, settings
from prevoyage_db.connect import connect

_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _table(schema: str, table: str) -> str:
    if not _IDENT.match(schema) or not _IDENT.match(table):
        raise ValueError(f"unsafe schema/table: {schema}.{table}")
    return f'"{schema}"."{table}"'


def voyage_sql_keys(voyage_number: str) -> list[str]:
    """DB voyage column variants for one registry key (spaces already gone)."""
    v = re.sub(r"\s+", "", str(voyage_number or "").strip().upper())
    if not v:
        return []
    bare = v[1:] if v.startswith("V") else v
    tagged = f"V{bare}"
    return list(dict.fromkeys([v, tagged, bare]))


def fetch_noon_rows(
    tenant: TenantDbConfig,
    *,
    voyage_number: str,
    vessel_id: str | None = None,
    report_types: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Oldest-first noon rows for one voyage. Empty if none match."""
    keys = voyage_sql_keys(voyage_number)
    types = list(report_types or settings.noon_report_types)
    if not keys or not types:
        return []

    tbl = _table(tenant.client_schema, settings.noon_table)
    sql = f"""
        SELECT id, report_id, voyage, vesselid, reporttype, report_date_time_utc, noonreportdata
        FROM {tbl}
        WHERE reporttype = ANY(%s)
          AND (
            replace(upper(coalesce(voyage, '')), ' ', '') = ANY(%s)
            OR replace(upper(coalesce(noonreportdata->>'Voyage_Number', '')), ' ', '') = ANY(%s)
          )
    """
    params: list[Any] = [types, keys, keys]
    if vessel_id and str(vessel_id).strip().isdigit():
        sql += " AND vesselid = %s"
        params.append(int(vessel_id))
    sql += " ORDER BY report_date_time_utc ASC NULLS LAST, id ASC"

    with connect(tenant.client_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def utc_iso(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    s = str(raw).strip()
    return s or None


if __name__ == "__main__":
    assert voyage_sql_keys("V2611L") == ["V2611L", "2611L"]
    assert voyage_sql_keys("2611 L") == ["2611L", "V2611L"]
    assert voyage_sql_keys("") == []
    print("prevoyage_db.noon self-check ok")
