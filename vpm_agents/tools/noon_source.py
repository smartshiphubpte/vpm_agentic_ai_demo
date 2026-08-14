"""Noon report sources — Excel (testing) and DB placeholder (production later)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from vpm_agents.config import settings
from vpm_agents.tools.noon_io import parse_noon_excel
from vpm_agents.tools.voyage_registry import VoyageRegistry


class NoonSource(ABC):
    @abstractmethod
    def fetch_new(self, registry: VoyageRegistry) -> list[dict[str, Any]]:
        """Return noon rows not yet processed (oldest first)."""


class ExcelNoonSource(NoonSource):
    """Poll a combined noon Excel on disk (VPM_NOON_EXCEL_PATH)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or settings.noon_excel_path)

    def fetch_new(self, registry: VoyageRegistry) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        rows = parse_noon_excel(self.path)
        new_rows = [r for r in rows if not registry.is_noon_processed(r["noon_id"])]
        return new_rows


class DbNoonSource(NoonSource):
    """Placeholder — wire to voyagepm_be noon table when DB access is available.

    Expected lookup: voyage_number + vessel_name → latest unprocessed noon row.
    """

    def fetch_new(self, registry: VoyageRegistry) -> list[dict[str, Any]]:
        # ponytail: returns [] until DB connection + schema are provided
        _ = registry
        return []


def get_noon_source() -> NoonSource:
    mode = (settings.noon_source or "excel").lower()
    if mode == "db":
        return DbNoonSource()
    return ExcelNoonSource()
