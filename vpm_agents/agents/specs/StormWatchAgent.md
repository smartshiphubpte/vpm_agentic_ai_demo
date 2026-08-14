# StormWatchAgent

## Role

Independent storm alert poller — runs whether or not any voyage is active.

## Objective

Every `VPM_STORM_INTERVAL_HOURS` (default 6), fetch **all active storms and their
progressions** from live tropical cyclone APIs and write snapshot files into
`VPM_STORM_OUT_DIR`. Flag voyages whose route enters the configured center or edge
buffers.

## Storm data source (`VPM_STORM_SOURCE`)

| Value | Source |
|-------|--------|
| `live` (default) | NOAA [CurrentStorms.json](https://www.nhc.noaa.gov/CurrentStorms.json) + JTWC RSS/warning texts (`metoc.navy.mil`) |
| `backend` | VoyagePM `GET /storm-pipeline/map-layer` |
| `mock` | In-memory MockBackend storms |

## Preconditions

- None for voyage context — runs with or without active voyages.
- Logs in with `VPM_EMAIL` / `VPM_PASSWORD` so mock/live storm APIs authenticate.

## Tasks

1. Run storm watcher refresh (backend mode only; live mode skips BE watcher).
2. Fetch active storms from `VPM_STORM_SOURCE` — includes center, area/radius, and
   progression track (present + forecast).
3. Assess each voyage route against center buffer (`VPM_STORM_CENTER_BUFFER_NM`,
   default 500) and edge buffer (`VPM_STORM_EDGE_BUFFER_NM`, default 300).
4. Write timestamped `.json` + templated `.txt` into storm out folder.

## Tools

| Tool | Purpose |
|------|---------|
| `watcher` | Refresh storm pipeline |
| `storms` | List active storms + progressions |

## Defaults

```json
{
  "phase": "storm_polled"
}
```

## Writes

- `VPM_STORM_OUT_DIR/storms_*.json`
- `VPM_STORM_OUT_DIR/storms_*.txt`
- `state.storms`

## Failure

- Backend errors → note and wait for next interval (daemon keeps running).
