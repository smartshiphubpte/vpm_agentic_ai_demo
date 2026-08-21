"""SMTP send for report PDFs."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from report_sender.config import settings
from report_sender.draft import (
    GENERIC,
    base_context,
    classify_report,
    merge_context,
    registry_lookup,
    render_email,
)


def _parse_emails(raw: str) -> list[str]:
    return [e.strip() for e in (raw or "").replace(";", ",").split(",") if e.strip() and "@" in e]


def _db_overlay(voyage_number: str) -> dict[str, str]:
    """Lazy import so folder-watch still works with no DB URLs configured."""
    if not voyage_number or not settings.db_urls:
        return {}
    from report_sender.db import voyage_contacts, voyage_details

    out: dict[str, str] = {}
    emails: list[str] = []
    for url in settings.db_urls:
        emails.extend(voyage_contacts(url, voyage_number))
        for k, v in voyage_details(url, voyage_number).items():
            if v and not out.get(k):
                out[k] = v
    if emails:
        seen: set[str] = set()
        uniq: list[str] = []
        for e in emails:
            key = e.lower()
            if key not in seen:
                seen.add(key)
                uniq.append(e)
        out["voyage_contacts"] = ", ".join(uniq)
    return out


def resolve_report_recipients(
    *,
    voyage_number: str = "",
    report_bucket: str = "",
    audience: str = "",
) -> list[str]:
    """env = review address; db = vpm_voyage_email / registry alert_emails, then env."""
    _ = (report_bucket, audience)
    env_to = _parse_emails(settings.review_email)
    if (settings.report_email_source or "env").strip().lower() != "db":
        return env_to
    overlay = _db_overlay(voyage_number)
    found = _parse_emails(overlay.get("voyage_contacts") or "")
    if not found:
        found = _parse_emails(registry_lookup(voyage_number).get("voyage_contacts") or "")
    return found or env_to


def send_report_pdf(
    path: str | Path,
    *,
    to: list[str] | None = None,
    voyage_number: str = "",
    vessel_id: str = "",
    vessel_name: str = "",
    report_bucket: str = "",
    audience: str = "",
    subject_suffix: str = "",
    extra: dict[str, Any] | None = None,
) -> bool:
    """Attach PDF and send a type-specific body. Never raises; False = skipped/failed."""
    path = Path(path)
    if path.suffix.lower() != ".pdf" or not path.is_file():
        return False
    recipients = to or resolve_report_recipients(
        voyage_number=voyage_number,
        report_bucket=report_bucket,
        audience=audience,
    )
    if not recipients:
        log("skip", f"{path.name}: no recipients (env/db routing unresolved)")
        return False
    host = (settings.smtp_host or "").strip()
    if not host:
        log("skip", f"{path.name}: VPM_SMTP_HOST unset")
        return False

    report_type = classify_report(path.name, report_bucket)
    pinned = {k: str(v).strip() for k, v in (extra or {}).items() if v is not None and str(v).strip()}
    ctx = base_context(
        path=path,
        voyage_number=voyage_number or path.parent.name,
        vessel_id=vessel_id,
        vessel_name=vessel_name,
        report_bucket=report_bucket,
        extra=pinned,
    )
    merge_context(ctx, registry_lookup(ctx["voyage_number"]), _db_overlay(ctx["voyage_number"]), pinned)
    ctx["recipients"] = ", ".join(recipients)
    ctx["to"] = ctx["recipients"]
    if not ctx.get("voyage_contacts") or ctx["voyage_contacts"] == "—":
        ctx["voyage_contacts"] = ctx["recipients"]

    subject, body = render_email(report_type, ctx)
    suffix = f" {subject_suffix}" if subject_suffix else ""
    from_addr = (settings.smtp_from or settings.smtp_user or recipients[0]).strip()
    msg = EmailMessage()
    msg["Subject"] = f"{subject}{suffix}"
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    msg.add_attachment(
        path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=path.name,
    )
    try:
        port = int(settings.smtp_port or 587)
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, timeout=120)
        else:
            smtp = smtplib.SMTP(host, port, timeout=120)
            smtp.starttls()
        with smtp:
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        kind = report_type if report_type != GENERIC else "report"
        log("sent", f"{path.name} [{kind}] → {', '.join(recipients)}")
        return True
    except Exception as e:
        log("failed", f"{path.name}: {e}")
        return False


def log(tag: str, msg: str) -> None:
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    print(f"[{ts}] [ReportSender:{tag}] {msg}", flush=True)
