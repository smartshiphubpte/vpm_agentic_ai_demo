"""Postgres connections for prevoyage_db (sslmode + optional search_path)."""

from __future__ import annotations

import re
import time

from prevoyage_db.config import settings
from prevoyage_db.log import log

_SCHEMA_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def connect(url: str, *, search_path: str | None = None):
    try:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
    except ImportError as e:
        raise ImportError("psycopg required — pip install 'psycopg[binary]'") from e
    params = conninfo_to_dict(url.strip())
    if settings.sslmode and "sslmode" not in params:
        params["sslmode"] = settings.sslmode
    params.setdefault("keepalives", "1")
    params.setdefault("keepalives_idle", "30")
    params.setdefault("keepalives_interval", "10")
    dsn = make_conninfo("", **params)
    timeout = max(10, int(settings.connect_timeout))
    retries = max(1, int(settings.connect_retries))
    last: Exception | None = None
    conn = None
    for attempt in range(1, retries + 1):
        try:
            conn = psycopg.connect(dsn, connect_timeout=timeout)
            break
        except Exception as e:
            last = e
            if attempt >= retries:
                raise
            log("db", f"connect retry {attempt}/{retries}: {e}")
            time.sleep(min(8, 2 * attempt))
    if conn is None:
        raise last or RuntimeError("db connect failed")
    if search_path:
        if not _SCHEMA_RE.match(search_path):
            conn.close()
            raise ValueError(f"unsafe search_path schema: {search_path!r}")
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {search_path}")
    return conn
