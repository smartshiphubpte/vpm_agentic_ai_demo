"""IMAP poller — parse pre-voyage attachments in memory; never write them to disk."""

from __future__ import annotations

import imaplib
import smtplib
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Any

from vpm_agents.config import settings
from inbox_agent.parse import _INBOX_SUFFIXES, try_parse_pre_voyage

_ATTACH_OK = {s.lower() for s in _INBOX_SUFFIXES}

# Custom-domain mailboxes on iPowered still login at imap.ipower.com with the full address.
_KNOWN_IMAP = {
    "gmail.com": ("imap.gmail.com",),
    "googlemail.com": ("imap.gmail.com",),
    "outlook.com": ("outlook.office365.com",),
    "hotmail.com": ("outlook.office365.com",),
    "live.com": ("outlook.office365.com",),
    "office365.com": ("outlook.office365.com",),
}

_working_host: str | None = None


@dataclass
class IncomingMail:
    uid: str
    raw: bytes
    subject: str
    from_addr: str
    attachments: list[tuple[str, bytes]] = field(default_factory=list)


def mail_enabled() -> bool:
    return bool(settings.mail_imap_user and settings.mail_imap_password)


def _candidate_hosts() -> list[str]:
    explicit = (settings.mail_imap_host or "").strip()
    if explicit:
        return [explicit]
    user = (settings.mail_imap_user or "").strip().lower()
    domain = user.split("@", 1)[-1] if "@" in user else ""
    known = _KNOWN_IMAP.get(domain)
    if known:
        return list(known)
    hosts = ["imap.ipower.com", "mail.ipower.com"]
    if domain:
        hosts.extend([f"mail.{domain}", f"imap.{domain}"])
    seen: set[str] = set()
    out: list[str] = []
    for h in hosts:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _open_imap(host: str) -> imaplib.IMAP4:
    port = int(settings.mail_imap_port)
    timeout = 60
    if settings.mail_imap_ssl:
        imap = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    else:
        imap = imaplib.IMAP4(host, port, timeout=timeout)
        imap.starttls()
    imap.login(settings.mail_imap_user, settings.mail_imap_password)
    return imap


def _connect() -> imaplib.IMAP4:
    global _working_host
    hosts = _candidate_hosts()
    if _working_host and _working_host in hosts:
        hosts = [_working_host, *[h for h in hosts if h != _working_host]]
    last: Exception | None = None
    for host in hosts:
        try:
            imap = _open_imap(host)
            if _working_host != host:
                print(f"[MailInbox] IMAP login {settings.mail_imap_user} @ {host}", flush=True)
            _working_host = host
            return imap
        except Exception as e:
            last = e
    raise RuntimeError(
        f"IMAP login failed for {settings.mail_imap_user!r} on {hosts}: {last}"
    )


def _decode_filename(part) -> str:
    name = part.get_filename() or ""
    return str(name).strip()


def parse_rfc822(raw: bytes) -> IncomingMail:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    subject = str(msg.get("Subject") or "").strip()
    from_addr = str(msg.get("From") or "").strip()
    atts: list[tuple[str, bytes]] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disp = (part.get_content_disposition() or "").lower()
        name = _decode_filename(part)
        if not name:
            continue
        from pathlib import Path

        if Path(name).suffix.lower() not in _ATTACH_OK:
            continue
        if disp == "inline" and Path(name).suffix.lower() not in _ATTACH_OK:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        atts.append((name, payload))
    return IncomingMail(uid="", raw=raw, subject=subject, from_addr=from_addr, attachments=atts)


