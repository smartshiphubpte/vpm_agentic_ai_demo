"""Watch incoming/ folders for PDFs; move to sent/ after email."""

from __future__ import annotations

import shutil
from pathlib import Path

from report_sender.config import settings
from report_sender.mailer import log, send_report_pdf
from vpm_agents.tools.folder_layout import INCOMING, SENT, report_context_from_path


def _pending_in(root: Path) -> list[tuple[Path, Path, dict[str, str]]]:
    """(pdf, report_dir, context) from any nested incoming/ folder."""
    out: list[tuple[Path, Path, dict[str, str]]] = []
    if not root.is_dir():
        return out
    for incoming in sorted(root.rglob(INCOMING)):
        if not incoming.is_dir():
            continue
        report_dir = incoming.parent
        for p in sorted(incoming.glob("*.pdf")):
            if p.is_file():
                out.append((p, report_dir, report_context_from_path(p, roots=settings.inbox_dirs)))
    return out


def _pending_pdfs() -> list[tuple[Path, Path, dict[str, str]]]:
    pairs: list[tuple[Path, Path, dict[str, str]]] = []
    for root in settings.inbox_dirs:
        pairs.extend(_pending_in(root))
    return pairs


def poll_folder_once() -> int:
    """Send PDFs from incoming/ only; archive to sent/. Returns count sent."""
    sent_n = 0
    for pdf, base, ctx in _pending_pdfs():
        voy = ctx.get("voyage_number") or ""
        bucket = ctx.get("report_bucket") or base.name
        if send_report_pdf(
            pdf,
            voyage_number=voy,
            vessel_id=ctx.get("vessel_id") or "",
            report_bucket=bucket,
        ):
            dest_dir = base / SENT
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / pdf.name
            if dest.exists():
                dest = dest_dir / f"{pdf.stem}_{pdf.stat().st_mtime_ns}.pdf"
            shutil.move(str(pdf), str(dest))
            log("folder", f"archived {pdf.name} → {base}/{SENT}/")
            sent_n += 1
    return sent_n
