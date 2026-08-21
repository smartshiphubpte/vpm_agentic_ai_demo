"""Immediate stdout progress — use when SessionState is not in scope."""

from __future__ import annotations

from datetime import datetime, timezone


def fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(seconds, 60.0)
    return f"{int(m)}m{s:04.1f}s"


def progress(agent: str, msg: str, *, phase: str = "", elapsed_s: float | None = None) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    extra = f" phase={phase}" if phase else ""
    timing = f" elapsed={fmt_elapsed(elapsed_s)}" if elapsed_s is not None else ""
    print(f"[{ts}] [{agent}]{extra} {msg}{timing}", flush=True)
