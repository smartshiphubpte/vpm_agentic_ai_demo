"""Shared session state — working memory across agents for one orchestration run."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass
class SessionState:
    """Tenant-scoped working memory for a multi-agent run."""

    company: str = ""
    user_email: str = ""
    role: str = ""
    authenticated: bool = False

    vessel_id: str | None = None
    vessel_name: str | None = None
    voyage_id: str | None = None
    voyage_number: str | None = None

    master_route: list[dict[str, Any]] = field(default_factory=list)
    suggested_route: list[dict[str, Any]] = field(default_factory=list)
    optimized_routes: dict[str, Any] = field(default_factory=dict)

    weather_summary: dict[str, Any] = field(default_factory=dict)
    storms: list[dict[str, Any]] = field(default_factory=list)
    hard_regions: list[dict[str, Any]] = field(default_factory=list)

    alerts: list[dict[str, Any]] = field(default_factory=list)
    advisories: list[dict[str, Any]] = field(default_factory=list)

    noon_reports: list[dict[str, Any]] = field(default_factory=list)
    cii: dict[str, Any] = field(default_factory=dict)
    eov: dict[str, Any] = field(default_factory=dict)
    performance: dict[str, Any] = field(default_factory=dict)

    artifacts: dict[str, Any] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    phase: str = "new"

    def note(self, agent: str, msg: str) -> None:
        self.log.append(f"[{agent}] {msg}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionState:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
