"""Runtime configuration from env."""

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


# Center buffer: prefer VPM_STORM_CENTER_BUFFER_NM, fall back to legacy threshold key.
_STORM_CENTER_NM = float(
    os.getenv("VPM_STORM_CENTER_BUFFER_NM")
    or os.getenv("VPM_STORM_ROUTE_THRESHOLD_NM")
    or "500"
)
_STORM_EDGE_NM = float(os.getenv("VPM_STORM_EDGE_BUFFER_NM", "300"))


def _looks_like_gemini_key(key: str) -> bool:
    """Google AI Studio / Gemini keys — not valid on api.openai.com."""
    k = (key or "").strip()
    return k.startswith(("AIza", "AQ."))


def _looks_like_openai_model(name: str) -> bool:
    m = (name or "").strip().lower()
    return m.startswith(("gpt-", "o1", "o2", "o3", "o4", "chatgpt"))


def _gemini_model_from_env() -> str:
    """GEMINI_MODEL > VPM_GEMINI_MODEL > VPM_LLM_MODEL (if not an OpenAI name)."""
    for key in ("GEMINI_MODEL", "VPM_GEMINI_MODEL", "VPM_LLM_MODEL"):
        m = os.getenv(key, "").strip()
        if m and not _looks_like_openai_model(m):
            return m
    return "gemini-3.6-flash"


def _openai_model_from_env() -> str:
    """OPENAI_MODEL > VPM_LLM_MODEL (OpenAI-style only) > default."""
    explicit = os.getenv("OPENAI_MODEL", "").strip()
    if explicit:
        return explicit
    vpm = os.getenv("VPM_LLM_MODEL", "").strip()
    if vpm and _looks_like_openai_model(vpm):
        return vpm
    if vpm:
        return vpm  # legacy: non-gpt VPM_LLM_MODEL when provider=openai
    return "gpt-4o-mini"


