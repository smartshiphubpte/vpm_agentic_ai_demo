"""Port weather microservice — env-only knobs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _path(env_key: str, default: Path) -> Path:
    raw = os.getenv(env_key, "").strip()
    return Path(raw).expanduser() if raw else default


def _bool(env_key: str, default: str = "true") -> bool:
    return os.getenv(env_key, default).lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    out_dir: Path = _path("VPM_PORT_WEATHER_OUT_DIR", ROOT / "port_weather_out")
    # How often to send a fresh report while the vessel stays in port
    interval_hours: float = float(os.getenv("VPM_PORT_WEATHER_INTERVAL_HOURS", "24"))
    # How often to scan the registry for arrival / departure (not the send cadence)
    poll_seconds: float = float(os.getenv("VPM_PORT_WEATHER_POLL_SECONDS", "30"))
    # Forecast window written into each report
    horizon_hours: int = int(float(os.getenv("VPM_PORT_WEATHER_HORIZON_HOURS", "24")))
    template: str = os.getenv("VPM_PORT_WEATHER_TEMPLATE", "port_weather_report.txt").strip()
    enabled: bool = _bool("VPM_PORT_WEATHER", "true")
    state_path: Path = _path(
        "VPM_PORT_WEATHER_STATE_PATH",
        _path("VPM_PORT_WEATHER_OUT_DIR", ROOT / "port_weather_out") / "port_weather_state.json",
    )


settings = Settings()
