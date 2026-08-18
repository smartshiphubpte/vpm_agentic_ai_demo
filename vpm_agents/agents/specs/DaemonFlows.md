# DaemonFlows

## Role

Tagged continuous-daemon flows — pick how far the **one-shot pre-voyage chain** goes and
which **background agents** stay active.

## Objective

Let the operator select a flow tag (`VPM_DAEMON_FLOW` or `--flow`). The daemon **never stops**;
`stop` in a chain only means “do not run the next one-shot agent on ingest” (e.g. skip
`route_optimize`). Agents included in the flow keep polling on their timers (weather due jobs,
noon Excel, storm watch). When an **Arrival** noon row is processed, End-of-Voyage report
generation is queued on a **background thread** (`VPM_EOV_ON_ARRIVAL`) so inbox/noon polling
continues for other voyages.

**Parallelism:** each loop is its own process (Docker Compose) or poller thread (`run_daemon.py`).
They share the voyage registry JSON, drop folders, storm snapshots, and a file job bus
(`VPM_JOBS_DIR`). Ingest never waits for route-opt: it writes `weather_due_at` on the
registry and a `routeopt:…` job file, then picks up the next Excel. StormWatchAgent
**never joins the job queue** — it fetches on its timer; route-opt only reads the last
snapshot (`storm_cache` / `storms_*.json`). Arrival noon still queues EOV on a background
thread inside the noon service (`VPM_EOV_ON_ARRIVAL`). Closed voyages (`eov_status=done`
or last noon was Arrival) are not route-optimized again.

## Flow tags

| Tag | One-shot on pre-voyage Excel | Background (keeps running) |
|-----|------------------------------|----------------------------|
| `pre_voyage_weather` | ingest → weather | weather reports on delay |
| `pre_voyage_routes` | ingest → weather → route_optimize | weather reports on delay |
| `noon_monitoring` | ingest only | noon Excel poll |
| `storm_monitoring` | ingest only | storm poll |
| `full` | ingest → weather → route_optimize | weather + noon + storm |

## Defaults

```json
{
  "default_flow": "pre_voyage_weather",
  "flows": {
    "pre_voyage_weather": {
      "description": "Pre-voyage Excel → master route → 6h waypoints → weather; no route optimize",
      "prevoyage_chain": ["ingest", "weather", "stop"],
      "weather_poll": true,
      "noon_poll": false,
      "storm_poll": false,
      "inbox_noon": false
    },
    "pre_voyage_routes": {
      "description": "Pre-voyage → weather → 4 route alternatives; no noon/storm",
      "prevoyage_chain": ["ingest", "weather", "route_optimize", "stop"],
      "weather_poll": true,
      "noon_poll": false,
      "storm_poll": false,
      "inbox_noon": false
    },
    "noon_monitoring": {
      "description": "Ingest pre-voyage if dropped; poll noon Excel on timer",
      "prevoyage_chain": ["ingest", "stop"],
      "weather_poll": false,
      "noon_poll": true,
      "storm_poll": false,
      "inbox_noon": false
    },
    "storm_monitoring": {
      "description": "Ingest if needed; storm poller only",
      "prevoyage_chain": ["ingest", "stop"],
      "weather_poll": false,
      "noon_poll": false,
      "storm_poll": true,
      "inbox_noon": false
    },
    "full": {
      "description": "Pre-voyage + route optimize + noon Excel + storm pollers",
      "prevoyage_chain": ["ingest", "weather", "route_optimize", "stop"],
      "weather_poll": true,
      "noon_poll": true,
      "storm_poll": true,
      "inbox_noon": true
    }
  }
}
```
