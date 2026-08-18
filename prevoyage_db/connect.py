"""Postgres connections for prevoyage_db (sslmode + optional search_path)."""

from __future__ import annotations

import re

from prevoyage_db.config import settings

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
    conn = psycopg.connect(make_conninfo("", **params), connect_timeout=15)
    if search_path:
        if not _SCHEMA_RE.match(search_path):
            conn.close()
            raise ValueError(f"unsafe search_path schema: {search_path!r}")
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {search_path}")
    return conn
