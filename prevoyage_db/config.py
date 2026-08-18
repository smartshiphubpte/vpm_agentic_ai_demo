"""Pre-voyage DB writer — env-only config (loads prevoyage_db/.env)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

_SCHEMA_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_TENANT_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_DEFAULT_CLIENT_SCHEMA = os.getenv("PREVOYAGE_DB_CLIENT_SCHEMA", "shipping_db").strip() or "shipping_db"
_DEFAULT_VPM_SCHEMA = os.getenv("PREVOYAGE_DB_VPM_SCHEMA", "shipping_db").strip() or "shipping_db"


def _csv(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").replace(";", ",").split(",") if p.strip()]


@dataclass(frozen=True)
class TenantDbConfig:
    key: str
    vpm_url: str
    client_url: str
    vpm_schema: str = "shipping_db"
    client_schema: str = "shipping_db"


@dataclass(frozen=True)
class Settings:
    poll_seconds: float = float(os.getenv("PREVOYAGE_DB_POLL_SECONDS", "2"))
    actor: str = os.getenv("PREVOYAGE_DB_ACTOR", "agentic-prevoyage-ingest@system")
    schema_version: str = os.getenv("PREVOYAGE_DB_SCHEMA_VERSION", "1")
    sslmode: str = os.getenv("PREVOYAGE_DB_SSLMODE", "prefer")
    dry_run: bool = os.getenv("PREVOYAGE_DB_DRY_RUN", "false").lower() in ("1", "true", "yes")
    jobs_dir: Path = Path(os.getenv("VPM_JOBS_DIR", str(ROOT / "data" / "jobs"))).expanduser()

    vessel_table: str = os.getenv("PREVOYAGE_DB_VESSEL_TABLE", "ship")
    vessel_id_column: str = os.getenv("PREVOYAGE_DB_VESSEL_ID_COLUMN", "id")
    vessel_name_column: str = os.getenv("PREVOYAGE_DB_VESSEL_NAME_COLUMN", "name")
    vessel_mapping_column: str = os.getenv("PREVOYAGE_DB_VESSEL_MAPPING_COLUMN", "mappingname")
    vessel_imo_column: str = os.getenv("PREVOYAGE_DB_VESSEL_IMO_COLUMN", "imo")

    voyages_table: str = os.getenv("PREVOYAGE_DB_VOYAGES_TABLE", "voyages")
    master_routes_table: str = os.getenv("PREVOYAGE_DB_MASTER_ROUTES_TABLE", "master_routes")
    noon_table: str = os.getenv("PREVOYAGE_DB_NOON_TABLE", "std_enoonreporttable")
    noon_report_types: tuple[str, ...] = tuple(
        _csv(os.getenv(
            "PREVOYAGE_DB_NOON_REPORT_TYPES",
            "Departure Report,Noon Report,Arrival Report",
        ))
    )

    default_route_type: str = os.getenv("PREVOYAGE_DB_ROUTE_TYPE", "captainsRoute")


def _tenant_keys() -> list[str]:
    return _csv(os.getenv("PREVOYAGE_DB_TENANTS", ""))


def load_tenants() -> dict[str, TenantDbConfig]:
    out: dict[str, TenantDbConfig] = {}
    for key in _tenant_keys():
        if not _TENANT_RE.match(key):
            continue
        prefix = f"PREVOYAGE_DB_{key.upper()}_"
        vpm = os.getenv(f"{prefix}VPM_URL", "").strip()
        client = os.getenv(f"{prefix}CLIENT_URL", "").strip()
        if not vpm or not client:
            continue
        vpm_schema = os.getenv(f"{prefix}VPM_SCHEMA", _DEFAULT_VPM_SCHEMA).strip() or _DEFAULT_VPM_SCHEMA
        client_schema = os.getenv(f"{prefix}CLIENT_SCHEMA", _DEFAULT_CLIENT_SCHEMA).strip() or _DEFAULT_CLIENT_SCHEMA
        for label, schema in (("VPM", vpm_schema), ("client", client_schema)):
            if not _SCHEMA_RE.match(schema):
                raise ValueError(f"invalid {label} schema for tenant {key}: {schema!r}")
        out[key.lower()] = TenantDbConfig(
            key=key.lower(),
            vpm_url=vpm,
            client_url=client,
            vpm_schema=vpm_schema,
            client_schema=client_schema,
        )
    return out


settings = Settings()
