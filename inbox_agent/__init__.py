"""Inbox ingest: folder/IMAP pickup + pre-voyage validation.

Package layout:
  parse.py   — Excel/CSV classify + field validation
  mail.py    — IMAP fetch / reject-forward
  watch.py   — InboxWatchAgent + MailInboxAgent
  ingest.py  — accept validated record → job bus
  service.py — ingest microservice loop
"""

__all__ = ["InboxWatchAgent", "MailInboxAgent", "PreVoyageIngestAgent"]


def __getattr__(name: str):
    if name == "PreVoyageIngestAgent":
        from inbox_agent.ingest import PreVoyageIngestAgent

        return PreVoyageIngestAgent
    if name in ("InboxWatchAgent", "MailInboxAgent"):
        from inbox_agent.watch import InboxWatchAgent, MailInboxAgent

        return InboxWatchAgent if name == "InboxWatchAgent" else MailInboxAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
