# SupervisorOrchestrator

## Role

Central planner/router over specialist agents — perceive goal, plan ordered specialists,
execute, persist SessionState.

## Objective

Turn a named workflow or free-form goal into an ordered agent plan and run it end-to-end
with shared memory and an audit log.

## Preconditions

- Specialist specs under `agents/specs/*.md` must exist for every roster agent.
- Backend (mock or live) available via `get_backend()`.

## Tasks

1. Perceive: accept workflow name or natural-language goal.
2. Plan: LLM plan if key present; else keyword → named workflow; else full lifecycle.
3. Always ensure AuthAgent is first when LLM omits it.
4. Act: run each specialist `run()` with kwargs from CLI/settings.
5. Observe: agents write SessionState; supervisor appends plan/complete notes.
6. Persist: JSON under `data/`.

## Tools

Supervisor does not own VoyagePM tools; it only schedules specialists.

## Defaults

```json
{
  "fallback_workflow": "full_voyage_lifecycle",
  "workflows": {
    "full_voyage_lifecycle": [
      "AuthAgent",
      "FleetAgent",
      "VoyageAgent",
      "RouteOptimizationAgent",
      "WeatherAgent",
      "StormGeofenceAgent",
      "AlertAgent",
      "PerformanceReportAgent"
    ],
    "optimize_and_publish": [
      "AuthAgent",
      "FleetAgent",
      "VoyageAgent",
      "WeatherAgent",
      "RouteOptimizationAgent",
      "AlertAgent"
    ],
    "storm_response": [
      "AuthAgent",
      "FleetAgent",
      "VoyageAgent",
      "StormGeofenceAgent",
      "WeatherAgent",
      "RouteOptimizationAgent",
      "AlertAgent"
    ],
    "daily_monitoring": [
      "AuthAgent",
      "FleetAgent",
      "VoyageAgent",
      "WeatherAgent",
      "StormGeofenceAgent",
      "AlertAgent"
    ],
    "performance_closeout": [
      "AuthAgent",
      "FleetAgent",
      "VoyageAgent",
      "PerformanceReportAgent"
    ]
  },
  "goal_hints": [
    {"keys": ["storm", "geofence", "cyclone", "typhoon"], "workflow": "storm_response"},
    {"keys": ["cii", "eov", "noon", "performance", "savings"], "workflow": "performance_closeout"},
    {"keys": ["monitor", "daily", "alert"], "workflow": "daily_monitoring"},
    {"keys": ["optimize", "fuel", "fastest", "shortest", "route"], "workflow": "optimize_and_publish"},
    {"keys": ["voyage", "full", "lifecycle", "end-to-end", "e2e"], "workflow": "full_voyage_lifecycle"}
  ]
}
```

Edit `workflows` / `goal_hints` here to change routing without hunting through Python.
The orchestrator loads this Defaults block at init and uses it as the plan catalogue.

## Writes

- `state.log` supervisor notes
- persisted JSON via `save_state`

## Failure

- Unknown workflow name → raise ValueError listing known workflows.
