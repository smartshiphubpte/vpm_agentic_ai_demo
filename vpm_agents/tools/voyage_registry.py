"""Persist active voyages keyed by voyage_number (JSON file)."""

from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from vpm_agents.config import settings

try:
    import fcntl
except ImportError:  # Windows — thread lock only
    fcntl = None  # type: ignore[assignment]

# Trailing laden/ballast leg tag: …L, …B, …-L1, …-B2 (kept as part of voyage identity)
_LEG_TAG = re.compile(r"[-_]?[LB]\d*$", re.IGNORECASE)

# One lock per registry file so parallel daemon workers don't clobber JSON.
_file_locks: dict[str, threading.RLock] = {}
_file_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _file_locks_guard:
        lock = _file_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _file_locks[key] = lock
        return lock


@contextmanager
def _registry_guard(path: Path) -> Iterator[None]:
    """Thread lock + flock so containers sharing the JSON don't clobber it."""
    thread = _lock_for(path)
    with thread:
        lock_path = path.with_name(path.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a", encoding="utf-8") as fh:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def voyage_is_closed(rec: dict[str, Any] | None) -> bool:
    """True after EOV has started or last noon was an Arrival — stop further noon/route-opt."""
    if not rec:
        return False
    if rec.get("eov_status") in ("done", "running"):
        return True
    last = rec.get("last_noon") or {}
    return "arrival" in str(last.get("report_type") or "").lower()


def compact_voyage_number(v: str) -> str:
    """Normalize after collapsing whitespace so DB '2611 L' matches registry V2611L."""
    return normalize_voyage_number(re.sub(r"\s+", "", str(v or "").strip()))


def normalize_voyage_number(v: str) -> str:
    """Canonical voyage key. Preserves L*/B* leg tags as distinct voyages.

    V-2602-02-L1 ↔ V2602-02-L1 (same)
    V2602-02-L1 ≠ V2602-02-B1 ≠ V2602-02 (separate)
    V2611L ≠ V2611B ≠ V2611 (separate)
    """
    v = str(v).strip().upper()
    if not v:
        return v
    if not v.startswith("V"):
        v = f"V{v}"
    # only the hyphen right after V (noon Excel often uses V-2602…)
    if len(v) > 1 and v[1] == "-":
        v = "V" + v[2:]
    return v


def voyage_leg_tag(voyage_number: str) -> str | None:
    """Return L / L1 / B2 etc. when present; None if untagged."""
    v = normalize_voyage_number(voyage_number)
    body = v[1:] if v.startswith("V") else v
    m = _LEG_TAG.search(body)
    if not m:
        return None
    tag = m.group(0).lstrip("-_").upper()
    return tag or None


def is_valid_voyage_number(voyage_number: str) -> bool:
    """V + id; optional trailing L/B with or without a digit (V2610-02, V2610-02-L1, V2611L)."""
    key = compact_voyage_number(voyage_number)
    if not key.startswith("V") or len(key) < 2:
        return False
    body = key[1:]
    leg = _LEG_TAG.search(body)
    if leg:
        if leg.end() != len(body):
            return False
        body = body[: leg.start()]
    elif re.search(r"[-_][A-Z]\d*$", body):
        return False
    return bool(body) and bool(re.fullmatch(r"[0-9A-Z-]+", body))


class VoyageRegistry:
    def __init__(self, path: Path | None = None):
        raw = Path(path or settings.registry_path)
        if raw.exists() and raw.is_dir():
            raw = raw / "voyage_registry.json"
        elif raw.suffix.lower() != ".json":
            raw = raw / "voyage_registry.json" if not raw.suffix else raw
        self.path = raw
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"voyages": {}, "processed_noon_ids": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data.setdefault("processed_noon_ids", [])
        data.setdefault("voyages", {})
        return data

    def _write(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")

    def save(self) -> None:
        with _registry_guard(self.path):
            self._write()

    def _reload(self) -> None:
        self._data = self._load()

    def upsert(self, voyage_number: str, record: dict[str, Any]) -> dict[str, Any]:
        with _registry_guard(self.path):
            self._reload()
            canon = compact_voyage_number(voyage_number)
            voyages = self._data.setdefault("voyages", {})
            existing: dict[str, Any] = {}
            for key in list(voyages):
                if normalize_voyage_number(key) == canon:
                    existing = voyages.pop(key)
                    break
            existing.update(record)
            existing["voyage_number"] = canon
            if tag := voyage_leg_tag(canon):
                existing["leg_tag"] = tag
            voyages[canon] = existing
            self._write()
            return existing

    def get(self, voyage_number: str) -> dict[str, Any] | None:
        rec, _ = self.find_voyage(voyage_number)
        return rec

    def find_voyage(self, voyage_number: str) -> tuple[dict[str, Any] | None, str | None]:
        """Exact match on normalized key — L*/B* tags never cross-match."""
        if not voyage_number:
            return None, None
        with _registry_guard(self.path):
            self._reload()
            needle = compact_voyage_number(voyage_number)
            voyages = self._data.get("voyages", {})
            if needle in voyages:
                return voyages[needle], needle
            for key, rec in voyages.items():
                if compact_voyage_number(key) == needle:
                    return rec, key
            return None, None

    def all(self) -> dict[str, Any]:
        with _registry_guard(self.path):
            self._reload()
            return dict(self._data.get("voyages", {}))

    def is_noon_processed(self, noon_id: str) -> bool:
        with _registry_guard(self.path):
            self._reload()
            return noon_id in self._data.get("processed_noon_ids", [])

    def mark_noon_processed(self, noon_id: str) -> None:
        with _registry_guard(self.path):
            self._reload()
            ids = self._data.setdefault("processed_noon_ids", [])
            if noon_id not in ids:
                ids.append(noon_id)
                self._write()

    def forget_noon_ids(self, noon_ids: list[str]) -> None:
        drop = {i for i in noon_ids if i}
        if not drop:
            return
        with _registry_guard(self.path):
            self._reload()
            ids = self._data.setdefault("processed_noon_ids", [])
            kept = [x for x in ids if x not in drop]
            if len(kept) != len(ids):
                self._data["processed_noon_ids"] = kept
                self._write()

    def forget_voyage_noons(self, voyage_number: str) -> None:
        """Drop processed-noon marks for this voyage so a re-ingest can replay DB rows."""
        rec = self.get(voyage_number) or {}
        ids = [h.get("noon_id") for h in rec.get("noon_history") or [] if h.get("noon_id")]
        last = rec.get("last_noon") or {}
        if last.get("noon_id"):
            ids.append(last["noon_id"])
        self.forget_noon_ids(ids)


if __name__ == "__main__":
    assert normalize_voyage_number("V-2602-02-L1") == "V2602-02-L1"
    assert normalize_voyage_number("2602-02-B1") == "V2602-02-B1"
    assert normalize_voyage_number("2611L") == "V2611L"
    assert compact_voyage_number("2611 L") == "V2611L"
    assert compact_voyage_number("V2611L") == compact_voyage_number("2611L")
    assert voyage_leg_tag("V2602-02-L1") == "L1"
    assert voyage_leg_tag("V2602-02-B2") == "B2"
    assert voyage_leg_tag("V2611L") == "L"
    assert voyage_leg_tag("V2611B") == "B"
    assert voyage_leg_tag("V2611") is None
    assert is_valid_voyage_number("V2610-02")
    assert is_valid_voyage_number("V2610-02-L1")
    assert is_valid_voyage_number("V2610-02-L")
    assert is_valid_voyage_number("V2611L")
    assert is_valid_voyage_number("2611 B")
    assert not is_valid_voyage_number("V2610-02-X1")

    r = VoyageRegistry.__new__(VoyageRegistry)
    r.path = Path("/tmp/voyage_registry_selfcheck.json")
    r._data = {
        "voyages": {
            "V2602-02-L1": {"voyage_number": "V2602-02-L1"},
            "V2602-02-B1": {"voyage_number": "V2602-02-B1"},
            "V2611L": {"voyage_number": "V2611L"},
        },
        "processed_noon_ids": [],
    }
    r._reload = lambda: None  # type: ignore[method-assign]
    assert r.find_voyage("V-2602-02-L1")[0] is not None
    assert r.find_voyage("V-2602-02-L1")[1] == "V2602-02-L1"
    assert r.find_voyage("2611 L")[1] == "V2611L"
    assert r.find_voyage("V2602-02-B1")[1] == "V2602-02-B1"
    # L* / B* / untagged stay separate
    assert r.find_voyage("V2602-02-L1")[1] != r.find_voyage("V2602-02-B1")[1]
    assert r.find_voyage("V2611")[0] is None
    assert r.find_voyage("V2611B")[0] is None
    assert r.find_voyage("V2611L")[1] == "V2611L"
    assert voyage_is_closed({"eov_status": "done"}) is True
    assert voyage_is_closed({"last_noon": {"report_type": "Arrival Report"}}) is True
    assert voyage_is_closed({"last_noon": {"report_type": "Noon Report"}}) is False
    print("voyage_registry self-check ok")
