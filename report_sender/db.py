"""Poll multiple Postgres DBs for pending vpm_report rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from report_sender.config import settings
from report_sender.download import download_report
from report_sender.mailer import log, send_report_pdf


@dataclass(frozen=True)
class PendingReport:
    db_label: str
    report_link: str
    voyage_number: str = ""


def _db_label(url: str) -> str:
    # postgres://user:pass@host:5432/dbname → host/dbname
    try:
        from urllib.parse import urlparse

        p = urlparse(url)
        db = (p.path or "").lstrip("/") or "?"
        host = p.hostname or "?"
        return f"{host}/{db}"
    except Exception:
        return url[:40]


def _connect(url: str):
    try:
        import psycopg
    except ImportError as e:
        raise ImportError("psycopg required for DB mode — pip install 'psycopg[binary]'") from e
    return psycopg.connect(url)


def fetch_pending(url: str) -> list[PendingReport]:
    """Rows where sent IS NULL AND status IS NULL."""
    tbl = settings.report_table
    label = _db_label(url)
    sql = f"""
        SELECT report_link FROM {tbl}
        WHERE sent IS NULL AND status IS NULL
        ORDER BY report_link
    """
    try:
        with _connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
    except Exception as e:
        log("db", f"{label} query failed: {e}")
        return []
    return [PendingReport(label, str(r[0])) for r in rows]


def mark_sent(url: str, report_link: str) -> bool:
    tbl = settings.report_table
    label = _db_label(url)
    try:
        with _connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {tbl} SET sent = TRUE WHERE report_link = %s AND sent IS NULL",
                    (report_link,),
                )
                conn.commit()
                if cur.rowcount < 1:
                    log("db", f"{label} mark_sent no row for {report_link[:60]}…")
                    return False
        return True
    except Exception as e:
        log("db", f"{label} mark_sent failed: {e}")
        return False


def voyage_email(url: str, voyage_number: str) -> str | None:
    """Lookup final recipient for a voyage (used when status=accepted — not first pass)."""
    if not voyage_number:
        return None
    tbl = settings.voyage_email_table
    try:
        with _connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT email_id FROM {tbl} WHERE voyage_number = %s LIMIT 1",
                    (voyage_number,),
                )
                row = cur.fetchone()
        return str(row[0]).strip() if row and row[0] else None
    except Exception as e:
        log("db", f"{_db_label(url)} voyage_email lookup failed: {e}")
        return None


def poll_databases_once() -> int:
    """Pick up pending DB rows, send to review email, mark sent=TRUE."""
    if not settings.db_urls:
        return 0
    sent = 0
    for url in settings.db_urls:
        for row in fetch_pending(url):
            local = download_report(row.report_link)
            if not local:
                log("db", f"{row.db_label} download failed: {row.report_link[:80]}")
                continue
            ok = send_report_pdf(local, voyage_number=row.voyage_number, subject_suffix="(review)")
            local.unlink(missing_ok=True)
            if ok and mark_sent(url, row.report_link):
                log("db", f"{row.db_label} sent + marked sent=TRUE ({row.report_link[:60]}…)")
                sent += 1
    return sent

