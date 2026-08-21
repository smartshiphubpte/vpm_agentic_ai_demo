"""Minimal stdout logging for prevoyage_db service."""

from __future__ import annotations

from datetime import datetime, timezone


def log(tag: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    print(f"[{ts}] [PreVoyageDB:{tag}] {msg}", flush=True)
