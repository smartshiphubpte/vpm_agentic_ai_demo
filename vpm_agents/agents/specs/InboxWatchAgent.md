# InboxWatchAgent

## Role

Watch `VPM_INBOX_DIR` for new pre-voyage Excel (or CSV) drops and dispatch them.

## Objective

Each poll cycle, classify new files and hand pre-voyage workbooks to PreVoyageIngestAgent.
Noon files belong in `VPM_NOON_INBOX_DIR` (NoonExcelWatchAgent), not this inbox.

## Preconditions

- `VPM_INBOX_DIR` exists or can be created.

## Tasks

1. List new `.csv` / `.xlsx` / `.xlsm` files in inbox (not in processed/failed).
2. Classify by header columns (pre-voyage vs unknown).
3. Dispatch pre-voyage files to PreVoyageIngestAgent (multiple files = multiple voyages).
4. Noon files dropped here → `failed/` (use `VPM_NOON_INBOX_DIR`).
5. Unknown types → `failed/`.

## Tools

| Tool | Purpose |
|------|---------|
| `list_inbox` | List pending inbox files |

## Defaults

```json
{
  "phase": "inbox_scanned"
}
```

## Writes

- Delegates writes to PreVoyageIngestAgent / NoonOpsAgent.

## Failure

- Per-file failures handled by specialists; watcher continues.
