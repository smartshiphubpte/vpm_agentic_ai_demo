"""Report sender main loop — folder watch + multi-DB poll."""

from __future__ import annotations

import signal
import time

from report_sender.config import settings
from report_sender.db import poll_databases_once
from report_sender.mailer import log
from report_sender.folder import poll_folder_once


def _describe() -> None:
    parts = []
    if settings.folder_enabled:
        parts.append("folder=" + ",".join(str(d) for d in settings.inbox_dirs))
    if settings.db_enabled and settings.db_urls:
        parts.append(f"dbs={len(settings.db_urls)}")
    review = (settings.review_email or "").strip() or "(unset)"
    smtp = (settings.smtp_host or "").strip() or "(unset)"
    log("init", f"{' + '.join(parts) or 'nothing enabled'} review={review} smtp={smtp}")


def run_once() -> tuple[int, int]:
    folder_n = poll_folder_once() if settings.folder_enabled else 0
    db_n = poll_databases_once() if settings.db_enabled else 0
    return folder_n, db_n


def run_forever() -> None:
    _describe()
    stop = False

    def _sig(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    folder_interval = max(1.0, settings.poll_seconds)
    db_interval = max(1.0, settings.db_poll_seconds)
    next_folder = 0.0
    next_db = 0.0

    while not stop:
        now = time.monotonic()
        if settings.folder_enabled and now >= next_folder:
            n = poll_folder_once()
            if n:
                log("tick", f"folder sent {n}")
            next_folder = now + folder_interval
        if settings.db_enabled and now >= next_db:
            n = poll_databases_once()
            if n:
                log("tick", f"db sent {n}")
            next_db = now + db_interval
        time.sleep(0.5)

    log("init", "stopped")
