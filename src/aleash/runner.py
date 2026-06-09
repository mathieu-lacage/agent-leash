"""
Orchestrates a single sandbox run:
  1. start xdg-dbus-proxy
  2. start mitmproxy (mitmdump)
  3. build bwrap command
  4. exec the agent in a PTY
  5. stream PTY output to DB + WebSocket
  6. cleanup on exit
"""

import asyncio
import fcntl
import os
import pty
import select
import shutil
import signal
import socket
import struct
import tempfile
import termios
import threading
import time
import tty
import uuid
from pathlib import Path

from .bwrap import build_bwrap_argv, write_fake_flatpak_info
from .profiles import Profile
from . import db as dbmod

_DEFAULT_SERVER_URL = "http://localhost:7612"
MLEASH_DIR = Path.home() / ".aleash"

# Maps sandbox_id -> master PTY fd (same-process fast path)
_pty_fds: dict[str, int] = {}
# Maps sandbox_id -> (cols, rows) current PTY size
_pty_sizes: dict[str, tuple[int, int]] = {}
# Maps sandbox_id -> browser_master flag
_sandbox_browser_master: dict[str, bool] = {}


def _pty_input_sock_path(sandbox_id: str) -> Path:
    return MLEASH_DIR / f"pty-{sandbox_id}.sock"


async def _push_size_to_server(sandbox_id: str, cols: int, rows: int,
                                server_url: str, browser_master: bool = False) -> None:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=1.0) as c:
            await c.post(f"{server_url}/api/internal/terminal-resize", json={
                "id": sandbox_id, "cols": cols, "rows": rows,
                "browser_master": browser_master,
            })
    except Exception:
        pass


def write_pty_input(sandbox_id: str, data: bytes) -> None:
    fd = _pty_fds.get(sandbox_id)
    if fd is not None:
        try:
            os.write(fd, data)
        except OSError:
            pass


def resize_pty(sandbox_id: str, cols: int, rows: int) -> None:
    fd = _pty_fds.get(sandbox_id)
    if fd is not None:
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _xdg_runtime() -> str:
    return os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")


def _mitmproxy_ca_cert() -> Path:
    return Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"


