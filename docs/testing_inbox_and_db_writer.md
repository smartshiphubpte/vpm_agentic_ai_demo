# Testing env: email ingest + Excel → Postgres

Run **only** the IMAP ingest service and the pre-voyage DB writer. Skip weather, route-opt, noon, storm, port-weather, and report-sender.

This slice is for a VM that already has **Postgres** (VoyagePM + client DBs). Ingest does **not** call the backend. It polls IMAP, parses Pre-Dep attachments in memory, and drops a job file. `prevoyage_db` claims that job and upserts `shipping_db.voyages` + `shipping_db.master_routes`.

```text
Email (IMAP UNSEEN) → parse attachment in memory
        → valid: VPM_JOBS_DIR job → prevoyage_db → Postgres
        → invalid: forward original + missing-field list to VPM_MAIL_REJECT_TO
        (attachment is never written to disk)
```

The `ingest` microservice is **IMAP-only** — no folder watch, no registry, no report files. For local folder-drop testing use `python3 scripts/run_daemon.py` instead (do **not** run that alongside `ingest`).

Pickup, validation, IMAP, and ingest accept live in `inbox_agent/`. Other compose services: `prevoyage_db/`, `report_sender/`, `port_weather/`, `noon_agent/`, `weather_agent/`, `routeopt_agent/`, `storm_agent/`.

---

## What to start

| Process | Command | Role |
|---------|---------|------|
| `ingest` | `python3 scripts/run_service.py ingest` | IMAP poll → parse Pre-Dep Excel/CSV in memory → enqueue DB job |
| `prevoyage_db` | `python3 scripts/run_service.py prevoyage_db` | Writes voyage + master route to Postgres |

Docker equivalent (same two services):

```bash
docker compose up --build ingest prevoyage-db
```

---

## Env files

Two files. Postgres passwords stay in `prevoyage_db/.env` only. Copy the examples, then set **only** the keys below — leave weather / noon / storm / route-opt / LLM / report-sender vars empty.

```bash
cp .env.example .env
cp prevoyage_db/.env.example prevoyage_db/.env
```

### Minimum set (email → Postgres)

| File | Variable | Why |
|------|----------|-----|
| `.env` | `VPM_MODE=mock` | Ingest does not call `voyagepm_be` |
| `.env` | `VPM_TENANT` | Tags the job (must match a tenant in `PREVOYAGE_DB_TENANTS`, lowercase) |
| `.env` | `VPM_JOBS_DIR` | Shared job bus with the writer |
| `.env` | `VPM_MAIL_EMAIL` | Mailbox address (iPowered: full address is the IMAP user) |
| `.env` | `VPM_MAIL_PASSWORD` | Mailbox password — **required**; ingest exits without it |
| `.env` | `VPM_MAIL_REJECT_TO` | Where invalid Pre-Dep mail is forwarded |
| `prevoyage_db/.env` | `VPM_JOBS_DIR` | **Same absolute path** as root `.env` |
| `prevoyage_db/.env` | `PREVOYAGE_DB_TENANTS` | Comma list of tenant keys, e.g. `orion` |
| `prevoyage_db/.env` | `PREVOYAGE_DB_<TENANT>_VPM_URL` | VoyagePM Postgres (`voyages` + `master_routes`) |
| `prevoyage_db/.env` | `PREVOYAGE_DB_<TENANT>_CLIENT_URL` | Client Postgres (`ship` lookup) |

`<TENANT>` in URL names is the tenant key **uppercased** (`orion` → `PREVOYAGE_DB_ORION_VPM_URL`).

Do **not** set `VPM_EMAIL` / `VPM_PASSWORD` / `VPM_BASE_URL` for this slice — those are VoyagePM backend login, not the mailbox.

Not needed for this slice: `VPM_INBOX_DIR`, `VPM_REGISTRY_PATH`, `VPM_REPORTS_OUT_DIR`, `VPM_TEMPLATES_DIR`, `VPM_DAEMON_FLOW`.

### Optional mail tuning

| File | Variable | Why |
|------|----------|-----|
| `.env` | `VPM_MAIL_POLL_SECONDS` | IMAP poll interval (default `900`) |
| `.env` | `VPM_SMTP_*` | Override only — reject mail normally sends **from** `VPM_MAIL_EMAIL` via the same mailbox SMTP (iPower `smtp.ipower.com`) |

Optional IMAP overrides (only if autodetect is wrong): `VPM_MAIL_IMAP_HOST`, `VPM_MAIL_IMAP_PORT`, `VPM_MAIL_IMAP_SSL`, `VPM_MAIL_IMAP_FOLDER`, `VPM_MAIL_SUBJECT_CONTAINS`.

### Defaults you can leave unset

Writer uses these if omitted: `PREVOYAGE_DB_POLL_SECONDS=2`, `PREVOYAGE_DB_DRY_RUN=false`, `PREVOYAGE_DB_SSLMODE=prefer`, `PREVOYAGE_DB_CONNECT_TIMEOUT=45`, `PREVOYAGE_DB_CONNECT_RETRIES=4`, schemas `shipping_db`, tables `ship` / `voyages` / `master_routes`. Mail poll default is `VPM_MAIL_POLL_SECONDS=900`.

First-pass dry run: `PREVOYAGE_DB_DRY_RUN=true` (maps + vessel lookup, no INSERT).

If you later want ingest to talk to the running backend, set `VPM_MODE=live` plus `VPM_BASE_URL` / `VPM_EMAIL` / `VPM_PASSWORD` / `VPM_COMPANY`. Not needed for Excel → DB.

### Root `.env` (ingest) — copy-paste

