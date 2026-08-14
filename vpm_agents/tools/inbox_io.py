"""Inbox drop-folder I/O — pre-voyage / noon Excel (or CSV stand-in).

Supports:
  1) Flat CSV/xlsx demo schema (voyage_number, cp_speed_kn, waypoints, …)
  2) Real SSH Pre-Dep Voyage workbook (sheets: Vessel Details, Voyage Details,
     Waypoints List, CP Terms FWC)

Drop files into VPM_INBOX_DIR. Processed files move to inbox/processed/.
"""

from __future__ import annotations

import csv
import re
import shutil
from pathlib import Path
from typing import Any, Literal

MsgKind = Literal["pre_voyage", "noon_report", "unknown"]

_INBOX_SUFFIXES = {".csv", ".xlsx", ".xlsm"}

# Real Pre-Dep workbook sheet names (case-insensitive match)
_PREDEP_SHEETS = {
    "voyage details": "voyage",
    "waypoints list": "waypoints",
    "vessel details": "vessel",
    "cp terms fwc": "cp",
}


def _norm_header(h: str) -> str:
    return h.strip().lower().replace(" ", "_")


def _norm_label(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _is_xlsx(path: Path) -> bool:
    return path.suffix.lower() in {".xlsx", ".xlsm"}


def _load_wb(path: Path):
    try:
        from openpyxl import load_workbook
    except ImportError as e:
        raise ImportError("openpyxl required for .xlsx — pip install openpyxl") from e
    return load_workbook(path, data_only=True)


def _sheet_map(wb) -> dict[str, Any]:
    """Map logical roles → worksheet for Pre-Dep workbooks."""
    out: dict[str, Any] = {}
    for ws in wb.worksheets:
        key = _PREDEP_SHEETS.get(_norm_label(ws.title))
        if key:
            out[key] = ws
    return out


def is_predep_workbook(path: Path) -> bool:
    if not _is_xlsx(path):
        return False
    wb = _load_wb(path)
    try:
        sheets = _sheet_map(wb)
        return "voyage" in sheets and "waypoints" in sheets
    finally:
        wb.close()


def _label_value_map(ws, value_col: int = 4, sample_col: int = 7) -> dict[str, Any]:
    """Read label(col C) → value(col D), falling back to sample(col G)."""
    found: dict[str, Any] = {}
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row or 40, max_col=10, values_only=True):
        cells = list(row) + [None] * 10
        label = cells[2]
        if label is None or str(label).strip() == "":
            continue
        key = _norm_label(label)
        val = cells[value_col - 1]
        if val is None or str(val).strip() == "":
            val = cells[sample_col - 1]
        if val is None or str(val).strip() == "":
            continue
        found[key] = val
    return found


def _pick(mapping: dict[str, Any], *needles: str) -> Any:
    for needle in needles:
        n = _norm_label(needle)
        for k, v in mapping.items():
            if n in k or k in n:
                return v
    return None


def parse_dm_coordinate(text: str) -> float:
    """Parse '12 39.812 N' / '109 24.414 E' (deg + decimal minutes + hemisphere)."""
    s = str(text).strip().upper().replace("°", " ").replace("'", " ").replace('"', " ")
    s = re.sub(r"\s+", " ", s)
    m = re.match(
        r"^(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*([NSEW])$",
        s,
    )
    if not m:
        # already decimal?
        try:
            return float(s)
        except ValueError as e:
            raise ValueError(f"bad coordinate: {text!r}") from e
    deg = float(m.group(1))
    minutes = float(m.group(2))
    hemi = m.group(3)
    val = deg + minutes / 60.0
    if hemi in ("S", "W"):
        val = -val
    return round(val, 6)