@dataclass(frozen=True)
class Settings:
    mode: str = os.getenv("VPM_MODE", "mock")  # mock | live
    base_url: str = os.getenv("VPM_BASE_URL", "http://localhost:3000/be_voyagepm")
    email: str = os.getenv("VPM_EMAIL", "ops@smartshiphub.com")
    password: str = os.getenv("VPM_PASSWORD", "demo")
    company: str = os.getenv("VPM_COMPANY", "orion")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "") or os.getenv("VPM_LLM_API_KEY", "")
    openai_model: str = _openai_model_from_env()
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "") or os.getenv("VPM_GEMINI_API_KEY", "")
    gemini_model: str = _gemini_model_from_env()
    gemini_base_url: str = (
        os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
        .rstrip("/")
    )
    # openai | openai_compatible | gemini
    llm_provider: str = os.getenv("VPM_LLM_PROVIDER", "openai")
    llm_base_url: str = os.getenv("VPM_LLM_BASE_URL", "").rstrip("/")
    # Live route optimize without voyagepm_be: conventional | llm | backend
    route_opt_method: str = os.getenv("VPM_ROUTE_OPT_METHOD", "conventional")
    # conventional only: astar | dijkstra
    route_opt_algo: str = os.getenv("VPM_ROUTE_OPT_ALGO", "astar")
    # Hard rule: route points/legs must stay this many NM clear of landmasses
    land_clearance_nm: float = float(os.getenv("VPM_LAND_CLEARANCE_NM", "25"))
    data_dir: Path = ROOT / "data"

    # Continuous ops folders — set absolute paths in .env
    inbox_dir: Path = _path("VPM_INBOX_DIR", ROOT / "inbox")
    storm_out_dir: Path = _path("VPM_STORM_OUT_DIR", ROOT / "storm_alerts")
    reports_out_dir: Path = _path("VPM_REPORTS_OUT_DIR", ROOT / "reports")
    templates_dir: Path = _path("VPM_TEMPLATES_DIR", ROOT / "templates")
    registry_path: Path = _path("VPM_REGISTRY_PATH", ROOT / "data" / "voyage_registry.json")

    inbox_poll_seconds: float = float(os.getenv("VPM_INBOX_POLL_SECONDS", "30"))
    storm_interval_hours: float = float(os.getenv("VPM_STORM_INTERVAL_HOURS", "6"))
    noon_horizon_hours: float = float(os.getenv("VPM_NOON_HORIZON_HOURS", str(7 * 24)))
    waypoint_interval_hours: float = float(os.getenv("VPM_WAYPOINT_INTERVAL_HOURS", "6"))
    weather_report_delay_minutes: float = float(
        os.getenv("VPM_WEATHER_REPORT_DELAY_MINUTES", "0")
    )

    # Noon report polling (Excel testing or DB later)
    noon_source: str = os.getenv("VPM_NOON_SOURCE", "excel")  # excel | db
    noon_excel_path: Path = _path("VPM_NOON_EXCEL_PATH", ROOT / "samples" / "noon_reports.xlsx")
    noon_poll_seconds: float = float(os.getenv("VPM_NOON_POLL_SECONDS", "120"))
    noon_batch_size: int = int(os.getenv("VPM_NOON_BATCH_SIZE", "1"))
    # Storm avoidance: route points must stay outside BOTH buffers
    # (not within center of storm, and not within edge_buffer of storm edge).
    storm_center_buffer_nm: float = _STORM_CENTER_NM
    storm_edge_buffer_nm: float = _STORM_EDGE_NM
    storm_route_threshold_nm: float = _STORM_CENTER_NM  # legacy alias
    # live = NOAA NHC JSON + JTWC warning texts; backend = voyagepm map-layer; mock = in-memory
    storm_source: str = os.getenv("VPM_STORM_SOURCE", "live")
    # live = Open-Meteo forecast+marine; backend = voyagepm_be /weather/route
    weather_source: str = os.getenv("VPM_WEATHER_SOURCE", "live")

    # Tagged daemon flow (see agents/specs/DaemonFlows.md)
    daemon_flow: str = os.getenv("VPM_DAEMON_FLOW", "pre_voyage_weather")

    # Weather reports — defaults to same tree as VPM_REPORTS_OUT_DIR/{voyage}/
    weather_out_dir: Path = _path(
        "VPM_WEATHER_OUT_DIR",
        _path("VPM_REPORTS_OUT_DIR", ROOT / "reports"),
    )
    weather_report_on_noon: bool = os.getenv("VPM_WEATHER_REPORT_ON_NOON", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    weather_report_on_prevoyage: bool = os.getenv(
        "VPM_WEATHER_REPORT_ON_PREVOYAGE", "true"
    ).lower() in ("1", "true", "yes")
    weather_wind_threshold_kn: float = float(os.getenv("VPM_WEATHER_WIND_THRESHOLD_KN", "35"))
    weather_wave_threshold_m: float = float(os.getenv("VPM_WEATHER_WAVE_THRESHOLD_M", "4.0"))
    weather_swell_threshold_m: float = float(os.getenv("VPM_WEATHER_SWELL_THRESHOLD_M", "3.0"))

    @property
    def effective_llm_provider(self) -> str:
        """Resolved provider — auto-routes Gemini-shaped keys off OpenAI."""
        p = (self.llm_provider or "openai").strip().lower()
        if p == "gemini":
            return "gemini"
        if self.gemini_api_key and not self.openai_api_key:
            return "gemini"
        active = self.openai_api_key or self.gemini_api_key
        if p == "openai" and active and _looks_like_gemini_key(active):
            return "gemini"
        return p

    @property
    def llm_api_key(self) -> str:
        if self.effective_llm_provider == "gemini":
            return self.gemini_api_key or self.openai_api_key
        return self.openai_api_key or self.gemini_api_key

    @property
    def llm_model(self) -> str:
        if self.effective_llm_provider == "gemini":
            return self.gemini_model
        return self.openai_model

    @property
    def effective_llm_base_url(self) -> str:
        if self.llm_base_url:
            return self.llm_base_url
        if self.effective_llm_provider == "gemini":
            return self.gemini_base_url
        return ""

    @property
    def use_llm(self) -> bool:
        return bool(self.llm_api_key)


settings = Settings()
