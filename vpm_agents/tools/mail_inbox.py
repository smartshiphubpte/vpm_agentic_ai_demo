"""Compatibility shim — IMAP pickup lives in inbox_agent.mail."""

from inbox_agent.mail import *  # noqa: F403
from inbox_agent.mail import (  # noqa: F401
    fetch_unseen,
    forward_rejection,
    mail_enabled,
    mark_seen,
    try_attachments,
)
