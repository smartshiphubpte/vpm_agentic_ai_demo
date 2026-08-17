"""Fill report templates from VPM_TEMPLATES_DIR (str.format, zero extra deps)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vpm_agents.config import ROOT, settings


def templates_dir() -> Path:
    d = Path(settings.templates_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_template(name: str) -> str:
    path = templates_dir() / name
    if not path.is_file():
        fallback = ROOT / "templates" / name
        if fallback.is_file():
            return fallback.read_text(encoding="utf-8")
        raise FileNotFoundError(f"template missing: {path} (also tried {fallback})")
    return path.read_text(encoding="utf-8")


def fill_template(name: str, ctx: dict[str, Any]) -> str:
    return load_template(name).format(**ctx)


def format_waypoints(points: list[dict], limit: int = 40) -> str:
    lines = []
    for p in points[:limit]:
        lines.append(f"  #{p['seq']:03d}  {p['lat']:.4f},{p['lon']:.4f}  ETA {p['eta_utc']}")
    if len(points) > limit:
        lines.append(f"  ... ({len(points) - limit} more)")
    return "\n".join(lines) if lines else "  (none)"


def write_report(
    out_dir: Path,
    filename: str,
    body: str,
    *,
    email_pdf: bool = False,
    voyage_number: str = "",
) -> Path:
    """Write .txt report. If email_pdf, also write a PDF sibling (auto-emailed)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    path.write_text(body, encoding="utf-8")
    if email_pdf:
        write_text_pdf(
            out_dir,
            Path(filename).with_suffix(".pdf").name,
            body,
            voyage_number=voyage_number,
        )
    return path


_ASCII_FALLBACK = str.maketrans(
    {
        "═": "=",
        "─": "-",
        "→": "->",
        "•": "*",
        "—": "-",
        "≥": ">=",
        "📍": "*",
    }
)


def _mono_font_path() -> Path | None:
    for candidate in (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSansMono.ttf"),
        Path("/usr/local/share/fonts/DejaVuSansMono.ttf"),
        Path("/System/Library/Fonts/Supplemental/Courier New.ttf"),
        Path("C:/Windows/Fonts/consola.ttf"),
    ):
        if candidate.is_file():
            return candidate
    return None


def write_text_pdf(
    out_dir: Path, filename: str, body: str, *, voyage_number: str = ""
) -> Path:
    """Render preformatted report text to landscape A4 PDF (monospace, multi-page)."""
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_margins(8, 8, 8)
    pdf.set_auto_page_break(auto=True, margin=8)
    pdf.add_page()

    font_path = _mono_font_path()
    if font_path:
        pdf.add_font("Mono", "", str(font_path))
        pdf.set_font("Mono", size=5.5)
        text = body
    else:
        # ponytail: ASCII fallback when no TTF mono font on host; bundle DejaVu to drop this path
        pdf.set_font("Courier", size=5.5)
        text = body.translate(_ASCII_FALLBACK)

    line_height = 3.2
    for line in text.splitlines():
        pdf.cell(0, line_height, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.output(str(path))
    from vpm_agents.tools.report_email import send_report_pdf

    send_report_pdf(path, voyage_number=voyage_number or out_dir.name)
    return path
