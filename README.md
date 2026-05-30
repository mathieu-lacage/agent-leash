# agent-leash

Sandbox runner for AI coding agents. Restricts filesystem access to the current
directory and intercepts all network traffic with interactive per-domain approval.

```console
$ pipx install agent-leach
# Start the web UI
$ aleash start
Sandbox UI available on http://localhost:7612/
$ aleash claude
# Stop the web UI
$ aleash stop
```

## How it works

- **Filesystem** — [bubblewrap](https://github.com/containers/bubblewrap) restricts the agent to the current working directory. The rest of the filesystem is read-only or hidden.
- **Network** — [mitmproxy](https://mitmproxy.org/) intercepts all outbound HTTPS. Each new domain triggers a browser popup (and desktop notification). You choose: always allow, allow once, always block, or block once.
- **Web UI** — Vue 3 + xterm.js frontend served on `localhost:7612`. Shows live terminal output, domain decisions, and session history with full-text search.

## Requirements

| Tool | Install |
|------|---------|
| `bwrap` (bubblewrap) | `dnf install bubblewrap` / `apt install bubblewrap` |
| `xdg-dbus-proxy` | `dnf install xdg-dbus-proxy` / `apt install xdg-dbus-proxy` |
| Python ≥ 3.11 | system or [pyenv](https://github.com/pyenv/pyenv) |

`mitmproxy` is installed automatically as a Python dependency.

## Usage

### Pass arguments

```sh
aleash claude -- --dangerously-skip-permissions
aleash run python script.py --some-flag
```

### Terminal size

By default the local terminal controls the PTY size. The browser shows the fixed-size terminal with scrollbars. Use `--browser-master` to invert this (browser FitAddon resizes the PTY):

```sh
aleash --browser-master claude
```

### Profile override

```sh
aleash --profile generic claude   # run claude with the generic profile
```

## Profiles

| Profile | What it binds |
|---------|--------------|
| `claude` | `~/.claude`, `~/.claude.json`, `~/.gitconfig` |
| `opencode` | `~/.opencode`, `~/.gitconfig`, and opencode config/cache dirs |
| `generic` | nothing extra |

`claude` and `opencode` are auto-detected by binary name. Use `--profile` to override.

## Data

All state lives in `~/.aleash/`:

| Path | Content |
|------|---------|
| `~/.aleash/data.db` | SQLite: sessions, terminal logs, domain decisions |
| `~/.aleash/server.pid` | daemon PID |
| `~/.aleash/server.log` | daemon log |
| `~/.mitmproxy/` | mitmproxy CA cert (auto-generated on first run) |

Delete `~/.aleash/data.db` to reset all history.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
