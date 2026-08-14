"""Parse combined noon-report Excel workbooks (multi-row, BE-style columns)."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vpm_agents.tools.inbox_io import parse_dm_coordinate


def parse_dms_coordinate(text: str) -> float:
    """Parse 35°13'6''N, 12 39.812 N, or decimal."""
    s = str(text).strip().upper().replace("''", '"').replace("′", "'").replace("″", '"')
    m = re.match(
        r"(\d+(?:\.\d+)?)[°\s]+(\d+(?:\.\d+)?)['\s]+(\d+(?:\.\d+)?)[\"'\s]*([NSEW])",
        s,
    )
    if m:
        deg, minutes, sec, hemi = map(m.group, (1, 2, 3, 4))
        val = float(deg) + float(minutes) / 60 + float(sec) / 3600
        if hemi in ("S", "W"):
            val = -val
        return round(val, 6)
    return parse_dm_coordinate(text)


def normalize_voyage_number(v: str) -> str:
    v = str(v).strip().upper()
    return v if v.startswith("V") else f"V{v}"


def _norm_col(h: str) -> str:
    return re.sub(r"\s+", "_", h.strip().lower())


def noon_row_id(row: dict[str, Any]) -> str:
    rid = row.get("report_id") or row.get("id")
    if rid is not None and str(rid).strip():
        return f"report:{rid}"
    blob = f"{row.get('voyage_number')}|{row.get('observed_at')}|{row.get('lat')}|{row.get('lon')}"
    return "hash:" + hashlib.sha256(blob.encode()).hexdigest()[:16]


def parse_noon_excel(path: str | Path) -> list[dict[str, Any]]:
    """Read all noon rows from a combined Excel workbook."""
    path = Path(path)
    try:
        from openpyxl import load_workbook
    except ImportError as e:
        raise ImportError("openpyxl required — pip install openpyxl") from e

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        header_row = next(rows_iter, None)
        if not header_row:
            return []
        headers = [_norm_col(str(h)) if h is not None else "" for h in header_row]
        col = {h: i for i, h in enumerate(headers) if h}

        def cell(row: tuple, *names: str) -> Any:
            for name in names:
                idx = col.get(name)
                if idx is not None and idx < len(row):
                    v = row[idx]
                    if v is not None and str(v).strip() != "":
                        return v
            return None

        out: list[dict[str, Any]] = []
        for row in rows_iter:
            if not row or all(c is None or str(c).strip() == "" for c in row):
                continue
            lat_raw = cell(row, "latitude", "lat")
            lon_raw = cell(row, "longitude", "lon")
            voy = cell(row, "voyage_number")
            if lat_raw is None or lon_raw is None or voy is None:
                continue
            try:
                lat = parse_dms_coordinate(lat_raw)
                lon = parse_dms_coordinate(lon_raw)
            except ValueError:
                continue

            observed = cell(row, "report_date_time", "observed_at", "report_date_time_local")
            record = {
                "voyage_number": normalize_voyage_number(str(voy)),
                "vessel_name": str(cell(row, "vessel_name") or "").strip(),
                "lat": lat,
                "lon": lon,
                "observed_at": str(observed).strip() if observed else None,
                "report_type": str(cell(row, "report_type") or "").strip(),
                "avg_speed_kn": _float_or_none(cell(row, "avg_speed", "average_speed_since_sov", "log_speed")),
                "report_id": cell(row, "report_id", "id"),
                "source_file": str(path),
                "source": "excel",
            }
            record["noon_id"] = noon_row_id(record)
            out.append(record)
        out.reverse()  # sheet is newest-first; process oldest noon first
        return out
    finally:
        wb.close()


def _float_or_none(v: Any) -> float | None:
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
