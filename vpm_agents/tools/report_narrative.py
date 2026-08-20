"""LLM fill for templated report narrative blocks (plain text, fallback if no key)."""

from __future__ import annotations

import json
from typing import Any

from vpm_agents.core.llm import chat


def llm_section(instruction: str, facts: Any, fallback: str, *, max_chars: int = 8000) -> str:
    """Write one report subsection from facts. Returns fallback when LLM is unavailable."""
    payload = facts if isinstance(facts, str) else json.dumps(facts, default=str)
    if len(payload) > max_chars:
        payload = payload[:max_chars] + "…"
    text = chat(
        [
            {
                "role": "system",
                "content": (
                    "You write marine operations report sections for SmartShip Hub. "
                    "Plain text only. No markdown headings, no code fences. "
                    "Use '  • ' bullets when listing. Stay factual; do not invent numbers. "
                    + instruction
                ),
            },
            {"role": "user", "content": payload},
        ],
        temperature=0.3,
    )
    if text:
        return text.strip()
    return fallback


def compact_wx_facts(rows: list[dict[str, Any]], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Downsampled time-series stats for LLM prompts (not the full waypoint table)."""
    def _nums(key: str) -> list[float]:
        return [float(r[key]) for r in rows if r.get(key) is not None]

    winds, waves, swells = _nums("wind_kn"), _nums("wave_m"), _nums("swell_m")
    bfs, press, curr = _nums("beaufort"), _nums("pressure_hpa"), _nums("current_factor_kn")
    step = max(1, len(rows) // 12) if rows else 1
    sample = [
        {
            "t": r.get("date_utc"),
            "bf": r.get("beaufort"),
            "wind_kn": r.get("wind_kn"),
            "wave_m": r.get("wave_m"),
            "swell_m": r.get("swell_m"),
            "hpa": r.get("pressure_hpa"),
            "curr": r.get("current_factor_kn"),
            "highlight": r.get("highlight"),
        }
        for r in rows[::step]
    ]
    out: dict[str, Any] = {
        "n_points": len(rows),
        "highlight_count": sum(1 for r in rows if r.get("highlight")),
        "wind_kn": {"min": min(winds) if winds else None, "max": max(winds) if winds else None},
        "beaufort": {"min": min(bfs) if bfs else None, "max": max(bfs) if bfs else None},
        "wave_m": {"min": min(waves) if waves else None, "max": max(waves) if waves else None},
        "swell_m": {"min": min(swells) if swells else None, "max": max(swells) if swells else None},
        "pressure_hpa": {
            "first": press[0] if press else None,
            "last": press[-1] if press else None,
            "delta": (press[-1] - press[0]) if len(press) >= 2 else None,
        },
        "current_factor_kn": {
            "min": min(curr) if curr else None,
            "max": max(curr) if curr else None,
        },
        "window": {
            "start": rows[0].get("date_utc") if rows else None,
            "end": rows[-1].get("date_utc") if rows else None,
        },
        "series_sample": sample,
    }
    if extra:
        out.update(extra)
    return out
