"""Email generated report PDFs. Recipients from env now; DB is a placeholder."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from vpm_agents.config import settings
from vpm_agents.tools.agent_log import progress


def _parse_emails(raw: str) -> list[str]:
    return [e.strip() for e in (raw or "").replace(";", ",").split(",") if e.strip() and "@" in e]


def recipients_from_db(voyage_number: str = "") -> list[str]:
    """Placeholder — wire to voyagepm_be / company contacts when DB is available.

    Expected lookup: voyage_number → vessel/company → report recipient emails.
    """
    # ponytail: returns [] until DB connection + schema are provided
    _ = voyage_number
    return []


def report_recipients(voyage_number: str = "") -> list[str]:
    source = (settings.report_email_source or "env").strip().lower()
    if source == "db":
        found = recipients_from_db(voyage_number)
        if found:
            return found
        # ponytail: fall through to env until DB is wired
    return _parse_emails(settings.report_email)


def send_report_pdf(path: str | Path, *, voyage_number: str = "") -> bool:
    """Attach PDF and send to configured recipients. Never raises; False = skipped/failed."""
    path = Path(path)
    if path.suffix.lower() != ".pdf" or not path.is_file():
        return False
    to = report_recipients(voyage_number)
    if not to:
        return False
    host = (settings.smtp_host or "").strip()
    if not host:
        progress("ReportEmail", f"skip {path.name}: VPM_SMTP_HOST unset")
        return False
    from_addr = (settings.smtp_from or settings.smtp_user or to[0]).strip()
    voy = voyage_number or path.parent.name
    msg = EmailMessage()
    msg["Subject"] = f"VoyagePM report{f' {voy}' if voy else ''}: {path.name}"
    msg["From"] = from_addr
    msg["To"] = ", ".join(to)
    msg.set_content(
        f"A VoyagePM report was generated.\n\nVoyage: {voy or '—'}\nFile: {path.name}\n"
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
        progress("ReportEmail", f"sent {path.name} → {', '.join(to)}")
        return True
    except Exception as e:
        progress("ReportEmail", f"failed {path.name}: {e}")
        return False


if __name__ == "__main__":
    assert _parse_emails("a@x.com, b@y.com;c@z.com") == ["a@x.com", "b@y.com", "c@z.com"]
    assert _parse_emails("not-an-email") == []
    assert recipients_from_db("VTEST") == []
    assert send_report_pdf("/tmp/nope.pdf") is False
    print("report_email self-check ok")
