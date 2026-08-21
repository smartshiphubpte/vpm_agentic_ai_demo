"""Ingest microservice: folder poll + IMAP poll (pickup/validation in this package)."""

from __future__ import annotations

import time

from vpm_agents.config import settings
from vpm_agents.core.state import SessionState
from vpm_agents.poll_loop import poll_forever
from vpm_agents.tools import get_backend
from vpm_agents.tools.agent_log import progress
from vpm_agents.tools.voyage_registry import VoyageRegistry

from inbox_agent.mail import mail_enabled
from inbox_agent.watch import InboxWatchAgent, MailInboxAgent


def run_forever() -> None:
    backend = get_backend()
    registry = VoyageRegistry()
    agent = InboxWatchAgent(backend, registry, flow_name=settings.daemon_flow)
    mail_agent = MailInboxAgent(backend, registry)
    mail_every = max(1.0, settings.mail_poll_seconds)
    last_mail = 0.0
    if mail_enabled():
        progress("ingest", f"mail poll={mail_every}s (IMAP); folder poll={settings.inbox_poll_seconds}s")

    def tick() -> None:
        nonlocal last_mail
        agent.run(SessionState(), enqueue=True)
        if not mail_enabled():
            return
        now = time.monotonic()
        if last_mail and (now - last_mail) < mail_every:
            return
        mail_agent.run(SessionState())
        last_mail = time.monotonic()

    poll_forever("ingest", settings.inbox_poll_seconds, tick)