```bash
# mock is enough — ingest does not hit voyagepm_be
VPM_MODE=mock

# Must match PREVOYAGE_DB_TENANTS (lowercase)
VPM_TENANT=orion

# Shared job bus with prevoyage_db (same absolute path on both processes)
VPM_JOBS_DIR=/var/vpm/jobs

# IMAP — required for ingest microservice
# iPowered: full address + mailbox password is enough
VPM_MAIL_EMAIL=ops@yourcompany.com
VPM_MAIL_PASSWORD=
VPM_MAIL_REJECT_TO=ops-review@yourcompany.com
VPM_MAIL_POLL_SECONDS=900

# Reject mail is sent FROM VPM_MAIL_EMAIL (same mailbox SMTP). VPM_SMTP_* not required.
# Optional override: VPM_SMTP_HOST=smtp.ipower.com
```

### `prevoyage_db/.env` (writer) — copy-paste

`VPM_JOBS_DIR` here **must be the same path** as in root `.env`.

```bash
PREVOYAGE_DB_POLL_SECONDS=2
PREVOYAGE_DB_DRY_RUN=false
PREVOYAGE_DB_SSLMODE=prefer
PREVOYAGE_DB_CONNECT_TIMEOUT=45
PREVOYAGE_DB_CONNECT_RETRIES=4

VPM_JOBS_DIR=/var/vpm/jobs

PREVOYAGE_DB_VPM_SCHEMA=shipping_db
PREVOYAGE_DB_CLIENT_SCHEMA=shipping_db

PREVOYAGE_DB_TENANTS=orion

# URL-encode special chars in PASS (? → %3F, @ → %40, …)
PREVOYAGE_DB_ORION_VPM_URL=postgresql://USER:PASS@127.0.0.1:5432/voyagepm
PREVOYAGE_DB_ORION_CLIENT_URL=postgresql://USER:PASS@127.0.0.1:5432/orion
PREVOYAGE_DB_ORION_VPM_SCHEMA=shipping_db
PREVOYAGE_DB_ORION_CLIENT_SCHEMA=shipping_db
```

If the tenant key is not `orion`, change `PREVOYAGE_DB_TENANTS`, `VPM_TENANT`, and the `PREVOYAGE_DB_<TENANT>_VPM_URL` / `_CLIENT_URL` names (`PREVOYAGE_DB_ACME_VPM_URL`, …).

Vessel lookup uses the **client** DB (`ship.name` / `mappingname` / `imo`). Voyage rows go to the **voyagepm** DB. The vessel name in the Excel must exist in `shipping_db.ship` or the job fails.

---

## One-time setup on the VM

Python 3.12+ (matches the image). From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

mkdir -p /var/vpm/jobs
```

Compose bind-mounts only `VPM_JOBS_DIR` for `ingest` and `prevoyage-db` (no inbox/registry/reports volumes).

Postgres must be reachable from this VM (`127.0.0.1` if it is local; otherwise the DB host the backend already uses). Allow the VM in `pg_hba.conf` / firewall if the DBs are remote.

---

## Start (two terminals, or systemd)

```bash
cd /path/to/voyagepm_agentic_framework
source .venv/bin/activate

# 1) connectivity (read-only)
python3 scripts/test_prevoyage_db.py check --tenant orion

# 2) writer
python3 scripts/run_service.py prevoyage_db

# 3) ingest (other terminal)
python3 scripts/run_service.py ingest
```

Ingest exits immediately if `VPM_MAIL_EMAIL` + `VPM_MAIL_PASSWORD` are unset.

Writer log on start should include `tenants=orion` and `jobs=/var/vpm/jobs`. If it exits with “no tenants”, the `PREVOYAGE_DB_*_URL` vars did not load (`prevoyage_db/.env` missing or URLs empty).

---

## How to test

### Email

Enable IMAP as above. Send a Pre-Dep `.xlsx` to `VPM_MAIL_EMAIL`. Valid mail is queued for `prevoyage_db`. Invalid mail is forwarded **from** `VPM_MAIL_EMAIL` to `VPM_MAIL_REJECT_TO` with the missing fields listed; the original message is attached as `.eml`. No separate Gmail/`VPM_SMTP_*` is required for iPowered (uses `smtp.ipower.com` with the same password).

Gmail/Workspace: use an app password, IMAP enabled. Microsoft 365: IMAP must be allowed (or this poller will not see the mailbox).

Expect:

| Check | Where |
|-------|--------|
| Job queued | ingest log: `queued prevoyage_db:orion:<VOY>` |
| Job claimed | writer log: `done orion <VOY> → voyage_id=…` |
| DB | `shipping_db.voyages` + `shipping_db.master_routes` for that voyage number |

Optional without the ingest loop:

```bash
python3 scripts/test_prevoyage_db.py --tenant orion lookup "VESSEL NAME"
python3 scripts/test_prevoyage_db.py --tenant orion dry-run /path/to/Pre-Dep.xlsx
python3 scripts/test_mail_ingest_e2e.py
```

---

## If nothing lands in Postgres

1. `VPM_TENANT` unset → ingest skips the job (`VPM_TENANT unset — skip prevoyage_db job`).
2. `VPM_JOBS_DIR` differs between `.env` and `prevoyage_db/.env` → writer never sees the job.
3. Tenant key mismatch (`orion` vs `Orion`) — both sides are lowercased; URLs must use `PREVOYAGE_DB_ORION_…`.
4. Vessel name not in client `ship` table → writer fails the job (see `VPM_JOBS_DIR` failed files / writer log).
5. Ingest container exits on start → `VPM_MAIL_EMAIL` / `VPM_MAIL_PASSWORD` missing.
6. `run_daemon.py` and `ingest` both running → duplicate processing if you also use folder drop locally.

Stop with Ctrl-C. Compose: `docker compose stop ingest prevoyage-db`.
