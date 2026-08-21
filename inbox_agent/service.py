"""Ingest microservice: IMAP poll only (pickup/validation in this package)."""

from __future__ import annotations

from vpm_agents.config import settings
from vpm_agents.core.state import SessionState
from vpm_agents.poll_loop import poll_forever
from vpm_agents.tools import get_backend
from vpm_agents.tools.agent_log import progress
from vpm_agents.tools.voyage_registry import VoyageRegistry

from inbox_agent.mail import mail_enabled
from inbox_agent.watch import MailInboxAgent


def run_forever() -> None:
    if not mail_enabled():
        raise SystemExit(
            "ingest requires VPM_MAIL_EMAIL + VPM_MAIL_PASSWORD (IMAP-only; no folder watch)"
        )
    backend = get_backend()
    mail_agent = MailInboxAgent(backend, VoyageRegistry())
    mail_every = max(1.0, settings.mail_poll_seconds)
    progress("ingest", f"mail poll={mail_every}s (IMAP)")

    def tick() -> None:
        mail_agent.run(SessionState())

    poll_forever("ingest", mail_every, tick)
