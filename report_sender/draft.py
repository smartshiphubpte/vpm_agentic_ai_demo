"""Categorize a report PDF and fill the matching email-body template."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from report_sender.config import ROOT, settings

PASSAGE_WEATHER = "passage_weather"
PRE_DEPARTURE = "pre_departure"
STORM_ALERT = "storm_alert"
END_OF_VOYAGE = "end_of_voyage"
PORT_WEATHER = "port_weather"
GENERIC = "generic"

REPORT_TITLES = {
    PASSAGE_WEATHER: "Passage Weather Report",
    PRE_DEPARTURE: "Pre-Departure Report",
    STORM_ALERT: "Storm Weather Alert",
    END_OF_VOYAGE: "End of Voyage Report",
    PORT_WEATHER: "Port Weather Report",
    GENERIC: "VoyagePM Report",
}

_STAMP_RE = re.compile(r"(\d{8}T\d{6}Z)")
_FALLBACK = (
    "A VoyagePM {report_title} is ready.\n\n"
    "Voyage: {voyage_number}\n"
    "Vessel: {vessel_name}\n"
    "Generated: {timestamp}\n"
    "File: {filename}\n\n"
    "The PDF is attached.\n"
)


class _Blank(dict):
    def __missing__(self, key: str) -> str:
        return "—"


def classify_report(filename: str = "", report_bucket: str = "") -> str:
    """Filename wins (storm PDFs land in the weather_report folder)."""
    name = Path(filename or "").name.lower()
    bucket = (report_bucket or "").strip().lower()
    if any(k in name for k in ("cyclone", "storm_alert", "tropical_cyclone")) or name.startswith(
        "storms_"
    ):
        return STORM_ALERT
    if "port_weather" in name or bucket == "port_weather":
        return PORT_WEATHER
    if "end_of_voyage" in name or bucket == "end_of_voyage_report":
        return END_OF_VOYAGE
    if "pre_voyage" in name or "pre_depart" in name or bucket == "pre_voyage_report":
        return PRE_DEPARTURE
    if "weather_report" in name or "passage_weather" in name or bucket == "weather_report":
        return PASSAGE_WEATHER
    return GENERIC


def template_name(report_type: str) -> str:
    return settings.email_templates.get(report_type) or settings.email_templates[GENERIC]


def load_email_template(report_type: str) -> str:
    name = template_name(report_type)
    for folder in (settings.templates_dir, ROOT / "templates"):
        path = folder / name
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return _FALLBACK


def fill_template(text: str, ctx: dict[str, Any]) -> str:
    clean = {str(k): ("—" if v is None or str(v).strip() == "" else str(v)) for k, v in ctx.items()}
    return text.format_map(_Blank(clean))


def split_subject(filled: str) -> tuple[str, str]:
    lines = filled.splitlines()
    if lines and lines[0].lower().startswith("subject:"):
        return lines[0].split(":", 1)[1].strip(), "\n".join(lines[1:]).lstrip("\n")
    return "", filled


def timestamp_from(path: Path | None = None, filename: str = "") -> str:
    name = filename or (path.name if path else "")
    m = _STAMP_RE.search(name)
    if m:
        raw = m.group(1)
        try:
            dt = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M UTC")
        except ValueError:
            return raw
    if path and path.is_file():
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _route_line(source: str, dest: str) -> str:
    if source and dest:
        return f"{source} → {dest}"
    return source or dest or "—"


def base_context(
    *,
    path: Path | None = None,
    voyage_number: str = "",
    vessel_id: str = "",
    vessel_name: str = "",
    report_bucket: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    filename = path.name if path else ""
    report_type = classify_report(filename, report_bucket)
    ts = timestamp_from(path, filename)
    ctx = {
        "voyage_number": voyage_number,
        "vessel_id": vessel_id,
        "vessel_name": vessel_name or vessel_id,
        "timestamp": ts,
        "generated_at": ts,
        "recipients": "",
        "to": "",
        "voyage_contacts": "",
        "report_type": report_type,
        "report_title": REPORT_TITLES.get(report_type, REPORT_TITLES[GENERIC]),
        "report_bucket": report_bucket,
        "filename": filename,
        "source_port": "",
        "dest_port": "",
        "port_name": "",
        "route_line": "—",
        "etd": "",
    }
    if extra:
        for k, v in extra.items():
            if v is None or str(v).strip() == "":
                continue
            ctx[str(k)] = str(v).strip()
    ctx["vessel_name"] = ctx.get("vessel_name") or ctx.get("vessel_id") or "—"
    ctx["route_line"] = _route_line(ctx.get("source_port") or "", ctx.get("dest_port") or "")
    if not ctx.get("port_name"):
        ctx["port_name"] = ctx.get("dest_port") or "—"
    return ctx


def _registry_path() -> Path:
    path = Path(settings.registry_path)
    if path.is_dir():
        return path / "voyage_registry.json"
    return path


def registry_lookup(voyage_number: str) -> dict[str, str]:
    """Best-effort vessel/route fields from the local voyage registry JSON."""
    if not voyage_number:
        return {}
    path = _registry_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    voyages = data.get("voyages") or {}
    needle = voyage_number.strip().upper()
    rec: dict[str, Any] | None = voyages.get(voyage_number) or voyages.get(needle)
    if rec is None:
        for key, row in voyages.items():
            if str(key).strip().upper() == needle:
                rec = row
                break
    if not isinstance(rec, dict):
        return {}
    noon = rec.get("last_noon") if isinstance(rec.get("last_noon"), dict) else {}
    emails = rec.get("alert_emails") or []
    if isinstance(emails, str):
        contacts = emails
    else:
        contacts = ", ".join(str(e).strip() for e in emails if str(e).strip())
    out = {
        "vessel_name": str(rec.get("vessel_name") or "").strip(),
        "vessel_id": str(rec.get("vessel_id") or "").strip(),
        "source_port": str(rec.get("source_port") or rec.get("departure") or "").strip(),
        "dest_port": str(rec.get("dest_port") or rec.get("destination") or "").strip(),
        "etd": str(rec.get("etd") or "").strip(),
        "voyage_contacts": contacts,
        "port_name": str(
            noon.get("port_name") or noon.get("port") or rec.get("dest_port") or ""
        ).strip(),
    }
    return {k: v for k, v in out.items() if v}


def merge_context(ctx: dict[str, str], *overlays: dict[str, str]) -> dict[str, str]:
    """Fill blank keys from overlays; later overlays win when the current value is empty."""
    for overlay in overlays:
        for k, v in overlay.items():
            if not v or str(v).strip() in ("", "—"):
                continue
            cur = ctx.get(k)
            if not cur or cur == "—":
                ctx[k] = str(v).strip()
    ctx["vessel_name"] = ctx.get("vessel_name") or ctx.get("vessel_id") or "—"
    ctx["route_line"] = _route_line(ctx.get("source_port") or "", ctx.get("dest_port") or "")
    if not ctx.get("port_name") or ctx["port_name"] == "—":
        ctx["port_name"] = ctx.get("dest_port") or "—"
    return ctx


def render_email(report_type: str, ctx: dict[str, Any]) -> tuple[str, str]:
    filled = fill_template(load_email_template(report_type), ctx)
    subject, body = split_subject(filled)
    if not subject:
        voy = ctx.get("voyage_number") or ""
        title = ctx.get("report_title") or "VoyagePM report"
        filename = ctx.get("filename") or ""
        subject = f"{title}{f' {voy}' if voy else ''}{f': {filename}' if filename else ''}"
    return subject, body.rstrip() + "\n"
