"""Port weather microservice — config from process env or VPM_CONFIG_JSON."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import vpm_cfg

ROOT = Path(__file__).resolve().parents[1]

def _path(env_key: str, default: Path) -> Path:
    raw = vpm_cfg.get(env_key, "").strip()
    return Path(raw).expanduser() if raw else default

def _bool(env_key: str, default: str = "true") -> bool:
    return vpm_cfg.get(env_key, default).lower() in ("1", "true", "yes")

@dataclass(frozen=True)
class Settings:
    out_dir: Path = _path("VPM_PORT_WEATHER_OUT_DIR", ROOT / "port_weather_out")
    # How often to send a fresh report while the vessel stays in port
    interval_hours: float = float(vpm_cfg.get("VPM_PORT_WEATHER_INTERVAL_HOURS", "24"))
    # How often to scan the registry for arrival / departure (not the send cadence)
    poll_seconds: float = float(vpm_cfg.get("VPM_PORT_WEATHER_POLL_SECONDS", "30"))
    # Forecast window written into each report
    horizon_hours: int = int(float(vpm_cfg.get("VPM_PORT_WEATHER_HORIZON_HOURS", "24")))
    template: str = vpm_cfg.get("VPM_PORT_WEATHER_TEMPLATE", "port_weather_report.txt").strip()
    enabled: bool = _bool("VPM_PORT_WEATHER", "true")
    state_path: Path = _path(
        "VPM_PORT_WEATHER_STATE_PATH",
        _path("VPM_PORT_WEATHER_OUT_DIR", ROOT / "port_weather_out") / "port_weather_state.json",
    )

settings = Settings()
