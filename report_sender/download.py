"""Download report PDFs from HTTPS or gs:// links."""

from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx


def _gs_to_https(url: str) -> str | None:
    """gs://bucket/object → public HTTPS URL (works for public objects only)."""
    if not url.startswith("gs://"):
        return None
    rest = url[5:]
    if "/" not in rest:
        return None
    bucket, obj = rest.split("/", 1)
    return f"https://storage.googleapis.com/{bucket}/{obj}"


def download_report(link: str, *, dest_dir: Path | None = None) -> Path | None:
    """Fetch PDF bytes from a Google Storage / HTTPS link. Returns local path or None."""
    link = (link or "").strip()
    if not link:
        return None
    fetch_url = link
    if link.startswith("gs://"):
        https = _gs_to_https(link)
        if not https:
            return None
        fetch_url = https
    if not fetch_url.startswith(("http://", "https://")):
        return None
    name = Path(urlparse(fetch_url).path).name or "report.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    out_dir = dest_dir or Path(tempfile.gettempdir()) / "vpm_report_sender"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / name
    try:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            r = client.get(fetch_url)
            r.raise_for_status()
            dest.write_bytes(r.content)
        return dest if dest.is_file() and dest.stat().st_size > 0 else None
    except Exception:
        if dest.is_file():
            dest.unlink(missing_ok=True)
        return None
