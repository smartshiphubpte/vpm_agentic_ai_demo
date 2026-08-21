"""Report sender microservice — config from process env or VPM_CONFIG_JSON."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import vpm_cfg

ROOT = Path(__file__).resolve().parents[1]

def _parse_csv(raw: str) -> list[str]:
    return [u.strip() for u in (raw or "").replace(";", ",").split(",") if u.strip()]

def _parse_dirs(raw: str, default: Path) -> tuple[Path, ...]:
    parts = _parse_csv(raw)
    if not parts:
        return (default,)
    return tuple(Path(p).expanduser() for p in parts)

_INBOX_DIRS = _parse_dirs(
    vpm_cfg.get("VPM_REPORT_SENDER_INBOX_DIR", ""),
    Path(vpm_cfg.get("VPM_REPORTS_OUT_DIR", "")).expanduser() if vpm_cfg.get("VPM_REPORTS_OUT_DIR", "").strip() else ROOT / "reports",
)

_TEMPLATES_DIR = Path(vpm_cfg.get("VPM_TEMPLATES_DIR", "")).expanduser() if vpm_cfg.get("VPM_TEMPLATES_DIR", "").strip() else ROOT / "templates"
_REGISTRY_RAW = Path(vpm_cfg.get("VPM_REGISTRY_PATH", "")).expanduser() if vpm_cfg.get("VPM_REGISTRY_PATH", "").strip() else ROOT / "data" / "voyage_registry.json"

def _email_templates() -> dict[str, str]:
    return {
        "passage_weather": vpm_cfg.get("VPM_REPORT_EMAIL_TEMPLATE_PASSAGE_WEATHER", "email_passage_weather.txt"),
        "pre_departure": vpm_cfg.get("VPM_REPORT_EMAIL_TEMPLATE_PRE_DEPARTURE", "email_pre_departure.txt"),
        "storm_alert": vpm_cfg.get("VPM_REPORT_EMAIL_TEMPLATE_STORM_ALERT", "email_storm_alert.txt"),
        "end_of_voyage": vpm_cfg.get("VPM_REPORT_EMAIL_TEMPLATE_END_OF_VOYAGE", "email_end_of_voyage.txt"),
        "port_weather": vpm_cfg.get("VPM_REPORT_EMAIL_TEMPLATE_PORT_WEATHER", "email_port_weather.txt"),
        "generic": vpm_cfg.get("VPM_REPORT_EMAIL_TEMPLATE_GENERIC", "email_generic.txt"),
    }

@dataclass(frozen=True)
class Settings:
    # folder watch — comma-separated roots; processed/ after send (per root)
    inbox_dirs: tuple[Path, ...] = _INBOX_DIRS
    inbox_dir: Path = _INBOX_DIRS[0]
    poll_seconds: float = float(vpm_cfg.get("VPM_REPORT_SENDER_POLL_SECONDS", "30"))
    folder_enabled: bool = vpm_cfg.get("VPM_REPORT_SENDER_FOLDER", "true").lower() in (
        "1",
        "true",
        "yes",
    )

    db_urls: tuple[str, ...] = tuple(_parse_csv(vpm_cfg.get("VPM_REPORT_SENDER_DB_URLS", "")))
    db_enabled: bool = vpm_cfg.get("VPM_REPORT_SENDER_DB", "true").lower() in ("1", "true", "yes")
    db_poll_seconds: float = float(vpm_cfg.get("VPM_REPORT_SENDER_DB_POLL_SECONDS", "30"))

    review_email: str = vpm_cfg.get("VPM_REPORT_SENDER_REVIEW_EMAIL", "") or vpm_cfg.get(
        "VPM_REPORT_EMAIL", ""
    )
    report_email_source: str = vpm_cfg.get("VPM_REPORT_EMAIL_SOURCE", "env").strip().lower()

    smtp_host: str = vpm_cfg.get("VPM_SMTP_HOST", "")
    smtp_port: int = int(vpm_cfg.get("VPM_SMTP_PORT", "") or "587")
    smtp_user: str = vpm_cfg.get("VPM_SMTP_USER", "")
    smtp_password: str = vpm_cfg.get("VPM_SMTP_PASSWORD", "")
    smtp_from: str = vpm_cfg.get("VPM_SMTP_FROM", "")

    report_table: str = vpm_cfg.get("VPM_REPORT_SENDER_REPORT_TABLE", "vpm_report")
    voyage_email_table: str = vpm_cfg.get(
        "VPM_REPORT_SENDER_VOYAGE_EMAIL_TABLE", "vpm_voyage_email"
    )
    voyage_schema: str = vpm_cfg.get("VPM_REPORT_SENDER_VOYAGE_SCHEMA", "shipping_db")
    voyage_table: str = vpm_cfg.get("VPM_REPORT_SENDER_VOYAGE_TABLE", "voyages")

    templates_dir: Path = _TEMPLATES_DIR
    registry_path: Path = _REGISTRY_RAW
    email_templates: dict[str, str] = field(default_factory=_email_templates)

settings = Settings()
