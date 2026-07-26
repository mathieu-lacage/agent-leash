# Network isolation

Each sandbox runs inside a bwrap `--unshare-net` network namespace.
The agent has no access to the host network stack except through two host-side proxies.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  bwrap sandbox (isolated network namespace)           │
│                                                       │
│  agent process                                        │
│    │                                                  │
│    ├─ HTTP/HTTPS ──► http://169.254.0.1:<mitm_port>  │
│    │                  env: http_proxy / https_proxy   │
│    │                                                  │
│    ├─ other TCP  ──► aleash0 TUN → gateway (gated)   │
│    ├─ TCP :80/:443 direct ──► RST (must use proxy)   │
│    │                                                  │
│    ├─ DNS (UDP/53) ──► aleash0 TUN → host DNS        │
│    │                   (ungated, forwarded)           │
│    └─ other UDP  ──► aleash0 TUN → gateway (gated)   │
│                                                       │
│  aleash0 TUN: 169.254.0.2/30                         │
│  default route: dev aleash0                           │
└───────────────────────┬──────────────────────────────┘
                        │  TUN fd (SCM_RIGHTS, cross-ns)
┌───────────────────────▼──────────────────────────────┐
│  host                                                 │
│                                                       │
│  _tun_gateway.py (asyncio)                           │
│    reads/writes raw IP packets on the TUN fd          │
│    – TCP :80/:443 direct → RST                       │
│    – TCP to 169.254.0.1  → 127.0.0.1 (no gate)      │
│    – TCP other           → approval gate → proxy     │
│    – UDP/53              → host DNS (no gate)         │
│    – UDP :80/:443        → drop (QUIC)               │
│    – UDP to 169.254.0.1  → 127.0.0.1 (no gate)      │
│    – UDP other           → approval gate → forward   │
│                                                       │
│  mitmdump (:mitm_port, 127.0.0.1)                    │
│    TLS termination + DomainGatekeeper addon           │
│    POST /api/proxy/request-approval → browser UI     │
│                                                       │
│  aleash server (:7612)                               │
│    WebSocket + notify-send → user approves/blocks    │
└──────────────────────────────────────────────────────┘
```

## Address mapping

`169.254.0.1` is the gateway address as seen from inside the sandbox.
The gateway maps it to `127.0.0.1` on the host for any connection that reaches it ungated (host services, DNS TCP).

`169.254.0.2/30` is the sandbox's own IP on the `aleash0` TUN link.

## Traffic paths

| Traffic | Path | Gated? |
|---|---|---|
| HTTP | `http_proxy` → mitmproxy → approval → host | yes, per domain |
| HTTPS | `https_proxy` → mitmproxy (MitM) → approval → host | yes, per domain |
| TCP port 80/443 direct | RST injected by gateway | blocked |
| TCP other (any client) | TUN → gateway → approval → `open_connection` | yes, per host:port |
| DNS (UDP/53) | TUN → gateway → host DNS (8.8.8.8 or systemd-resolved) | no |
| UDP port 80/443 | dropped by gateway | blocked (QUIC) |
| UDP other | TUN → gateway → approval → forwarded | yes, per host:port |
| TCP/UDP to 169.254.0.1 | TUN → gateway → 127.0.0.1 (no gate) | no |

The key difference from proxy-env-var approaches: the gateway intercepts at Layer 3, so programs that ignore `http_proxy` / `ALL_PROXY` (Go binaries, native DNS resolvers, anything using raw sockets) are gated the same way as proxy-aware clients.

## TUN setup

The gateway cannot create the TUN device from the host namespace directly; `TUNSETIFF` requires `CAP_NET_ADMIN` in the network namespace's owning user namespace. The setup uses a fork-based trick:

1. The gateway process (`_tun_gateway.py`) forks a child.
2. The child calls `setns` on the sandbox's user namespace (granted because the gateway runs as the namespace owner, uid 1000), then `setns` on the sandbox's network namespace.
3. Inside the child's new context, `TUNSETIFF` succeeds. The child configures the interface (`SIOCSIFADDR`, `SIOCSIFFLAGS`) and default route (`RTM_NEWROUTE` via netlink) using direct ioctls — `exec`'d subprocesses like `ip` lose the acquired capabilities across `execve`.
4. The child sends the TUN fd back to the parent via SCM_RIGHTS and exits.
5. The parent (still in host namespaces) holds the fd and can read/write raw IP packets that flow through `aleash0`.

## Approval flow

For every new TCP connection or UDP destination that requires gating, the gateway POSTs to `/api/proxy/request-approval`:

```
POST /api/proxy/request-approval
{"sandbox_id": "...", "domain": "github.com:22"}
```

The server creates an asyncio Future, notifies connected browser clients via WebSocket and optionally via `notify-send`. The request blocks (up to 60 s) until the user clicks one of:

- **Allow once** / **Always allow** → connection proceeds
- **Block once** / **Always block** → RST injected (TCP) or packet dropped (UDP)

"Always" decisions are persisted and applied immediately on future connections without prompting.

mitmproxy uses the same endpoint for HTTP/HTTPS, keyed by domain name rather than `host:port`.

## DNS

`/etc/resolv.conf` inside the sandbox is overridden at bwrap startup with `nameserver 169.254.0.1` (using `--ro-bind-data` to handle Fedora's symlink to `/run/systemd/resolve/stub-resolv.conf`). The gateway forwards all UDP/53 to the host's upstream resolver, detected from `/run/systemd/resolve/resolv.conf` before namespace entry (falling back to `127.0.0.53`). DNS is not gated; only actual connections are subject to approval.
