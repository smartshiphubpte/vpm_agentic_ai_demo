# InboxWatchAgent

## Role

Watch `VPM_INBOX_DIR` for new pre-voyage / noon Excel (or CSV) drops and dispatch them.

## Objective

Each poll cycle, classify new files and hand them to PreVoyageIngestAgent or NoonOpsAgent.

## Preconditions

- `VPM_INBOX_DIR` exists or can be created.

## Tasks

1. List new `.csv` / `.xlsx` / `.xlsm` files in inbox (not in processed/failed).
2. Classify by header columns.
3. Dispatch to the matching specialist agent.
4. Unknown types → `failed/`.

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
