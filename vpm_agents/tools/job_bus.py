"""File job bus — services hand off work via a shared volume, not in-process queues.

pending/ → running/ (atomic rename) → done/ | failed/
Safe across containers on the same filesystem.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def jobs_root(root: Path | None = None) -> Path:
    if root is None:
        from vpm_agents.config import settings

        root = Path(settings.jobs_dir)
    root = Path(root)
    for name in ("pending", "running", "done", "failed"):
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        # ponytail: host scripts (uid 1000) + docker (root) share this volume. Upgrade: compose `user: ${UID}`.
        try:
            d.chmod(0o777)
        except OSError:
            pass
    return root


def _safe_name(key: str) -> str:
    return _SAFE.sub("_", (key or "job").strip())[:180] or "job"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue(key: str, payload: dict[str, Any], *, root: Path | None = None) -> bool:
    """Write a pending job. False if that key is already pending or running."""
    root = jobs_root(root)
    name = f"{_safe_name(key)}.json"
    pending = root / "pending" / name
    running = root / "running" / name
    if pending.exists() or running.exists():
        return False
    body = {
        "key": key,
        "created_at": _now(),
        **payload,
    }
    tmp = pending.with_name(f".{pending.stem}.{os.getpid()}.{uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    tmp.replace(pending)
    return True


def claim(*, kind: str | None = None, root: Path | None = None) -> dict[str, Any] | None:
    """Take the oldest pending job (optionally filter by payload kind). None if idle."""
    root = jobs_root(root)
    files = sorted((root / "pending").glob("*.json"), key=lambda p: p.stat().st_mtime)
    for src in files:
        try:
            peek = json.loads(src.read_text(encoding="utf-8"))
        except Exception:
            continue
        if kind and peek.get("kind") != kind:
            continue
        dest = root / "running" / src.name
        try:
            src.rename(dest)
        except FileNotFoundError:
            continue
        except OSError:
            continue
        try:
            job = json.loads(dest.read_text(encoding="utf-8"))
        except Exception:
            dest.replace(root / "failed" / src.name)
            continue
        job["_file"] = dest.name
        job["claimed_at"] = _now()
        dest.write_text(json.dumps(job, indent=2, default=str), encoding="utf-8")
        return job
    return None


def complete(job: dict[str, Any], *, root: Path | None = None) -> None:
    _finish(job, "done", root=root)


def fail(job: dict[str, Any], error: str, *, root: Path | None = None) -> None:
    job = dict(job)
    job["error"] = error
    job["failed_at"] = _now()
    _finish(job, "failed", root=root)


def skip(job: dict[str, Any], reason: str, *, root: Path | None = None) -> None:
    job = dict(job)
    job["skipped"] = reason
    _finish(job, "done", root=root)


def _finish(job: dict[str, Any], bucket: str, *, root: Path | None = None) -> None:
    root = jobs_root(root)
    name = job.get("_file") or f"{_safe_name(str(job.get('key') or 'job'))}.json"
    src = root / "running" / name
    dest = root / bucket / name
    payload = {k: v for k, v in job.items() if k != "_file"}
    payload["finished_at"] = _now()
    if src.is_file():
        src.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        src.replace(dest)
    else:
        dest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def pending_count() -> int:
    return len(list((jobs_root() / "pending").glob("*.json")))


def running_count() -> int:
    return len(list((jobs_root() / "running").glob("*.json")))


def has_open(prefix: str, *, root: Path | None = None) -> bool:
    """True if any pending/running job key starts with prefix (after filename sanitizing)."""
    if not prefix:
        return False
    root = jobs_root(root)
    name_prefix = _safe_name(prefix)
    for bucket in ("pending", "running"):
        for _ in (root / bucket).glob(f"{name_prefix}*.json"):
            return True
    return False


def reclaim_running(*, kind: str | None = None, root: Path | None = None) -> int:
    """Move orphaned running jobs back to pending (container restart left them claimed)."""
    root = jobs_root(root)
    n = 0
    for src in list((root / "running").glob("*.json")):
        try:
            job = json.loads(src.read_text(encoding="utf-8"))
        except Exception:
            continue
        if kind and job.get("kind") != kind:
            continue
        dest = root / "pending" / src.name
        if dest.exists():
            continue
        job.pop("claimed_at", None)
        job.pop("_file", None)
        src.write_text(json.dumps(job, indent=2, default=str), encoding="utf-8")
        src.replace(dest)
        n += 1
    return n


if __name__ == "__main__":
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="vpm_jobs_"))
    assert enqueue(
        "routeopt:VTEST:pre_voyage",
        {"kind": "routeopt", "voyage_number": "VTEST"},
        root=tmp,
    )
    assert enqueue("routeopt:VTEST:pre_voyage", {"kind": "routeopt"}, root=tmp) is False
    job = claim(root=tmp)
    assert job and job["voyage_number"] == "VTEST"
    assert has_open("routeopt:VTEST:", root=tmp)
    assert claim(root=tmp) is None
    complete(job, root=tmp)
    assert has_open("routeopt:VTEST:", root=tmp) is False

    enqueue("routeopt:VTEST:pre_voyage", {"kind": "routeopt"}, root=tmp)
    stuck = claim(kind="routeopt", root=tmp)
    assert stuck and (tmp / "running" / stuck["_file"]).is_file()
    assert reclaim_running(kind="routeopt", root=tmp) == 1
    assert (tmp / "pending" / stuck["_file"]).is_file()
    print("job_bus self-check ok")
