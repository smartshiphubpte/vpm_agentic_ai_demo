"""Folder drop + IMAP pickup. Validation is inbox_agent.parse; accept is inbox_agent.ingest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vpm_agents.config import settings
from vpm_agents.core.base import Agent, Tool, ToolResult
from vpm_agents.core.state import SessionState
from vpm_agents.tools.folder_layout import incoming_dir
from vpm_agents.tools.voyage_registry import VoyageRegistry

from inbox_agent.ingest import PreVoyageIngestAgent
from inbox_agent.parse import (
    archive_inbox_file,
    classify_inbox_file,
    list_inbox,
    relocate_inbox_file,
)


class InboxWatchAgent(Agent):
    name = "InboxWatchAgent"

    def __init__(
        self,
        backend: Any,
        registry: VoyageRegistry | None = None,
        pre_agent: PreVoyageIngestAgent | None = None,
        noon_agent: Any | None = None,
        flow_name: str | None = None,
    ):
        self.registry = registry or VoyageRegistry()
        self.pre_agent = pre_agent or PreVoyageIngestAgent(backend, self.registry)
        self.noon_agent = noon_agent
        self.flow_name = flow_name or settings.daemon_flow
        super().__init__(backend)

    def build_tools(self) -> list[Tool]:
        return [Tool("list_inbox", "List new inbox files", self._list)]

    def _list(self) -> ToolResult:
        return ToolResult(ok=True, data=[str(p) for p in list_inbox(settings.inbox_dir)])

    def _dispatch_one(
        self, kind: str, path: Path, *, enqueue_after_ingest: bool = False
    ) -> SessionState:
        state = SessionState()
        state.note(self.name, f"seen {path.name} kind={kind}")
        if kind == "pre_voyage":
            from vpm_agents.core.flow_runner import PreVoyageFlowRunner

            runner = PreVoyageFlowRunner(self.backend, self.registry, self.flow_name)
            state = runner.run(state, path, enqueue_after_ingest=enqueue_after_ingest)
        elif kind == "noon_report":
            dest = relocate_inbox_file(path, incoming_dir(settings.noon_inbox_dir))
            state.note(self.name, f"{path.name} is noon Excel → noon/incoming/")
        else:
            archive_inbox_file(path, "failed")
        return state

    def run(self, state: SessionState, *, enqueue: bool = False) -> SessionState:
        files = list_inbox(settings.inbox_dir)
        if not files:
            state.note(self.name, "inbox empty", quiet=True)
            return state

        ordered = [(classify_inbox_file(p), p) for p in files]
        ordered.sort(key=lambda t: 0 if t[0] == "pre_voyage" else 1)

        if enqueue:
            from vpm_agents.tools.daemon_jobs import LANE_INGEST, submit_job

            for kind, path in ordered:
                if kind != "pre_voyage":
                    st = self._dispatch_one(kind, path)
                    state.log.extend(st.log)
                    continue
                key = f"inbox:{path.resolve()}"
                fut = submit_job(
                    key,
                    lambda k=kind, p=path: self._dispatch_one(k, p, enqueue_after_ingest=True),
                    lane=LANE_INGEST,
                )
                if fut is not None:
                    state.note(self.name, f"queued {path.name} kind={kind}")
            state.phase = self.spec.get("phase", "inbox_scanned")
            return state

        for kind, path in ordered:
            st = self._dispatch_one(kind, path)
            state.log.extend(st.log)
            state.artifacts.update(st.artifacts)
            if st.voyage_number:
                state.voyage_number = st.voyage_number
        state.phase = self.spec.get("phase", "inbox_scanned")
        return state


class MailInboxAgent(Agent):
    """UNSEEN IMAP mail → parse attachments in memory → prevoyage_db job or reject-forward."""

    name = "MailInboxAgent"

    def __init__(self, backend: Any, registry: VoyageRegistry | None = None):
        self.registry = registry or VoyageRegistry()
        self.pre_agent = PreVoyageIngestAgent(backend, self.registry)
        super().__init__(backend)

    def build_tools(self) -> list[Tool]:
        return []

    def run(self, state: SessionState) -> SessionState:
        from inbox_agent.mail import (
            fetch_unseen,
            forward_rejection,
            mail_enabled,
            mark_seen,
            try_attachments,
        )

        if not mail_enabled():
            state.note(self.name, "IMAP unset — skip", quiet=True)
            return state
        try:
            mails = fetch_unseen()
        except Exception as e:
            state.note(self.name, f"IMAP fetch failed: {e}")
            return state
        if not mails:
            state.note(self.name, "no unseen mail", quiet=True)
            return state
        for mail in mails:
            recs, issues = try_attachments(mail)
            for rec in recs:
                try:
                    self.pre_agent.ingest_parsed(state, rec, persist_files=False)
                except Exception as e:
                    issues.append(f"{rec.get('source_file')}: ingest failed: {e}")
            if issues:
                try:
                    forward_rejection(mail, issues, accepted=len(recs))
                    state.note(
                        self.name,
                        f"uid={mail.uid} rejected → {settings.mail_reject_to} ({len(issues)} issue(s))",
                    )
                except Exception as e:
                    state.note(self.name, f"uid={mail.uid} reject-forward failed: {e}")
                    continue
            else:
                state.note(self.name, f"uid={mail.uid} accepted {len(recs)} attachment(s)")
            try:
                mark_seen(mail.uid)
            except Exception as e:
                state.note(self.name, f"uid={mail.uid} mark seen failed: {e}")
        return state
