"""Optional LLM helper — rule-based planner works without a key."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from vpm_agents.config import settings


def _flatten_messages(messages: list[dict[str, str]], *, json_mode: bool) -> str:
    parts = [f"{(m.get('role') or 'user').upper()}:\n{m.get('content') or ''}" for m in messages]
    tail = (
        "\n\nReply with the assistant answer only. Do not use tools, do not edit files, "
        "do not list a plan."
    )
    if json_mode:
        tail += " Reply with a single JSON object and nothing else."
    return "\n\n".join(parts) + tail


def _cursor_text(result: Any) -> str:
    if result is None:
        return ""
    raw = getattr(result, "result", None)
    if raw is None:
        text_fn = getattr(result, "text", None)
        raw = text_fn() if callable(text_fn) else text_fn
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        return str(raw.get("text") or raw.get("content") or raw.get("result") or "").strip()
    return str(raw or "").strip()


def _chat_cursor(messages: list[dict[str, str]], *, json_mode: bool, model: str) -> tuple[str | None, str | None]:
    try:
        from cursor_sdk import Agent, AgentOptions, CloudAgentOptions, LocalAgentOptions
    except ImportError:
        return None, "cursor-sdk not installed (pip install cursor-sdk)"

    prompt = _flatten_messages(messages, json_mode=json_mode)
    tmp: Path | None = None
    try:
        if settings.cursor_runtime == "cloud":
            opts = AgentOptions(
                api_key=settings.llm_api_key,
                model=model,
                cloud=CloudAgentOptions(repos=[]),
            )
        else:
            tmp = Path(tempfile.mkdtemp(prefix="vpm_cursor_"))
            opts = AgentOptions(
                api_key=settings.llm_api_key,
                model=model,
                local=LocalAgentOptions(cwd=str(tmp)),
            )
        result = Agent.prompt(prompt, opts)
        if getattr(result, "status", None) == "error":
            return None, f"cursor run error id={getattr(result, 'id', '')}"
        text = _cursor_text(result)
        if not text:
            return None, "empty_model_response"
        return text, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e} [provider=cursor model={model} runtime={settings.cursor_runtime}]"
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def chat_detail(
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    *,
    json_mode: bool = False,
    model: str | None = None,
) -> tuple[str | None, str | None]:
    """Return (assistant text, error reason). error is None on success."""
    if not settings.use_llm:
        return None, "no_api_key"
    resolved_model = model or settings.llm_model
    if settings.effective_llm_provider == "cursor":
        return _chat_cursor(messages, json_mode=json_mode, model=resolved_model)
    try:
        from openai import OpenAI

        kwargs: dict[str, Any] = {"api_key": settings.llm_api_key}
        base = settings.effective_llm_base_url
        if base:
            kwargs["base_url"] = base
        client = OpenAI(**kwargs)
        create_kw: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            create_kw["response_format"] = {"type": "json_object"}
        try:
            resp = client.chat.completions.create(**create_kw)
        except Exception as first_err:
            # ponytail: Gemini OpenAI-compat may reject response_format; retry plain JSON prompt
            if not json_mode or settings.effective_llm_provider != "gemini":
                raise first_err
            create_kw.pop("response_format", None)
            try:
                resp = client.chat.completions.create(**create_kw)
            except Exception as retry_err:
                return None, f"{type(retry_err).__name__}: {retry_err}"
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            return None, "empty_model_response"
        return text, None
    except Exception as e:
        base = settings.effective_llm_base_url or "(default OpenAI)"
        return None, f"{type(e).__name__}: {e} [provider={settings.effective_llm_provider} model={resolved_model} base={base}]"


def chat(
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    *,
    json_mode: bool = False,
    model: str | None = None,
) -> str | None:
    """Return assistant text, or None if no API key / client unavailable."""
    text, _ = chat_detail(messages, temperature, json_mode=json_mode, model=model)
    return text


def plan_goal(goal: str, roster: list[dict[str, Any]]) -> list[str] | None:
    """Ask LLM to pick an ordered list of agent names for a free-form goal."""
    names = ", ".join(a["name"] for a in roster)
    text = chat(
        [
            {
                "role": "system",
                "content": (
                    "You plan VoyagePM multi-agent workflows. "
                    f"Available agents: {names}. "
                    "Reply with a comma-separated list of agent names only, in execution order."
                ),
            },
            {"role": "user", "content": goal},
        ]
    )
    if not text:
        return None
    cleaned = text.replace("\n", ",").replace(" ", "")
    return [p for p in cleaned.split(",") if p]
