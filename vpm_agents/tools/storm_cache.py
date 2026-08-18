"""Last storm snapshot — StormWatchAgent writes; everyone else only reads."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from vpm_agents.tools.storm_normalize import normalize_active_storms

_lock = threading.Lock()
_storms: list[dict[str, Any]] = []
_fetched_at: str | None = None  # set after a successful poll (including empty)


def remember_storms(storms: list[dict[str, Any]], fetched_at: str) -> None:
    global _storms, _fetched_at
    with _lock:
        _storms = list(storms)
        _fetched_at = fetched_at


def last_storms() -> list[dict[str, Any]]:
    """In-memory snapshot, else newest storms_*.json. Never hits the live APIs."""
    with _lock:
        if _fetched_at is not None:
            return list(_storms)
    loaded = _read_latest_file()
    if loaded is None:
        return []
    storms, fetched_at = loaded
    remember_storms(storms, fetched_at)
    return list(storms)


def last_storms_fetched_at() -> str | None:
    with _lock:
        if _fetched_at is not None:
            return _fetched_at
    loaded = _read_latest_file()
    return loaded[1] if loaded else None


def _read_latest_file() -> tuple[list[dict[str, Any]], str] | None:
    from vpm_agents.config import settings

    out = Path(settings.storm_out_dir)
    if not out.is_dir():
        return None
    files = sorted(out.glob("storms_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        storms = normalize_active_storms(data.get("storms") or [])
        fetched_at = str(data.get("fetched_at") or path.stem)
        return storms, fetched_at
    return None


if __name__ == "__main__":
    remember_storms([{"id": "TEST", "lat": 1.0, "lon": 2.0}], "2026-01-01T00:00:00+00:00")
    got = last_storms()
    assert len(got) == 1 and got[0]["id"] == "TEST"
    assert last_storms_fetched_at() == "2026-01-01T00:00:00+00:00"
    print("storm_cache self-check ok")
