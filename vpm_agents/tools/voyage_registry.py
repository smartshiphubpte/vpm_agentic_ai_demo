"""Persist active voyages keyed by voyage_number (JSON file)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vpm_agents.config import settings


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
        voyages = self._data.setdefault("voyages", {})
        existing = voyages.get(voyage_number, {})
        existing.update(record)
        existing["voyage_number"] = voyage_number
        voyages[voyage_number] = existing
        self.save()
        return existing

    def get(self, voyage_number: str) -> dict[str, Any] | None:
        rec, _ = self.find_voyage(voyage_number)
        return rec

    def find_voyage(self, voyage_number: str) -> tuple[dict[str, Any] | None, str | None]:
        """Match V2611L ↔ 2611L ↔ registry keys."""
        if not voyage_number:
            return None, None
        voyages = self._data.get("voyages", {})
        for key in (voyage_number, voyage_number.upper(), f"V{voyage_number.lstrip('Vv')}"):
            if key in voyages:
                return voyages[key], key
        needle = voyage_number.upper().lstrip("V")
        for key, rec in voyages.items():
            if key.upper().lstrip("V") == needle:
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