def parse_predep_workbook(path: Path) -> dict[str, Any]:
    """Parse SSH Pre-Dep Voyage *.xlsx into the common pre-voyage dict."""
    wb = _load_wb(path)
    try:
        sheets = _sheet_map(wb)
        if "voyage" not in sheets or "waypoints" not in sheets:
            raise ValueError(f"{path.name}: not a Pre-Dep workbook (missing Voyage/Waypoints sheets)")

        voyage = _label_value_map(sheets["voyage"])
        vessel = _label_value_map(sheets["vessel"]) if "vessel" in sheets else {}
        cp = _label_value_map(sheets["cp"]) if "cp" in sheets else {}

        voyage_number = _pick(voyage, "voyage number")
        source_port = _pick(voyage, "departure port")
        dest_port = _pick(voyage, "destination port")
        vessel_name = _pick(vessel, "vessel name")
        imo = _pick(vessel, "imo no", "imo")
        cp_speed = _pick(cp, "cp speed in kts/day", "cp speed")
        cp_consumption = _pick(cp, "cp consumption", "consumption mt/day", "fwc consumption")

        if voyage_number is None:
            raise ValueError(f"{path.name}: Voyage Number missing on Voyage Details")
        if source_port is None or dest_port is None:
            raise ValueError(f"{path.name}: Departure/Destination port missing")
        if cp_speed is None:
            raise ValueError(
                f"{path.name}: CP Speed missing on CP Terms FWC "
                "(fill value or keep Sample format 12.5)"
            )

        waypoints: list[list[float]] = []
        wp_names: list[str] = []
        ws = sheets["waypoints"]
        # Row 3 = Lat/Long headers; data from row 4
        for row in ws.iter_rows(min_row=4, max_row=ws.max_row or 4, max_col=7, values_only=True):
            name, lat_s, lon_s = (row + (None, None, None))[:3]
            if lat_s is None or lon_s is None:
                continue
            if str(lat_s).strip() == "" or str(lon_s).strip() == "":
                continue
            # skip header repeats
            if _norm_label(str(lat_s)) == "lat":
                continue
            try:
                lat = parse_dm_coordinate(lat_s)
                lon = parse_dm_coordinate(lon_s)
            except ValueError:
                continue
            waypoints.append([lat, lon])
            wp_names.append("" if name is None else str(name).strip())

        if len(waypoints) < 2:
            raise ValueError(f"{path.name}: Waypoints List needs ≥2 points")

        vessel_id = str(imo).strip() if imo is not None else (str(vessel_name).strip() if vessel_name else "")
        return {
            "voyage_number": str(voyage_number).strip(),
            "vessel_id": vessel_id,
            "vessel_name": "" if vessel_name is None else str(vessel_name).strip(),
            "source_port": str(source_port).strip(),
            "dest_port": str(dest_port).strip(),
            "cp_speed_kn": float(cp_speed),
            "cp_consumption_mt_day": float(cp_consumption) if cp_consumption not in (None, "") else None,
            "alert_emails": [],
            "master_waypoints": waypoints,
            "waypoint_names": wp_names,
            "source_file": str(path),
            "format": "predep_xlsx",
            "condition": str(_pick(voyage, "condition (ballast/laden)") or "").strip(),
            "etd": str(_pick(voyage, "estimated departure time") or "").strip(),
            "eta": str(_pick(voyage, "estimated arrival time") or "").strip(),
        }
    finally:
        wb.close()


