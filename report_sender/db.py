"""Poll multiple Postgres DBs for pending vpm_report rows."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from report_sender.config import settings
from report_sender.download import download_report
from report_sender.mailer import log, send_report_pdf

_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


@dataclass(frozen=True)
class PendingReport:
    db_label: str
    report_link: str
    voyage_number: str = ""
    extra: dict[str, str] = field(default_factory=dict)


def _db_label(url: str) -> str:
    # postgres://user:pass@host:5432/dbname → host/dbname
    try:
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


def _row_get(row: dict, *names: str) -> str:
    lower = {re.sub(r"[^a-z0-9]", "", str(k).lower()): v for k, v in row.items()}
    for n in names:
        v = lower.get(re.sub(r"[^a-z0-9]", "", n.lower()))
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def fetch_pending(url: str) -> list[PendingReport]:
    """Rows where sent IS NULL AND status IS NULL."""
    tbl = settings.report_table
    label = _db_label(url)
    sql = f"""
        SELECT * FROM {tbl}
        WHERE sent IS NULL AND status IS NULL
    """
    try:
        with _connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        log("db", f"{label} query failed: {e}")
        return []
    out: list[PendingReport] = []
    for rec in rows:
        link = _row_get(rec, "report_link", "reportLink")
        if not link:
            continue
        voy = _row_get(rec, "voyage_number", "voyageNumber", "voyage_no")
        extra = {
            "vessel_name": _row_get(rec, "vessel_name", "vessel", "vesselName"),
            "vessel_id": _row_get(rec, "vessel_id", "vesselId", "imo"),
            "source_port": _row_get(rec, "source_port", "departure", "departure_port"),
            "dest_port": _row_get(rec, "dest_port", "destination", "arrival_port"),
        }
        out.append(
            PendingReport(
                label,
                link,
                voyage_number=voy,
                extra={k: v for k, v in extra.items() if v},
            )
        )
    return out


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
    """First stored recipient for a voyage."""
    found = voyage_contacts(url, voyage_number)
    return found[0] if found else None


def voyage_contacts(url: str, voyage_number: str) -> list[str]:
    """All emails on vpm_voyage_email for this voyage (comma-separated cells split)."""
    if not voyage_number:
        return []
    tbl = settings.voyage_email_table
    try:
        with _connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT email_id FROM {tbl} WHERE voyage_number = %s",
                    (voyage_number,),
                )
                rows = cur.fetchall()
    except Exception as e:
        log("db", f"{_db_label(url)} voyage_email lookup failed: {e}")
        return []
    out: list[str] = []
    for row in rows:
        raw = str(row[0]).strip() if row and row[0] else ""
        for part in raw.replace(";", ",").split(","):
            e = part.strip()
            if e and "@" in e:
                out.append(e)
    return out


def voyage_details(url: str, voyage_number: str) -> dict[str, str]:
    """Vessel name / ports from the voyages table (same layout as prevoyage_db)."""
    if not voyage_number:
        return {}
    schema, table = settings.voyage_schema, settings.voyage_table
    if not _IDENT.match(schema) or not _IDENT.match(table):
        return {}
    sql = f"""
        SELECT vessel, "voyageNumber", departure, destination, "vesselId"
        FROM "{schema}"."{table}"
        WHERE "voyageNumber" = %s
        ORDER BY id DESC LIMIT 1
    """
    try:
        with _connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (voyage_number,))
                row = cur.fetchone()
    except Exception as e:
        log("db", f"{_db_label(url)} voyage lookup failed: {e}")
        return {}
    if not row:
        return {}
    vessel, voy, dep, dest, vid = row
    return {
        k: str(v).strip()
        for k, v in (
            ("vessel_name", vessel),
            ("voyage_number", voy),
            ("source_port", dep),
            ("dest_port", dest),
            ("vessel_id", vid),
        )
        if v is not None and str(v).strip()
    }


def poll_databases_once() -> int:
    """Pick up pending DB rows, send, mark sent=TRUE."""
    if not settings.db_urls:
        return 0
    sent = 0
    for url in settings.db_urls:
        for row in fetch_pending(url):
            local = download_report(row.report_link)
            if not local:
                log("db", f"{row.db_label} download failed: {row.report_link[:80]}")
                continue
            extra = dict(row.extra)
            bucket = Path(urlparse(row.report_link).path).parts
            report_bucket = bucket[-2] if len(bucket) >= 2 else ""
            ok = send_report_pdf(
                local,
                voyage_number=row.voyage_number,
                vessel_id=extra.get("vessel_id", ""),
                vessel_name=extra.get("vessel_name", ""),
                report_bucket=report_bucket,
                subject_suffix="(review)",
                extra=extra,
            )
            local.unlink(missing_ok=True)
            if ok and mark_sent(url, row.report_link):
                log("db", f"{row.db_label} sent + marked sent=TRUE ({row.report_link[:60]}…)")
                sent += 1
    return sent
