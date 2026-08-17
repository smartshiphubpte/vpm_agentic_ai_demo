"""Noon report sources — drop folder, combined Excel (testing), DB placeholder."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from vpm_agents.config import settings
from vpm_agents.tools.inbox_io import archive_inbox_file, list_inbox, parse_noon_report
from vpm_agents.tools.noon_io import noon_row_id, parse_noon_excel
from vpm_agents.tools.voyage_registry import VoyageRegistry

_DROP_PATH = "_drop_path"


class NoonSource(ABC):
    @abstractmethod
    def fetch_new(self, registry: VoyageRegistry) -> list[dict[str, Any]]:
        """Return noon rows not yet processed (oldest first)."""


def parse_noon_drop(path: str | Path) -> list[dict[str, Any]]:
    """One drop file → noon rows (combined Excel workbook or single-row CSV/xlsx)."""
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        rows = parse_noon_excel(path)
        if rows:
            return rows
    one = parse_noon_report(path)
    one["noon_id"] = noon_row_id(one)
    return [one]


def archive_finished_drops(rows: list[dict[str, Any]], registry: VoyageRegistry) -> None:
    seen: set[str] = set()
    for row in rows:
        raw = row.get(_DROP_PATH)
        if not raw or raw in seen:
            continue
        seen.add(raw)
        p = Path(raw)
        if not p.is_file():
            continue
        leftover = [r for r in parse_noon_drop(p) if not registry.is_noon_processed(r["noon_id"])]
        if not leftover:
            archive_inbox_file(p, "processed")


class FolderNoonSource(NoonSource):
    """Drop .xlsx/.csv noon files into VPM_NOON_INBOX_DIR (processed/failed like inbox)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or settings.noon_inbox_dir)

    def fetch_new(self, registry: VoyageRegistry) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in list_inbox(self.path):
            try:
                rows = parse_noon_drop(f)
            except Exception:
                archive_inbox_file(f, "failed")
                continue
            if not rows:
                archive_inbox_file(f, "failed")
                continue
            new_rows = [r for r in rows if not registry.is_noon_processed(r["noon_id"])]
            if not new_rows:
                archive_inbox_file(f, "processed")
                continue
            for r in new_rows:
                r[_DROP_PATH] = str(f)
                out.append(r)
        return out


class ExcelNoonSource(NoonSource):
    """Poll a combined noon Excel on disk (VPM_NOON_EXCEL_PATH)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or settings.noon_excel_path)

    def fetch_new(self, registry: VoyageRegistry) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        inbox = settings.noon_inbox_dir.resolve()
        try:
            self.path.resolve().relative_to(inbox)
            return []  # folder watcher owns files in the noon drop dir
        except ValueError:
            pass
        rows = parse_noon_excel(self.path)
        return [r for r in rows if not registry.is_noon_processed(r["noon_id"])]


class DbNoonSource(NoonSource):
    """Placeholder — wire to voyagepm_be noon table when DB access is available.

    Expected lookup: voyage_number + vessel_name → latest unprocessed noon row.
    """

    def fetch_new(self, registry: VoyageRegistry) -> list[dict[str, Any]]:
        # ponytail: returns [] until DB connection + schema are provided
        _ = registry
        return []


def get_noon_sources() -> list[NoonSource]:
    mode = (settings.noon_source or "excel").lower()
    if mode == "db":
        return [FolderNoonSource(), DbNoonSource()]
    return [FolderNoonSource(), ExcelNoonSource()]


def get_noon_source() -> NoonSource:
    return get_noon_sources()[0]


if __name__ == "__main__":
    sample = Path(__file__).resolve().parents[2] / "samples" / "inbox" / "noon_report.csv"
    rows = parse_noon_drop(sample)
    assert len(rows) == 1 and rows[0]["voyage_number"] == "VYG-2026-001"
    print("noon_source self-check ok")
