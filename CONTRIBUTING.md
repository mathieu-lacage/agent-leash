# Contributing

## System requirements

| Tool | Purpose | Install |
|------|---------|---------|
| `bwrap` | filesystem isolation | `dnf install bubblewrap` / `apt install bubblewrap` |
| `xdg-dbus-proxy` | portal socket for xdg-open | `dnf install xdg-dbus-proxy` / `apt install xdg-dbus-proxy` |
| Python ≥ 3.11 | backend | system or pyenv |
| Node ≥ 18 + npm | frontend build | system or nvm |

`mitmproxy` is a Python dependency — no separate install needed.

## First-time setup

```sh
git clone <repo> aleash
cd aleash

# Python env — uv creates .venv, downloads Python 3.12 (pinned in .python-version),
# and installs all deps. The frontend build is skipped for editable installs.
uv sync

# Frontend deps + initial build (one-time)
cd frontend
npm install       # generates package-lock.json if missing
npm run build     # outputs to src/aleash/static/
cd ..
```

## Running in dev mode

Two terminals needed.

**Terminal 1 — daemon (foreground so you see logs):**

```sh
uv run aleash start --foreground
# → Sandbox UI available on http://localhost:7612/
```

**Terminal 2 — run an agent:**

```sh
cd /some/project
uv run --project /path/to/aleash aleash claude
```

Open http://localhost:7612/ to see the UI.

## Frontend iteration

```sh
make frontend
# → http://localhost:5173/
```

Vite serves the UI with hot-reload. Edits to `frontend/src/` reflect instantly in the browser.
Proxies `/api` and `/ws` to the daemon — requires `uv run aleash start --foreground` running first.

## Project layout

```
src/aleash/
  cli.py          # Click commands: start / stop / list / claude / opencode / run
  server.py       # FastAPI daemon (REST + WebSocket)
  runner.py       # PTY capture, bwrap + mitmproxy orchestration
  proxy_addon.py  # mitmproxy addon (domain gating)
  bwrap.py        # bubblewrap argv builder
  profiles.py     # per-agent bind-mount profiles
  db.py           # aiosqlite schema + queries  (~/.aleash/data.db)
  notifications.py# notify-send wrapper

frontend/src/
  App.vue                       # root: sidebar + main panel + approval modal
  components/SandboxList.vue    # sidebar sandbox list
  components/SandboxDetail.vue  # tabs: Terminal | Domains
  components/TerminalPane.vue   # xterm.js over WebSocket
  components/DomainsPane.vue    # domain decision table
  components/DomainApproval.vue # approval modal
```

## Adding an agent profile

Edit `src/aleash/profiles.py`:

```python
_register(Profile(
    name="myagent",
    binary_names=["myagent"],          # binary names on PATH to auto-detect
    extra_binds=[
        (_home(".config/myagent"), _home(".config/myagent")),
    ],
    ensure_home_dirs=[".cache/myagent"],
))
```

Then add a Click command in `cli.py` (copy the `claude` command, change the name).

## Building a wheel

Requires npm on PATH — the build hook runs `npm ci && npm run build` automatically for wheel builds (skipped for editable installs).

```sh
uv build
# outputs dist/aleash-*.whl
```

## Data

All state lives in `~/.aleash/`:

| Path | Content |
|------|---------|
| `~/.aleash/data.db` | SQLite: sandboxes, terminal logs, domain decisions |
| `~/.aleash/server.pid` | daemon PID |
| `~/.aleash/server.log` | daemon stdout/stderr when daemonized |
| `~/.mitmproxy/` | mitmproxy CA cert (auto-generated on first run) |

Delete `~/.aleash/data.db` to reset all history.