async def _start_xdg_dbus_proxy(sock_path: str) -> asyncio.subprocess.Process:
    dbus_addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    proc = await asyncio.create_subprocess_exec(
        "xdg-dbus-proxy", dbus_addr, sock_path,
        "--filter",
        "--talk=org.freedesktop.portal.Desktop",
        "--call=org.freedesktop.portal.OpenURI=*",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    # wait until socket appears (up to 5s)
    for _ in range(50):
        if Path(sock_path).is_socket():
            break
        await asyncio.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError(f"xdg-dbus-proxy: socket never appeared at {sock_path}")
    return proc


async def _ensure_mitmproxy_ca() -> Path:
    ca = _mitmproxy_ca_cert()
    if not ca.exists():
        # run mitmdump briefly to generate certs
        proc = await asyncio.create_subprocess_exec(
            "mitmdump", "--listen-port", "18080", "-n",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        for _ in range(50):
            if ca.exists():
                break
            await asyncio.sleep(0.1)
        proc.kill()
        await proc.wait()
    return ca


async def _start_mitmdump(proxy_port: int, sandbox_id: str, addon_path: str,
                          server_url: str) -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        "mitmdump",
        "--listen-host", "127.0.0.1",
        "--listen-port", str(proxy_port),
        "-s", addon_path,
        "--set", f"sandbox_id={sandbox_id}",
        "--set", f"server_url={server_url}",
        "--quiet",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    # give it a moment to bind
    await asyncio.sleep(0.5)
    return proc


async def run_sandbox(
    profile: Profile,
    cwd: str,
    cmd: list[str],
    sandbox_id: str | None = None,
    browser_master: bool = False,
    server_url: str = _DEFAULT_SERVER_URL,
) -> int:
    """Run agent in sandbox. Returns exit code."""
    sandbox_id = sandbox_id or str(uuid.uuid4())
    started_at = int(time.time() * 1000)
    _sandbox_browser_master[sandbox_id] = browser_master
    MLEASH_DIR.mkdir(parents=True, exist_ok=True)

    # locate the proxy addon script
    addon_path = str(Path(__file__).parent / "proxy_addon.py")

    tmpdir = tempfile.mkdtemp(prefix="aleash-")
    xdg_proxy_sock = str(Path(tmpdir) / "xdg-proxy.sock")
    fake_flatpak = write_fake_flatpak_info(tmpdir)

    import httpx
    async with httpx.AsyncClient(timeout=2.0) as c:
        await c.post(f"{server_url}/api/internal/sandbox-start", json={
            "id": sandbox_id, "profile": profile.name,
            "cwd": cwd, "cmd": " ".join(cmd), "started_at": started_at,
            "browser_master": browser_master,
        })

    proxy_port = _free_port()
    ca_cert = await _ensure_mitmproxy_ca()

    xdg_proc = None
    mitm_proc = None
    exit_code = 1

    try:
        # start xdg-dbus-proxy
        xdg_proc = await _start_xdg_dbus_proxy(xdg_proxy_sock)

        # start mitmproxy
        mitm_proc = await _start_mitmdump(proxy_port, sandbox_id, addon_path, server_url)

        # build bwrap command
        bwrap_argv = build_bwrap_argv(
            profile=profile,
            cwd=cwd,
            proxy_port=proxy_port,
            xdg_proxy_sock=xdg_proxy_sock,
            ca_cert_path=str(ca_cert),
            fake_flatpak_info=fake_flatpak,
            cmd=cmd,
        )

        # open PTY
        master_fd, slave_fd = pty.openpty()
        _pty_fds[sandbox_id] = master_fd

        # Unix socket so the server process can forward browser keyboard input
        MLEASH_DIR.mkdir(parents=True, exist_ok=True)
        sock_path = _pty_input_sock_path(sandbox_id)
        sock_path.unlink(missing_ok=True)

        def _make_input_handler(fd: int):
            async def _handle(reader: asyncio.StreamReader, _writer: asyncio.StreamWriter):
                buf = b""
                while True:
                    chunk = await reader.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    # scan for resize frames: \x00R<cols_hi><cols_lo><rows_hi><rows_lo>
                    out = b""
                    i = 0
                    while i < len(buf):
                        if buf[i:i+2] == b"\x00R" and len(buf) >= i + 6:
                            cols, rows = struct.unpack(">HH", buf[i+2:i+6])
                            winsize = struct.pack("HHHH", rows, cols, 0, 0)
                            try:
                                fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
                            except OSError:
                                pass
                            i += 6
                        else:
                            out += buf[i:i+1]
                            i += 1
                    buf = b""
                    if out:
                        try:
                            os.write(fd, out)
                        except OSError:
                            break
            return _handle

        pty_input_server = await asyncio.start_unix_server(
            _make_input_handler(master_fd), path=str(sock_path)
        )

        # set raw mode so the PTY passes through resize etc.
        os.set_inheritable(slave_fd, True)

        # inherit terminal size from current terminal if any, store for browser
        cols, rows = 220, 50  # sensible fallback
        try:
            ts = struct.pack("HHHH", 0, 0, 0, 0)
            ts = fcntl.ioctl(0, termios.TIOCGWINSZ, ts)
            rows_r, cols_r, _, _ = struct.unpack("HHHH", ts)
            if cols_r > 0 and rows_r > 0:
                cols, rows = cols_r, rows_r
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, ts)
        except Exception:
            pass
        _pty_sizes[sandbox_id] = (cols, rows)

        # notify server of initial size + mode
        await _push_size_to_server(sandbox_id, cols, rows, server_url, browser_master)

        # SIGWINCH: track local terminal resize → update PTY + notify server/browser
        loop = asyncio.get_event_loop()
        if not browser_master and os.isatty(0):
            def _on_sigwinch():
                try:
                    ts2 = fcntl.ioctl(0, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
                    r2, c2, _, _ = struct.unpack("HHHH", ts2)
                    if c2 > 0 and r2 > 0:
                        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, ts2)
                        _pty_sizes[sandbox_id] = (c2, r2)
                        asyncio.ensure_future(_push_size_to_server(sandbox_id, c2, r2, server_url, False))
                except OSError:
                    pass
            loop.add_signal_handler(signal.SIGWINCH, _on_sigwinch)

        def _child_setup() -> None:
            # New session so the child becomes session leader, then claim the
            # slave PTY as the controlling terminal. fd 0 is already the slave
            # PTY at this point (dup2 has run, preexec_fn runs after).
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        proc = await asyncio.create_subprocess_exec(
            *bwrap_argv,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            preexec_fn=_child_setup,
        )
        os.close(slave_fd)

        # stream PTY output; also relay local terminal if stdin is a TTY
        loop = asyncio.get_event_loop()
        await _stream_pty(master_fd, sandbox_id, loop, interactive=os.isatty(0))

        exit_code = await proc.wait()

    finally:
        _pty_fds.pop(sandbox_id, None)
        _pty_sizes.pop(sandbox_id, None)
        _sandbox_browser_master.pop(sandbox_id, None)
        try:
            loop.remove_signal_handler(signal.SIGWINCH)
        except Exception:
            pass
        # No awaits in this block — CancelledError is BaseException and would skip
        # everything after the first await.  Use sync operations throughout.
        try:
            pty_input_server.close()
        except Exception:
            pass
        _pty_input_sock_path(sandbox_id).unlink(missing_ok=True)
        if master_fd:
            try:
                os.close(master_fd)
            except OSError:
                pass
        for _p in (mitm_proc, xdg_proc):
            if _p and _p.returncode is None:
                try:
                    _p.kill()
                except Exception:
                    pass

        shutil.rmtree(tmpdir, ignore_errors=True)

        # Sync DB update — guaranteed even if asyncio task was cancelled.
        import sqlite3 as _sqlite3
        ended_at = int(time.time() * 1000)
        try:
            with _sqlite3.connect(str(dbmod.DB_PATH)) as _sdb:
                _sdb.execute(
                    "UPDATE sandboxes SET ended_at=?, exit_code=? WHERE id=?",
                    (ended_at, exit_code, sandbox_id),
                )
        except Exception:
            pass

        # Notify server so it broadcasts sandboxes_updated to browser.
        # Runs in a thread so it fires even during asyncio shutdown.
        import urllib.request as _urlreq, json as _json, threading as _thr
        def _notify():
            try:
                body = _json.dumps({"id": sandbox_id, "ended_at": ended_at,
                                    "exit_code": exit_code}).encode()
                req = _urlreq.Request(
                    f"{server_url}/api/internal/sandbox-end",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                _urlreq.urlopen(req, timeout=0.5)
            except Exception:
                pass
        _t = _thr.Thread(target=_notify, daemon=True)
        _t.start()
        _t.join(timeout=0.6)

    return exit_code


async def _stream_pty(
    master_fd: int,
    sandbox_id: str,
    loop: asyncio.AbstractEventLoop,
    interactive: bool = False,
) -> None:
    """
    Read PTY output → DB + WebSocket (+ local stdout when interactive).
    When interactive, also forward local stdin → PTY master in raw mode.
    """
    import aiosqlite

    read_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    old_term = None

    if interactive:
        try:
            old_term = termios.tcgetattr(0)
            tty.setraw(0)
        except termios.error:
            interactive = False  # stdin not a real tty after all

    def _reader():
        watch = [master_fd, 0] if interactive else [master_fd]
        while True:
            try:
                r, _, _ = select.select(watch, [], [], 0.05)
            except (ValueError, OSError):
                break

            if master_fd in r:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                loop.call_soon_threadsafe(read_queue.put_nowait, data)

            if interactive and 0 in r:
                try:
                    data = os.read(0, 4096)
                    if data:
                        os.write(master_fd, data)
                except OSError:
                    pass

        loop.call_soon_threadsafe(read_queue.put_nowait, None)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    log_db = await aiosqlite.connect(dbmod.DB_PATH)

    try:
        while True:
            data = await read_queue.get()
            if data is None:
                break
            if interactive:
                os.write(1, data)  # local stdout
            ts = int(time.time() * 1000)
            await dbmod.append_terminal_log(log_db, sandbox_id, ts, data)
            # server polls terminal_log directly; no in-process broadcast needed
    finally:
        if old_term is not None:
            termios.tcsetattr(0, termios.TCSADRAIN, old_term)
        await log_db.close()
        t.join(timeout=1.0)
