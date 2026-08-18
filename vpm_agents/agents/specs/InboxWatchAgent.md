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
2. Classify by header columns (pre-voyage vs noon Excel vs unknown).
3. Dispatch pre-voyage files to PreVoyageIngestAgent (multiple files = multiple voyages).
   In daemon forever mode (`enqueue=True`), each file is submitted to the **ingest** job pool.
   After ingest succeeds, weather / route-optimize are queued on the **heavy** pool so the next
   file can ingest while route-opt is still running. Unknown drops are archived immediately
   (they do not occupy a worker).
4. Noon files dropped here (including noon Excel named like Pre-Dep) → moved to
   `VPM_NOON_INBOX_DIR`. Unknown types → `failed/`.

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
