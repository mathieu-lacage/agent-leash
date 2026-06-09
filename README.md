# agent-leash

Sandbox runner for AI coding agents:

- Restricts filesystem access to the current directory
- Custom allow-list to expose more directories as read-only or read-write
- Intercepts all network traffic with interactive per-domain approval
- Controls access to host services (podman, docker, ssh-agent, etc.)

```console
$ pipx install agent-leash
$ aleash claude
Sandbox UI available on http://localhost:7612/
```

## How it works

- **Filesystem** — [bubblewrap](https://github.com/containers/bubblewrap) restricts the agent to the current working directory. The rest of the filesystem is read-only or hidden.
- **Network** — [mitmproxy](https://mitmproxy.org/) intercepts all outbound HTTPS. Each new domain triggers a browser popup (and desktop notification). You choose: always allow, allow once, always block, or block once.
- **Web UI** — Vue 3 + xterm.js frontend served on `localhost:7612`. Shows live terminal output, domain decisions

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
| `claude` | `~/.claude`, `~/.claude.json`, `~/.gitconfig`, `~/.local/share/claude` |
| `opencode` | `~/.opencode`, `~/.gitconfig`, and opencode config/cache dirs |
| `generic` | nothing extra |

`claude` and `opencode` are auto-detected by binary name. Use `--profile` to override.

## Data

All state lives in `CWD/.aleash/`:

| Path | Content |
|------|---------|
| `CWD/.aleash/data.db` | SQLite: sessions, terminal logs, domain decisions |
| `~/.mitmproxy/` | mitmproxy CA cert (auto-generated on first run) |

Delete `CWD/.aleash/data.db` to reset all history.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
