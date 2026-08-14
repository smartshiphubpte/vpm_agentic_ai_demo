# Agent behavior spec — template

Copy this file to `{AgentName}.md`. The agent class name must match the filename.

Edit the Markdown to change how the agent behaves. Machine-readable knobs live only in
the **Defaults** JSON fence — `run()` reads those. Everything else is the human/LLM
task brief (role, steps, preconditions, failure rules).

---

## Role

One-line description shown in the supervisor roster / planner.

## Objective

What success looks like for this agent in one run.

## Preconditions

- What must already be true on `SessionState` before this agent runs.

## Tasks

Ordered checklist the agent executes:

1. Step one
2. Step two

## Tools

| Tool | Purpose |
|------|---------|
| `tool_name` | When / why to call it |

## Defaults

```json
{
  "phase": "done",
  "example_knob": "value"
}
```

## Writes

- `state.field` — what this agent mutates

## Failure

- How to abort or skip without crashing the workflow
