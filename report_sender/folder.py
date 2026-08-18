"""Watch incoming/ folders for PDFs; move to sent/ after email."""

from __future__ import annotations

import shutil
from pathlib import Path

from report_sender.config import settings
from report_sender.mailer import log, send_report_pdf

# Same layout as vpm_agents.tools.folder_layout (keep report_sender import-light)
INCOMING = "incoming"
SENT = "sent"
_SKIP = frozenset({INCOMING, SENT, "failed", "processed"})


def _ensure_dirs(base: Path) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / INCOMING).mkdir(parents=True, exist_ok=True)
    (base / SENT).mkdir(parents=True, exist_ok=True)


def _pending_in(root: Path) -> list[tuple[Path, Path]]:
    """(pdf, base_dir_for_sent) — only incoming/, never sent/."""
    out: list[tuple[Path, Path]] = []
    if not root.is_dir():
        return out
    _ensure_dirs(root)
    for p in sorted((root / INCOMING).glob("*.pdf")):
        if p.is_file():
            out.append((p, root))
    for sub in sorted(root.iterdir()):
        if not sub.is_dir() or sub.name in _SKIP:
            continue
        _ensure_dirs(sub)
        for p in sorted((sub / INCOMING).glob("*.pdf")):
            if p.is_file():
                out.append((p, sub))
    return out


def _pending_pdfs() -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for root in settings.inbox_dirs:
        pairs.extend(_pending_in(root))
    return pairs


def poll_folder_once() -> int:
    """Send PDFs from incoming/ only; archive to sent/. Returns count sent."""
    sent_n = 0
    for pdf, base in _pending_pdfs():
        voy = base.name if base not in settings.inbox_dirs else ""
        if send_report_pdf(pdf, voyage_number=voy):
            dest_dir = base / SENT
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / pdf.name
            if dest.exists():
                dest = dest_dir / f"{pdf.stem}_{pdf.stat().st_mtime_ns}.pdf"
            shutil.move(str(pdf), str(dest))
            log("folder", f"archived {pdf.name} → {base.name}/{SENT}/")
            sent_n += 1
    return sent_n
