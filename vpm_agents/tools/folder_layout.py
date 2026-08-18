"""incoming/ + sent/ (+ failed/) layout for drops and PDFs awaiting email."""

from __future__ import annotations

from pathlib import Path

INCOMING = "incoming"
SENT = "sent"
FAILED = "failed"
_SKIP_DIRS = frozenset({INCOMING, SENT, FAILED, "processed"})


def ensure_drop_dirs(base: Path) -> None:
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    for name in (INCOMING, SENT, FAILED):
        (base / name).mkdir(parents=True, exist_ok=True)


def incoming_dir(base: Path) -> Path:
    d = Path(base) / INCOMING
    d.mkdir(parents=True, exist_ok=True)
    return d


def sent_dir(base: Path) -> Path:
    d = Path(base) / SENT
    d.mkdir(parents=True, exist_ok=True)
    return d


def failed_dir(base: Path) -> Path:
    d = Path(base) / FAILED
    d.mkdir(parents=True, exist_ok=True)
    return d


if __name__ == "__main__":
    import tempfile

    root = Path(tempfile.mkdtemp())
    ensure_drop_dirs(root)
    assert (root / INCOMING).is_dir() and (root / SENT).is_dir()
    p = incoming_dir(root) / "test.pdf"
    p.write_bytes(b"x")
    assert p.is_file()
    print("folder_layout self-check ok")