def fetch_unseen() -> list[IncomingMail]:
    if not mail_enabled():
        return []
    needle = (settings.mail_subject_contains or "").strip().lower()
    imap = _connect()
    try:
        typ, _ = imap.select(settings.mail_imap_folder, readonly=False)
        if typ != "OK":
            raise RuntimeError(f"cannot select IMAP folder {settings.mail_imap_folder!r}")
        typ, data = imap.uid("SEARCH", None, "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()
        out: list[IncomingMail] = []
        for uid in uids:
            typ, fetched = imap.uid("FETCH", uid, "(RFC822)")
            if typ != "OK" or not fetched:
                continue
            raw = b""
            for item in fetched:
                if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                    raw = bytes(item[1])
                    break
            if not raw:
                continue
            mail = parse_rfc822(raw)
            mail.uid = uid.decode() if isinstance(uid, bytes) else str(uid)
            if needle and needle not in mail.subject.lower():
                continue
            out.append(mail)
        return out
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def mark_seen(uid: str) -> None:
    imap = _connect()
    try:
        imap.select(settings.mail_imap_folder, readonly=False)
        imap.uid("STORE", uid, "+FLAGS", r"(\Seen)")
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def _smtp_send(msg: EmailMessage) -> None:
    host = (settings.smtp_host or "").strip()
    if not host:
        raise RuntimeError("VPM_SMTP_HOST unset — cannot forward rejected mail")
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


def forward_rejection(mail: IncomingMail, reasons: list[str], *, accepted: int = 0) -> None:
    to = (settings.mail_reject_to or "").strip()
    if not to:
        raise RuntimeError("VPM_MAIL_REJECT_TO unset — cannot forward rejected mail")
    from_addr = (settings.smtp_from or settings.smtp_user or settings.mail_imap_user).strip()
    bullets = "\n".join(f"  - {r}" for r in reasons)
    if accepted:
        lead = (
            f"{accepted} attachment(s) were queued for the database. "
            "The following did not pass validation:\n\n"
        )
    else:
        lead = "This pre-voyage email was not written to the database.\n\n"
    body = (
        f"{lead}"
        f"Original From: {mail.from_addr or '(unknown)'}\n"
        f"Original Subject: {mail.subject or '(none)'}\n"
        f"Mailbox: {settings.mail_imap_user}\n\n"
        "Rejection details — each item is the field that failed and the Excel values we read:\n"
        f"{bullets}\n\n"
        "The original message is attached.\n"
    )
    msg = EmailMessage()
    orig_subj = mail.subject or "(no subject)"
    msg["Subject"] = f"Rejected pre-voyage ingest: {orig_subj}"
    msg["From"] = from_addr
    msg["To"] = to
    msg.set_content(body)
    msg.add_attachment(
        mail.raw,
        maintype="message",
        subtype="rfc822",
        filename="original.eml",
    )
    _smtp_send(msg)


def vessel_lookup_issue(record: dict[str, Any]) -> str | None:
    """Optional client-DB check when prevoyage_db/.env is available on this process."""
    tenant_key = (settings.tenant or "").strip().lower()
    if not tenant_key:
        return "VPM_TENANT is unset — cannot enqueue a database write."
    try:
        from prevoyage_db.config import load_tenants
        from prevoyage_db.vessel_lookup import lookup_vessel_id
    except Exception:
        return None
    tenants = load_tenants()
    tenant = tenants.get(tenant_key)
    if not tenant:
        return None
    try:
        lookup_vessel_id(tenant, record)
    except Exception as e:
        return (
            f"Vessel not found in client database ({tenant.client_schema}.ship): {e}. "
            "Check Vessel Name / IMO on the form against the ship register."
        )
    return None


def try_attachments(mail: IncomingMail) -> tuple[list[dict[str, Any]], list[str]]:
    ok: list[dict[str, Any]] = []
    issues: list[str] = []
    if not mail.attachments:
        issues.append(
            "No pre-voyage attachment found. Attach a .xlsx / .xlsm Pre-Dep workbook or a .csv."
        )
        return ok, issues
    for name, blob in mail.attachments:
        rec, errs = try_parse_pre_voyage(filename=name, data=blob)
        if errs:
            issues.extend(errs)
            continue
        assert rec is not None
        extra = vessel_lookup_issue(rec)
        if extra:
            issues.append(f"{name}: {extra}")
            continue
        rec["source_file"] = f"imap:{mail.uid}:{name}"
        ok.append(rec)
    return ok, issues


if __name__ == "__main__":
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from pathlib import Path

    assert _candidate_hosts()[0] in ("imap.ipower.com", "imap.gmail.com", "outlook.office365.com") or settings.mail_imap_host
    sample = Path(__file__).resolve().parents[1] / "samples" / "inbox" / "pre_voyage.csv"
    outer = MIMEMultipart()
    outer["Subject"] = "Pre-Dep test"
    outer["From"] = "master@ship.test"
    outer.attach(MIMEText("see attached"))
    att = MIMEApplication(sample.read_bytes(), Name=sample.name)
    att["Content-Disposition"] = f'attachment; filename="{sample.name}"'
    outer.attach(att)
    mail = parse_rfc822(outer.as_bytes())
    assert mail.attachments and mail.attachments[0][0] == sample.name
    rec, errs = try_parse_pre_voyage(filename=mail.attachments[0][0], data=mail.attachments[0][1])
    assert rec and not errs, (rec, errs)
    print("mail_inbox self-check ok")
