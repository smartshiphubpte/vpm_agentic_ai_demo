"""SMTP send for report PDFs."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from report_sender.config import settings


def _parse_emails(raw: str) -> list[str]:
    return [e.strip() for e in (raw or "").replace(";", ",").split(",") if e.strip() and "@" in e]


def send_report_pdf(
    path: str | Path,
    *,
    to: list[str] | None = None,
    voyage_number: str = "",
    subject_suffix: str = "",
) -> bool:
    """Attach PDF and send. Never raises; False = skipped/failed."""
    path = Path(path)
    if path.suffix.lower() != ".pdf" or not path.is_file():
        return False
    recipients = to or _parse_emails(settings.review_email)
    if not recipients:
        log("skip", f"{path.name}: no recipients (VPM_REPORT_SENDER_REVIEW_EMAIL unset)")
        return False
    host = (settings.smtp_host or "").strip()
    if not host:
        log("skip", f"{path.name}: VPM_SMTP_HOST unset")
        return False
    from_addr = (settings.smtp_from or settings.smtp_user or recipients[0]).strip()
    voy = voyage_number or path.parent.name
    suffix = f" {subject_suffix}" if subject_suffix else ""
    msg = EmailMessage()
    msg["Subject"] = f"VoyagePM report{f' {voy}' if voy else ''}{suffix}: {path.name}"
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg.set_content(
        f"A VoyagePM report is ready for review.\n\nVoyage: {voy or '—'}\nFile: {path.name}\n"
    )
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
        log("sent", f"{path.name} → {', '.join(recipients)}")
        return True
    except Exception as e:
        log("failed", f"{path.name}: {e}")
        return False


def log(tag: str, msg: str) -> None:
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    print(f"[{ts}] [ReportSender:{tag}] {msg}", flush=True)
