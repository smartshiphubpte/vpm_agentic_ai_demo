"""Poll job bus for prevoyage_db jobs and write to tenant databases."""

from __future__ import annotations

import signal
import time

from prevoyage_db.config import load_tenants, settings
from prevoyage_db.log import log
from prevoyage_db.writer import ingest_prevoyage, ingest_suggested_routes

_TRANSIENT = (
    "timeout expired",
    "connection timeout",
    "could not connect",
    "connection refused",
    "server closed the connection",
    "connection reset",
    "network is unreachable",
    "connection timed out",
    "ssl syscall error",
)


def _transient_db(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(s in msg for s in _TRANSIENT)


def drain_prevoyage_db_once() -> int:
    """Claim at most one prevoyage_db job. Returns 1 if processed, else 0."""
    from vpm_agents.tools import job_bus

    tenants = load_tenants()
    if not tenants:
        return 0

    job = job_bus.claim(kind="suggested_routes", root=settings.jobs_dir)
    if not job:
        job = job_bus.claim(kind="prevoyage_db", root=settings.jobs_dir)
    if not job:
        return 0

    tenant_key = str(job.get("tenant") or "").strip().lower()
    tenant = tenants.get(tenant_key)
    if not tenant:
        job_bus.skip(job, f"unknown tenant {tenant_key!r}", root=settings.jobs_dir)
        log("skip", f"unknown tenant {tenant_key!r} — configured: {', '.join(sorted(tenants))}")
        return 1

    record = job.get("record")
    if not isinstance(record, dict):
        job_bus.fail(job, "missing record dict", root=settings.jobs_dir)
        log("fail", f"{tenant_key} job missing record")
        return 1

    voy = record.get("voyage_number") or job.get("voyage_number") or "?"
    try:
        if job.get("kind") == "suggested_routes":
            result = ingest_suggested_routes(tenant, record)
            job_bus.complete(job, root=settings.jobs_dir)
            log(
                "done",
                f"{tenant_key} {voy} suggested_routes → voyage_id={result.get('voyage_id')} "
                f"ids={result.get('ids')} suggested={result.get('suggested_id')}",
            )
        else:
            result = ingest_prevoyage(tenant, record)
            job_bus.complete(job, root=settings.jobs_dir)
            log("done", f"{tenant_key} {voy} → voyage_id={result['voyage_id']}")
    except Exception as e:
        if _transient_db(e) and job_bus.requeue(
            job, str(e), root=settings.jobs_dir, max_attempts=settings.transient_attempts
        ):
            log("retry", f"{tenant_key} {voy}: {e}")
        else:
            job_bus.fail(job, str(e), root=settings.jobs_dir)
            log("fail", f"{tenant_key} {voy}: {e}")
    return 1


def run_forever() -> None:
    tenants = load_tenants()
    if not tenants:
        log("init", "no tenants — set PREVOYAGE_DB_TENANTS and PREVOYAGE_DB_<TENANT>_VPM_URL / _CLIENT_URL")
        raise SystemExit(2)
    log(
        "init",
        f"tenants={','.join(sorted(tenants))} schema_version={settings.schema_version} "
        f"jobs={settings.jobs_dir} poll={settings.poll_seconds}s",
    )

    stop = False

    def _sig(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    interval = max(1.0, settings.poll_seconds)

    while not stop:
        t0 = time.monotonic()
        try:
            n = drain_prevoyage_db_once()
            if n:
                log("tick", f"processed {n} job(s)")
        except Exception as e:
            log("error", str(e))
        wait = interval - (time.monotonic() - t0)
        if wait > 0 and not stop:
            time.sleep(wait)
    log("init", "stopped")


if __name__ == "__main__":
    run_forever()