def _read_table(path: Path) -> tuple[list[str], list[str]]:
    """Return (headers, first_data_row_values) from flat csv or single-sheet xlsx."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        if len(rows) < 2:
            raise ValueError(f"{path.name}: need header + one data row")
        return [_norm_header(h) for h in rows[0]], [c.strip() for c in rows[1]]

    if suffix in {".xlsx", ".xlsm"}:
        wb = _load_wb(path)
        try:
            ws = wb.active
            rows_iter = ws.iter_rows(values_only=True)
            header_row = next(rows_iter, None)
            data_row = next(rows_iter, None)
            if not header_row or not data_row:
                raise ValueError(f"{path.name}: need header + one data row")
            headers = [_norm_header(str(h)) for h in header_row if h is not None]
            values = ["" if v is None else str(v).strip() for v in data_row[: len(headers)]]
            return headers, values
        finally:
            wb.close()

    raise ValueError(f"unsupported inbox file type: {suffix}")


def classify_inbox_file(path: str | Path) -> MsgKind:
    path = Path(path)
    name_l = path.name.lower()

    # Real Pre-Dep Voyage workbook
    if _is_xlsx(path):
        try:
            if is_predep_workbook(path):
                return "pre_voyage"
        except Exception:
            pass
        if name_l.startswith("pre-dep") or "pre-dep voyage" in name_l or "pre_dep" in name_l:
            # filename strongly suggests pre-voyage even if sheets renamed
            try:
                if is_predep_workbook(path):
                    return "pre_voyage"
            except Exception:
                return "pre_voyage"  # let parse raise a clear error

    try:
        headers, _ = _read_table(path)
    except Exception:
        return "unknown"
    cols = set(headers)
    if "waypoints" in cols and "cp_speed_kn" in cols:
        return "pre_voyage"
    if {"voyage_number", "lat", "lon"} <= cols:
        return "noon_report"
    return "unknown"


def _parse_waypoint_field(wp_raw: str) -> list[list[float]]:
    waypoints: list[list[float]] = []
    for pair in wp_raw.split(";"):
        pair = pair.strip()
        if not pair:
            continue
        lat_s, lon_s = pair.split(",")
        waypoints.append([float(lat_s), float(lon_s)])
    if len(waypoints) < 2:
        raise ValueError("pre-voyage needs ≥2 waypoints")
    return waypoints


def parse_pre_voyage(path: str | Path) -> dict[str, Any]:
    """Parse flat CSV/xlsx demo schema OR real Pre-Dep Voyage workbook."""
    path = Path(path)

    if _is_xlsx(path) and is_predep_workbook(path):
        return parse_predep_workbook(path)

    if path.suffix.lower() == ".csv":
        lines = path.read_text(encoding="utf-8-sig").strip().splitlines()
        if len(lines) < 2:
            raise ValueError(f"{path.name}: need header + one data row")
        headers = [_norm_header(h) for h in lines[0].split(",")]
        parts = lines[1].split(",", 6)
        if len(parts) < 7:
            raise ValueError(f"{path.name}: bad pre-voyage CSV row")
        row = dict(zip(headers[:6], [p.strip() for p in parts[:6]]))
        row["waypoints"] = parts[6].strip()
    else:
        headers, values = _read_table(path)
        row = dict(zip(headers, values))

    for key in ("voyage_number", "vessel_id", "source_port", "dest_port", "cp_speed_kn", "waypoints"):
        if key not in row or not row[key]:
            raise ValueError(f"pre-voyage missing {key}")
    emails = [e for e in row.get("alert_emails", "").split("|") if e.strip()]
    return {
        "voyage_number": row["voyage_number"],
        "vessel_id": row["vessel_id"],
        "source_port": row["source_port"],
        "dest_port": row["dest_port"],
        "cp_speed_kn": float(row["cp_speed_kn"]),
        "alert_emails": emails,
        "master_waypoints": _parse_waypoint_field(row["waypoints"]),
        "source_file": str(path),
        "format": "flat",
    }


def parse_noon_report(path: str | Path) -> dict[str, Any]:
    """Columns: voyage_number, lat, lon  (optional observed_at)."""
    headers, values = _read_table(Path(path))
    row = dict(zip(headers, values))
    for key in ("voyage_number", "lat", "lon"):
        if key not in row or not row[key]:
            raise ValueError(f"noon report missing {key}")
    return {
        "voyage_number": row["voyage_number"],
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "observed_at": row.get("observed_at") or None,
        "source_file": str(path),
    }


def list_inbox(inbox_dir: str | Path) -> list[Path]:
    inbox = Path(inbox_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "processed").mkdir(exist_ok=True)
    (inbox / "failed").mkdir(exist_ok=True)
    return sorted(
        p
        for p in inbox.iterdir()
        if p.is_file() and p.suffix.lower() in _INBOX_SUFFIXES and not p.name.startswith(".")
    )


def archive_inbox_file(path: Path, dest_subdir: str = "processed") -> Path:
    path = Path(path)
    dest_dir = path.parent / dest_subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    if dest.exists():
        dest = dest_dir / f"{path.stem}_{path.stat().st_mtime_ns}{path.suffix}"
    shutil.move(str(path), str(dest))
    return dest
