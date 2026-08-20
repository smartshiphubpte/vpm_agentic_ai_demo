# MailInboxAgent

## Role

Poll a mailbox (IMAP) for pre-voyage Excel/CSV attachments, parse them in memory, and enqueue a `prevoyage_db` job. Do not save the email or attachment to disk.

## Objective

Valid Pre-Dep / pre-voyage attachments become the same database write as folder ingest. Invalid mail is forwarded to `VPM_MAIL_REJECT_TO` with a detailed missing-field list.

## Preconditions

- `VPM_MAIL_EMAIL` + `VPM_MAIL_PASSWORD` (iPowered: host defaults to `imap.ipower.com`).
- `VPM_TENANT` set to enqueue DB jobs.
- SMTP (`VPM_SMTP_*`) + `VPM_MAIL_REJECT_TO` for rejections.

## Tasks

1. Fetch UNSEEN messages from `VPM_MAIL_IMAP_FOLDER` every `VPM_MAIL_POLL_SECONDS` (default 900 = 15 min).
2. Collect `.xlsx` / `.xlsm` / `.csv` attachments in memory only.
3. Parse + validate (voyage number, vessel, ports, CP speed, ≥2 waypoints). Convert master Lat/Long from degrees (DM/DMS) to decimal; reject if converted points don't match the master.
4. Optional vessel lookup against client `ship` when `prevoyage_db/.env` is loaded.
5. Valid → `PreVoyageIngestAgent.ingest_parsed(persist_files=False)` → job bus.
6. Invalid → forward original RFC822 + rejection body; then mark `\Seen`.
7. If reject-forward fails, leave UNSEEN so the next poll retries.

## Tools

None — IMAP/SMTP helpers in `inbox_agent/mail.py`.

## Defaults

```json
{
  "phase": "mail_scanned"
}
```

## Writes

- `prevoyage_db` job files under `VPM_JOBS_DIR` (parsed record JSON, not the Excel).
- No inbox/sent copies of the attachment.

## Failure

- IMAP/SMTP errors are logged; a failed forward does not mark the message seen.
