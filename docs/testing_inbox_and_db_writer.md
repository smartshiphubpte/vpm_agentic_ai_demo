# Testing env: inbox ingest + Excel → Postgres

Run **only** the inbox watcher and the pre-voyage DB writer. Skip weather, route-opt, noon, storm, port-weather, and report-sender.

This slice is for a VM that already has **voyagepm_be** and **Postgres** (VoyagePM + client DBs). Ingest does **not** call the backend. It parses Excel, writes a local voyage registry, and drops a job file. `prevoyage_db` claims that job and upserts `shipping_db.voyages` + `shipping_db.master_routes`.

```text
Email (IMAP UNSEEN) → parse attachment in memory
        → valid: VPM_JOBS_DIR job → prevoyage_db → Postgres
        → invalid: forward original + missing-field list to VPM_MAIL_REJECT_TO
        (attachment is never written to VPM_INBOX_DIR)

Folder drop still works: Excel → VPM_INBOX_DIR/incoming/ → ingest → job → DB
```

Do **not** also run `python3 scripts/run_daemon.py` — that would double-pick inbox files.

Pickup, validation, IMAP, and ingest accept live in `inbox_agent/` (not the drop folder `inbox/`). Other compose services: `prevoyage_db/`, `report_sender/`, `port_weather/`, `noon_agent/`, `weather_agent/`, `routeopt_agent/`, `storm_agent/`.

---

## What to start

| Process | Command | Role |
|---------|---------|------|
| `ingest` | `python3 scripts/run_service.py ingest` | Watches inbox, parses pre-voyage Excel/CSV, enqueues DB job |
| `prevoyage_db` | `python3 scripts/run_service.py prevoyage_db` | Writes voyage + master route to Postgres |

Docker equivalent (same two services):

```bash
docker compose up --build ingest prevoyage-db
```

---

## Env files

Two files. Postgres passwords stay in `prevoyage_db/.env` only.

```bash
cp .env.example .env
cp prevoyage_db/.env.example prevoyage_db/.env
```

### Root `.env` (ingest)

Required for this slice:

```bash
# mock is enough — ingest does not hit voyagepm_be
VPM_MODE=mock

# Must match PREVOYAGE_DB_TENANTS (lowercase)
VPM_TENANT=orion

# Shared job bus with prevoyage_db (same absolute path on both processes)
VPM_JOBS_DIR=/var/vpm/jobs

# Drop folder — files go in incoming/, not the root
VPM_INBOX_DIR=/var/vpm/inbox

# Local voyage state (directory or .json path both work)
VPM_REGISTRY_PATH=/var/vpm/registry

# Ingest still writes a pre-voyage txt/pdf here; unused if you skip report-sender
VPM_REPORTS_OUT_DIR=/var/vpm/reports
VPM_TEMPLATES_DIR=/path/to/voyagepm_agentic_framework/templates

# Ingest-only chain: do not queue weather / route-opt jobs
VPM_DAEMON_FLOW=noon_monitoring

VPM_INBOX_POLL_SECONDS=15

# IMAP mailbox that receives Pre-Dep Excel (parsed in RAM; not saved to disk)
# iPowered: full address + mailbox password is enough
VPM_MAIL_EMAIL=ops@yourcompany.com
VPM_MAIL_PASSWORD=
VPM_MAIL_REJECT_TO=ops-review@yourcompany.com
VPM_MAIL_POLL_SECONDS=900

# SMTP used only to forward rejected mail
VPM_SMTP_HOST=
VPM_SMTP_PORT=587
VPM_SMTP_USER=
VPM_SMTP_PASSWORD=
VPM_SMTP_FROM=
```

`VPM_EMAIL` / `VPM_PASSWORD` are VoyagePM backend login, not this mailbox. Leave them empty for this slice.

If you later want ingest to talk to the running backend, set `VPM_MODE=live` plus `VPM_BASE_URL` / `VPM_EMAIL` / `VPM_PASSWORD` / `VPM_COMPANY`. Not needed for Excel → DB.

### `prevoyage_db/.env` (writer)

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

First-pass dry run: set `PREVOYAGE_DB_DRY_RUN=true` (maps + vessel lookup, no INSERT).

---

## One-time setup on the VM

Python 3.12+ (matches the image). From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

mkdir -p /var/vpm/inbox/incoming /var/vpm/jobs /var/vpm/registry /var/vpm/reports
```

Compose bind-mounts host paths from `.env`. Override the defaults in `docker-compose.yml` (they currently fall back to a developer laptop path). Set `VPM_INBOX_DIR`, `VPM_JOBS_DIR`, `VPM_REGISTRY_PATH`, `VPM_REPORTS_OUT_DIR`, `VPM_TEMPLATES_DIR` to real VM directories.

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

# 3) inbox (other terminal)
python3 scripts/run_service.py ingest
```

Writer log on start should include `tenants=orion` and `jobs=/var/vpm/jobs`. If it exits with “no tenants”, the `PREVOYAGE_DB_*_URL` vars did not load (`prevoyage_db/.env` missing or URLs empty).

---

## How to test

### Email

Enable IMAP as above. Send a Pre-Dep `.xlsx` to `VPM_MAIL_IMAP_USER`. Valid mail is queued for `prevoyage_db` (no copy in `incoming/`). Invalid mail is forwarded to `VPM_MAIL_REJECT_TO` with the missing fields listed; the original message is attached as `.eml`.

Gmail/Workspace: use an app password, IMAP enabled. Microsoft 365: IMAP must be allowed (or this poller will not see the mailbox).

### Folder drop

Drop the file into **incoming**, not the inbox root:

```bash
cp /path/to/Pre-Dep.xlsx /var/vpm/inbox/incoming/
```

Supported: `.xlsx` / `.xlsm` / `.csv` with SSH Pre-Dep sheets (`Voyage Details`, `Waypoints List`, …) or a flat file with waypoints + CP speed.

Expect:

| Check | Where |
|-------|--------|
| File left inbox | `/var/vpm/inbox/sent/` (failures → `failed/`) |
| Registry | `/var/vpm/registry/voyage_registry.json` |
| Job claimed | ingest log: `queued prevoyage_db:orion:<VOY>` then writer: `done orion <VOY> → voyage_id=…` |
| DB | `shipping_db.voyages` + `shipping_db.master_routes` for that voyage number |

Optional without the inbox loop:

```bash
python3 scripts/test_prevoyage_db.py --tenant orion lookup "VESSEL NAME"
python3 scripts/test_prevoyage_db.py --tenant orion dry-run /path/to/Pre-Dep.xlsx
```

---

## If nothing lands in Postgres

1. `VPM_TENANT` unset → ingest skips the job (`VPM_TENANT unset — skip prevoyage_db job`).
2. `VPM_JOBS_DIR` differs between `.env` and `prevoyage_db/.env` → writer never sees the job.
3. Tenant key mismatch (`orion` vs `Orion`) — both sides are lowercased; URLs must use `PREVOYAGE_DB_ORION_…`.
4. Vessel name not in client `ship` table → writer fails the job (see `VPM_JOBS_DIR` failed files / writer log).
5. File dropped in inbox root instead of `incoming/`.
6. `run_daemon.py` and `ingest` both running → race on the same file.

Stop with Ctrl-C. Compose: `docker compose stop ingest prevoyage-db`.
