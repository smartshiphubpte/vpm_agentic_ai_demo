"""Persist active voyages keyed by voyage_number (JSON file)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from vpm_agents.config import settings

# Trailing laden/ballast leg tag: …L, …B, …-L1, …-B2 (kept as part of voyage identity)
_LEG_TAG = re.compile(r"[-_]?[LB]\d*$", re.IGNORECASE)


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

    def save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def upsert(self, voyage_number: str, record: dict[str, Any]) -> dict[str, Any]:
        canon = normalize_voyage_number(voyage_number)
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
        self.save()
        return existing

    def get(self, voyage_number: str) -> dict[str, Any] | None:
        rec, _ = self.find_voyage(voyage_number)
        return rec

    def find_voyage(self, voyage_number: str) -> tuple[dict[str, Any] | None, str | None]:
        """Exact match on normalized key — L*/B* tags never cross-match."""
        if not voyage_number:
            return None, None
        needle = normalize_voyage_number(voyage_number)
        voyages = self._data.get("voyages", {})
        if needle in voyages:
            return voyages[needle], needle
        for key, rec in voyages.items():
            if normalize_voyage_number(key) == needle:
                return rec, key
        return None, None

    def all(self) -> dict[str, Any]:
        return dict(self._data.get("voyages", {}))

    def is_noon_processed(self, noon_id: str) -> bool:
        return noon_id in self._data.get("processed_noon_ids", [])

    def mark_noon_processed(self, noon_id: str) -> None:
        ids = self._data.setdefault("processed_noon_ids", [])
        if noon_id not in ids:
            ids.append(noon_id)
            self.save()


if __name__ == "__main__":
    assert normalize_voyage_number("V-2602-02-L1") == "V2602-02-L1"
    assert normalize_voyage_number("2602-02-B1") == "V2602-02-B1"
    assert normalize_voyage_number("2611L") == "V2611L"
    assert voyage_leg_tag("V2602-02-L1") == "L1"
    assert voyage_leg_tag("V2602-02-B2") == "B2"
    assert voyage_leg_tag("V2611L") == "L"
    assert voyage_leg_tag("V2611B") == "B"
    assert voyage_leg_tag("V2611") is None

    r = VoyageRegistry.__new__(VoyageRegistry)
    r._data = {
        "voyages": {
            "V2602-02-L1": {"voyage_number": "V2602-02-L1"},
            "V2602-02-B1": {"voyage_number": "V2602-02-B1"},
            "V2611L": {"voyage_number": "V2611L"},
        },
        "processed_noon_ids": [],
    }
    assert r.find_voyage("V-2602-02-L1")[0] is not None
    assert r.find_voyage("V-2602-02-L1")[1] == "V2602-02-L1"
    assert r.find_voyage("V2602-02-B1")[1] == "V2602-02-B1"
    # L* / B* / untagged stay separate
    assert r.find_voyage("V2602-02-L1")[1] != r.find_voyage("V2602-02-B1")[1]
    assert r.find_voyage("V2611")[0] is None
    assert r.find_voyage("V2611B")[0] is None
    assert r.find_voyage("V2611L")[1] == "V2611L"
    print("voyage_registry self-check ok")
