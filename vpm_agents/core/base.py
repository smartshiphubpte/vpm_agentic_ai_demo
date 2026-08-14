"""Minimal agent/tool primitives — ReAct-capable when LLM is present."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from vpm_agents.core.spec_loader import AgentSpec, load_agent_spec
from vpm_agents.core.state import SessionState


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "data": self.data, "error": self.error}


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., ToolResult]
    parameters: dict[str, str] = field(default_factory=dict)

    def run(self, **kwargs: Any) -> ToolResult:
        try:
            return self.fn(**kwargs)
        except Exception as e:  # ponytail: surface tool failures as results, not crashes
            return ToolResult(ok=False, error=str(e))


class Agent:
    """Specialist agent with a fixed tool belt and a deterministic run().

    Behavior brief + knobs live in `agents/specs/{name}.md` — edit the MD, not
    scattered Python constants, to change tasks/defaults.
    """

    name: str = "Agent"
    description: str = ""

    def __init__(self, backend: Any, spec: AgentSpec | None = None):
        self.backend = backend
        self.spec = spec or load_agent_spec(self.name)
        self.description = self.spec.description or self.description
        self.tools: dict[str, Tool] = {t.name: t for t in self.build_tools()}

    def build_tools(self) -> list[Tool]:
        return []

    def run(self, state: SessionState, **kwargs: Any) -> SessionState:
        raise NotImplementedError

    def call(self, tool_name: str, **kwargs: Any) -> ToolResult:
        tool = self.tools.get(tool_name)
        if not tool:
            return ToolResult(ok=False, error=f"unknown tool: {tool_name}")
        return tool.run(**kwargs)

    def tool_catalog(self) -> list[dict[str, str]]:
        return [
            {"name": t.name, "description": t.description, **{"params": str(t.parameters)}}
            for t in self.tools.values()
        ]
