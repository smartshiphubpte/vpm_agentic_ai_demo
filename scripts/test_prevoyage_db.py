#!/usr/bin/env python3
"""Test prevoyage_db connectivity, vessel lookup, and optional dry-run ingest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prevoyage_db.config import load_tenants, settings
from prevoyage_db.connect import connect
from prevoyage_db.vessel_lookup import lookup_vessel_id
from prevoyage_db.writer import ingest_prevoyage


def cmd_check(tenant_key: str) -> int:
    tenants = load_tenants()
    t = tenants.get(tenant_key.lower())
    if not t:
        print(f"unknown tenant {tenant_key!r}; configured: {', '.join(tenants)}", file=sys.stderr)
        return 2
    print(f"tenant={t.key} vpm_schema={t.vpm_schema} client_schema={t.client_schema}")
    with connect(t.client_url) as conn:
        conn.execute("SET default_transaction_read_only = ON")
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT COUNT(*) FROM "{t.client_schema}"."{settings.vessel_table}"'
            )
            print(f"  client {settings.vessel_table}: {cur.fetchone()[0]} rows")
    with connect(t.vpm_url) as conn:
        conn.execute("SET default_transaction_read_only = ON")
        with conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{t.vpm_schema}"."{settings.voyages_table}"')
            voy = cur.fetchone()[0]
            cur.execute(f'SELECT COUNT(*) FROM "{t.vpm_schema}"."{settings.master_routes_table}"')
            mr = cur.fetchone()[0]
            print(f"  vpm {settings.voyages_table}: {voy} rows")
            print(f"  vpm {settings.master_routes_table}: {mr} rows")
    print("check ok")
    return 0


def cmd_lookup(tenant_key: str, name: str) -> int:
    tenants = load_tenants()
    t = tenants[tenant_key.lower()]
    vid = lookup_vessel_id(t, {"vessel_name": name, "vessel_id": ""})
    print(f"vessel_name={name!r} → ship.id={vid}")
    return 0


def cmd_dry_run(tenant_key: str, path: Path) -> int:
    from vpm_agents.tools.inbox_io import parse_pre_voyage
    from vpm_agents.tools.voyage_registry import normalize_voyage_number

    tenants = load_tenants()
    t = tenants[tenant_key.lower()]
    data = parse_pre_voyage(path)
    record = {
        **data,
        "voyage_number": normalize_voyage_number(data["voyage_number"]),
    }
    result = ingest_prevoyage(t, record, dry_run=True)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_enqueue(tenant_key: str, path: Path) -> int:
    from vpm_agents.tools.inbox_io import parse_pre_voyage
    from vpm_agents.tools import job_bus
    from vpm_agents.tools.voyage_registry import normalize_voyage_number

    data = parse_pre_voyage(path)
    voy = normalize_voyage_number(data["voyage_number"])
    key = f"prevoyage_db:{tenant_key.lower()}:{voy}"
    ok = job_bus.enqueue(
        key,
        {
            "kind": "prevoyage_db",
            "tenant": tenant_key.lower(),
            "voyage_number": voy,
            "record": {
                **data,
                "voyage_number": voy,
            },
        },
        root=settings.jobs_dir,
    )
    print(f"enqueue {key}: {'ok' if ok else 'already pending'}")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Test prevoyage_db microservice")
    p.add_argument("--tenant", default="orion", help="Tenant key (PREVOYAGE_DB_TENANTS)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="Read-only DB connectivity + row counts")
    lp = sub.add_parser("lookup", help="Resolve vessel name → ship.id")
    lp.add_argument("vessel_name", help='e.g. "FUJIAN EXPRESS"')
    dr = sub.add_parser("dry-run", help="Parse Excel/CSV and dry-run ingest (no write)")
    dr.add_argument("path", type=Path, help="Pre-voyage file path")
    eq = sub.add_parser("enqueue", help="Parse file and enqueue prevoyage_db job")
    eq.add_argument("path", type=Path)

    args = p.parse_args()
    try:
        if args.cmd == "check":
            return cmd_check(args.tenant)
        if args.cmd == "lookup":
            return cmd_lookup(args.tenant, args.vessel_name)
        if args.cmd == "dry-run":
            return cmd_dry_run(args.tenant, args.path)
        if args.cmd == "enqueue":
            return cmd_enqueue(args.tenant, args.path)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
