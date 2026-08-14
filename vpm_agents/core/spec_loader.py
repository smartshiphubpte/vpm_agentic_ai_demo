"""Load agent behavior specs from Markdown files under agents/specs/.

Each agent has one .md file. Humans edit the MD to change role, tasks, and
machine defaults; agents read those defaults at init.

Format (see agents/specs/_TEMPLATE.md):
  ## Section headings for Role / Objective / Tasks / …
  ## Defaults  → fenced ```json block with knobs used by run()
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SPECS_DIR = Path(__file__).resolve().parents[1] / "agents" / "specs"

_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_JSON_FENCE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class AgentSpec:
    name: str
    path: Path
    body: str
    sections: dict[str, str] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)

    @property
    def role(self) -> str:
        return (self.sections.get("Role") or "").strip()

    @property
    def objective(self) -> str:
        return (self.sections.get("Objective") or "").strip()

    @property
    def description(self) -> str:
        """One-line summary for roster / planner — first non-empty Role line."""
        for line in self.role.splitlines():
            line = line.strip().lstrip("-").strip()
            if line:
                return line
        return self.name

    def get(self, key: str, default: Any = None) -> Any:
        return self.defaults.get(key, default)


def _split_sections(body: str) -> dict[str, str]:
    parts = _HEADING.split(body)
    # parts: [preamble, h1, content1, h2, content2, ...]
    sections: dict[str, str] = {}
    if len(parts) == 1:
        return sections
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        content = parts[i + 1] if i + 1 < len(parts) else ""
        sections[title] = content.strip()
    return sections


def _parse_defaults(sections: dict[str, str], body: str) -> dict[str, Any]:
    blob = sections.get("Defaults") or body
    m = _JSON_FENCE.search(blob)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid Defaults JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("Defaults JSON must be an object")
    return data


def load_agent_spec(name: str, specs_dir: Path | None = None) -> AgentSpec:
    """Load `{name}.md` from the specs directory."""
    root = specs_dir or SPECS_DIR
    path = root / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"agent spec missing: {path}")
    body = path.read_text(encoding="utf-8")
    sections = _split_sections(body)
    defaults = _parse_defaults(sections, body)
    return AgentSpec(name=name, path=path, body=body, sections=sections, defaults=defaults)


def list_spec_names(specs_dir: Path | None = None) -> list[str]:
    root = specs_dir or SPECS_DIR
    return sorted(
        p.stem
        for p in root.glob("*.md")
        if not p.name.startswith("_") and p.stem != "README"
    )
