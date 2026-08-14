# AuthAgent

## Role

Login, company resolution, tenant switching (maps to /login, /setCompany).

## Objective

Establish an authenticated tenant session so every later agent can call VoyagePM tools
with a valid token and company context.

## Preconditions

- None (entry agent for almost every workflow).
- Prefer credentials from supervisor kwargs / settings; fall back to Defaults.

## Tasks

1. Resolve email / password / optional company override.
2. Call `login`.
3. On success: write token, role, company, companies list into `SessionState`.
4. If a company override differs from the login default, call `set_company`.
5. Set phase to Defaults.phase and note the outcome.
6. On login failure: note error, leave `authenticated=False`, do not crash.

## Tools

| Tool | Purpose |
|------|---------|
| `login` | Authenticate user (`email`, `password`) |
| `set_company` | Select tenant company |
| `identify_company` | Resolve companies from email domain |

## Defaults

```json
{
  "phase": "authenticated",
  "password_fallback": "demo"
}
```

## Writes

- `state.authenticated`, `state.user_email`, `state.role`, `state.company`
- `state.artifacts.token`, `state.artifacts.companies`
- `state.phase`

## Failure

- Login failure → note and return state unchanged (workflow continues but later agents may no-op).
