"""Shared report/drop folder layout helpers."""

from __future__ import annotations

from pathlib import Path
import re

INCOMING = "incoming"
SENT = "sent"
FAILED = "failed"
_SKIP_DIRS = frozenset({INCOMING, SENT, FAILED, "processed"})

PRE_VOYAGE_REPORT = "pre_voyage_report"
WEATHER_REPORT = "weather_report"
PORT_WEATHER_REPORT = "port_weather"
END_OF_VOYAGE_REPORT = "end_of_voyage_report"
VPA_REPORT = "vpa"

REPORT_BUCKETS = frozenset(
    {
        PRE_VOYAGE_REPORT,
        WEATHER_REPORT,
        PORT_WEATHER_REPORT,
        END_OF_VOYAGE_REPORT,
        VPA_REPORT,
    }
)


def _clean_segment(value: str, fallback: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw)
    cleaned = cleaned.strip("._-")
    return cleaned or fallback


def vessel_dir_name(vessel_id: str) -> str:
    """Use IMO/vessel_id as the vessel folder name."""
    digits = "".join(ch for ch in str(vessel_id or "").strip() if ch.isdigit())
    return digits or _clean_segment(str(vessel_id or ""), "unknown_vessel")


def voyage_dir_name(voyage_number: str) -> str:
    return _clean_segment(voyage_number, "unknown_voyage")


def voyage_root(reports_root: Path, vessel_id: str, voyage_number: str) -> Path:
    root = Path(reports_root) / vessel_dir_name(vessel_id) / voyage_dir_name(voyage_number)
    root.mkdir(parents=True, exist_ok=True)
    return root


def voyage_report_dir(
    reports_root: Path,
    vessel_id: str,
    voyage_number: str,
    report_bucket: str,
) -> Path:
    bucket = _clean_segment(report_bucket, "report")
    root = voyage_root(reports_root, vessel_id, voyage_number) / bucket
    root.mkdir(parents=True, exist_ok=True)
    return root


def report_context_from_path(path: Path, *, roots: tuple[Path, ...] = ()) -> dict[str, str]:
    """Best-effort parse of .../{vessel_id}/{voyage_number}/{report_bucket}/incoming/file.pdf."""
    p = Path(path).resolve()
    parts = p.parts
    ctx = {"vessel_id": "", "voyage_number": "", "report_bucket": ""}
    try:
        incoming_i = len(parts) - 1 - list(reversed(parts)).index(INCOMING)
    except ValueError:
        incoming_i = -1
    if incoming_i >= 3:
        ctx["report_bucket"] = parts[incoming_i - 1]
        ctx["voyage_number"] = parts[incoming_i - 2]
        ctx["vessel_id"] = parts[incoming_i - 3]
        return ctx
    for root in roots:
        try:
            rel = p.relative_to(Path(root).resolve())
        except ValueError:
            continue
        rel_parts = rel.parts
        if len(rel_parts) >= 4 and rel_parts[2] in REPORT_BUCKETS:
            ctx["vessel_id"] = rel_parts[0]
            ctx["voyage_number"] = rel_parts[1]
            ctx["report_bucket"] = rel_parts[2]
            break
    return ctx


def ensure_drop_dirs(base: Path) -> None:
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    for name in (INCOMING, SENT, FAILED):
        (base / name).mkdir(parents=True, exist_ok=True)


def incoming_dir(base: Path) -> Path:
    d = Path(base) / INCOMING
    d.mkdir(parents=True, exist_ok=True)
    return d


def sent_dir(base: Path) -> Path:
    d = Path(base) / SENT
    d.mkdir(parents=True, exist_ok=True)
    return d


def failed_dir(base: Path) -> Path:
    d = Path(base) / FAILED
    d.mkdir(parents=True, exist_ok=True)
    return d


if __name__ == "__main__":
    import tempfile

    root = Path(tempfile.mkdtemp())
    ensure_drop_dirs(root)
    assert (root / INCOMING).is_dir() and (root / SENT).is_dir()
    p = incoming_dir(root) / "test.pdf"
    p.write_bytes(b"x")
    assert p.is_file()
    voy_root = voyage_root(root, "9184902", "V001")
    assert voy_root == root / "9184902" / "V001"
    wx_dir = voyage_report_dir(root, "9184902", "V001", WEATHER_REPORT)
    assert wx_dir == voy_root / WEATHER_REPORT
    ctx = report_context_from_path(wx_dir / INCOMING / "weather_report_test.pdf")
    assert ctx["vessel_id"] == "9184902"
    assert ctx["voyage_number"] == "V001"
    assert ctx["report_bucket"] == WEATHER_REPORT
    print("folder_layout self-check ok")
