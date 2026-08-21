"""Noon report sources — drop folder, combined Excel (testing), client DB."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from vpm_agents.config import settings
from vpm_agents.tools.folder_layout import SENT
from vpm_agents.tools.noon_io import _float_or_none, noon_row_id, parse_dms_coordinate, parse_noon_excel
from vpm_agents.tools.voyage_registry import VoyageRegistry, compact_voyage_number, voyage_is_closed

_DROP_PATH = "_drop_path"


class NoonSource(ABC):
    @abstractmethod
    def fetch_new(self, registry: VoyageRegistry) -> list[dict[str, Any]]:
        """Return noon rows not yet processed (oldest first)."""


def parse_noon_drop(path: str | Path) -> list[dict[str, Any]]:
    """One drop file → noon rows (combined Excel workbook or single-row CSV/xlsx)."""
    from inbox_agent.parse import parse_noon_report

    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        rows = parse_noon_excel(path)
        if rows:
            return rows
    one = parse_noon_report(path)
    one["noon_id"] = noon_row_id(one)
    return [one]


def archive_finished_drops(rows: list[dict[str, Any]], registry: VoyageRegistry) -> None:
    from inbox_agent.parse import archive_inbox_file

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
            archive_inbox_file(p, SENT)


class FolderNoonSource(NoonSource):
    """Drop .xlsx/.csv noon files into VPM_NOON_INBOX_DIR/incoming/ (sent/ after pickup)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or settings.noon_inbox_dir)

    def fetch_new(self, registry: VoyageRegistry) -> list[dict[str, Any]]:
        from inbox_agent.parse import archive_inbox_file, list_inbox

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
                archive_inbox_file(f)
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


def row_from_db(raw: dict[str, Any], *, registry_voyage: str) -> dict[str, Any] | None:
    """Map a std_enoonreporttable row to the NoonOps payload."""
    nd = raw.get("noonreportdata") or {}
    if not isinstance(nd, dict):
        return None
    lat_raw = nd.get("Latitude")
    lon_raw = nd.get("Longitude")
    if lat_raw is None or lon_raw is None:
        return None
    try:
        lat = parse_dms_coordinate(lat_raw)
        lon = parse_dms_coordinate(lon_raw)
    except ValueError:
        return None
    from prevoyage_db.noon import utc_iso

    report_type = str(raw.get("reporttype") or nd.get("Report_Type") or "").strip()
    observed = utc_iso(raw.get("report_date_time_utc")) or utc_iso(nd.get("Report_Date_Time"))
    record = {
        "voyage_number": compact_voyage_number(registry_voyage),
        "vessel_name": str(nd.get("Vessel_Name") or "").strip(),
        "lat": lat,
        "lon": lon,
        "observed_at": observed,
        "report_type": report_type,
        "avg_speed_kn": _float_or_none(nd.get("Avg_Speed") or nd.get("Log_Speed")),
        "report_id": str(raw.get("report_id") or raw.get("id") or "").strip() or None,
        "source": "db",
        "eov_row": {
            "reporttype": report_type,
            "utcTime": observed,
            "noonreportdata": nd,
            "lat": lat,
            "lon": lon,
        },
    }
    record["noon_id"] = noon_row_id(record)
    return record


def _resolve_vessel_id(tenant: Any, rec: dict[str, Any]) -> str | None:
    raw = str(rec.get("vessel_id") or "").strip()
    if raw.isdigit():
        return raw
    try:
        from prevoyage_db.vessel_lookup import lookup_vessel_id

        return lookup_vessel_id(tenant, rec)
    except Exception:
        return raw if raw.isdigit() else None


class DbNoonSource(NoonSource):
    """Poll client DB (std_enoonreporttable) for open registry voyages.

    One row at a time is enforced by NoonExcelWatchAgent; this returns all
    unprocessed candidates oldest-first so the watcher can pick the next one.
    """

    def fetch_new(self, registry: VoyageRegistry) -> list[dict[str, Any]]:
        tenant_key = (settings.tenant or "").strip().lower()
        if not tenant_key:
            return []
        try:
            from prevoyage_db.config import load_tenants
            from prevoyage_db.noon import fetch_noon_rows
        except Exception:
            return []
        tenants = load_tenants()
        tenant = tenants.get(tenant_key)
        if not tenant:
            return []

        out: list[dict[str, Any]] = []
        for voy, rec in registry.all().items():
            if voyage_is_closed(rec):
                continue
            vessel_id = _resolve_vessel_id(tenant, rec)
            try:
                raw_rows = fetch_noon_rows(
                    tenant, voyage_number=voy, vessel_id=vessel_id
                )
            except Exception as e:
                from vpm_agents.tools.agent_log import progress

                progress("DbNoonSource", f"{voy} fetch failed: {e}")
                continue
            for raw in raw_rows:
                mapped = row_from_db(raw, registry_voyage=voy)
                if not mapped:
                    continue
                if registry.is_noon_processed(mapped["noon_id"]):
                    continue
                out.append(mapped)
        return out


def get_noon_sources() -> list[NoonSource]:
    mode = (settings.noon_source or "excel").lower()
    if mode == "db":
        return [DbNoonSource()]
    if mode in {"both", "db+excel", "excel+db"}:
        return [DbNoonSource(), FolderNoonSource()]
    return [FolderNoonSource(), ExcelNoonSource()]


def get_noon_source() -> NoonSource:
    return get_noon_sources()[0]


if __name__ == "__main__":
    sample = Path(__file__).resolve().parents[2] / "samples" / "inbox" / "noon_report.csv"
    rows = parse_noon_drop(sample)
    assert len(rows) == 1 and rows[0]["voyage_number"] == "VYG-2026-001"
    fake = {
        "id": 1,
        "report_id": "abc-123",
        "voyage": "2611 L",
        "reporttype": "Departure Report",
        "report_date_time_utc": None,
        "noonreportdata": {
            "Latitude": "13°56'3''N",
            "Longitude": "109°29'8''E",
            "Vessel_Name": "CONDOR EXPRESS",
            "Avg_Speed": "11.5",
            "Report_Type": "Departure Report",
        },
    }
    mapped = row_from_db(fake, registry_voyage="V2611L")
    assert mapped and mapped["voyage_number"] == "V2611L"
    assert mapped["report_type"] == "Departure Report"
    assert mapped["noon_id"] == "report:abc-123"
    assert mapped["lat"] > 13 and mapped["lon"] > 109
    print("noon_source self-check ok")
