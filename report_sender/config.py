"""Report sender microservice — env-only config (no vpm_agents import)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _parse_csv(raw: str) -> list[str]:
    return [u.strip() for u in (raw or "").replace(";", ",").split(",") if u.strip()]


def _parse_dirs(raw: str, default: Path) -> tuple[Path, ...]:
    parts = _parse_csv(raw)
    if not parts:
        return (default,)
    return tuple(Path(p).expanduser() for p in parts)


_INBOX_DIRS = _parse_dirs(
    os.getenv("VPM_REPORT_SENDER_INBOX_DIR", ""),
    Path(os.getenv("VPM_REPORTS_OUT_DIR", "")).expanduser() if os.getenv("VPM_REPORTS_OUT_DIR", "").strip() else ROOT / "reports",
)


@dataclass(frozen=True)
class Settings:
    # folder watch — comma-separated roots; processed/ after send (per root)
    inbox_dirs: tuple[Path, ...] = _INBOX_DIRS
    inbox_dir: Path = _INBOX_DIRS[0]
    poll_seconds: float = float(os.getenv("VPM_REPORT_SENDER_POLL_SECONDS", "30"))
    folder_enabled: bool = os.getenv("VPM_REPORT_SENDER_FOLDER", "true").lower() in (
        "1",
        "true",
        "yes",
    )

    db_urls: tuple[str, ...] = tuple(_parse_csv(os.getenv("VPM_REPORT_SENDER_DB_URLS", "")))
    db_enabled: bool = os.getenv("VPM_REPORT_SENDER_DB", "true").lower() in ("1", "true", "yes")
    db_poll_seconds: float = float(os.getenv("VPM_REPORT_SENDER_DB_POLL_SECONDS", "30"))

    review_email: str = os.getenv("VPM_REPORT_SENDER_REVIEW_EMAIL", "") or os.getenv(
        "VPM_REPORT_EMAIL", ""
    )
    report_email_source: str = os.getenv("VPM_REPORT_EMAIL_SOURCE", "env").strip().lower()

    smtp_host: str = os.getenv("VPM_SMTP_HOST", "")
    smtp_port: int = int(os.getenv("VPM_SMTP_PORT") or "587")
    smtp_user: str = os.getenv("VPM_SMTP_USER", "")
    smtp_password: str = os.getenv("VPM_SMTP_PASSWORD", "")
    smtp_from: str = os.getenv("VPM_SMTP_FROM", "")

    report_table: str = os.getenv("VPM_REPORT_SENDER_REPORT_TABLE", "vpm_report")
    voyage_email_table: str = os.getenv(
        "VPM_REPORT_SENDER_VOYAGE_EMAIL_TABLE", "vpm_voyage_email"
    )


settings = Settings()
