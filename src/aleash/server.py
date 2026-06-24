import asyncio
import json
import os
import struct
import time
import uuid
from pathlib import Path

import aiosqlite
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db as dbmod
from .services import SERVICES, SSH_AGENT_SERVICES

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="aleash")

# -- shared state ----------------------------------------------------------

# active terminal WebSocket connections: sandbox_id -> list of WebSocket
_terminal_sockets: dict[str, list[WebSocket]] = {}
# pending approval futures: approval_id -> asyncio.Future[bool]
_approval_futures: dict[str, asyncio.Future] = {}
# dedup: (sandbox_id, domain) -> Future, prevents multiple notifications for same domain
_pending_by_domain: dict[tuple[str, str], asyncio.Future] = {}
# WebSocket connections for the approvals feed
_approval_sockets: list[WebSocket] = []
# WebSocket connections for the sandbox list feed
_sandbox_list_sockets: list[WebSocket] = []
# persistent connections to per-sandbox PTY input sockets (cross-process)
_pty_input_writers: dict[str, asyncio.StreamWriter] = {}
# same-process PTY fds (set by runner when embedded)
_pty_fds: dict[str, int] = {}
# per-sandbox terminal size: (cols, rows)
_sandbox_sizes: dict[str, tuple[int, int]] = {}
# per-sandbox browser_master flag
_sandbox_browser_master: dict[str, bool] = {}
# shared db connection (set at startup)
_db: aiosqlite.Connection | None = None
# currently running sandbox id (at most one)
_current_sandbox_id: str | None = None
# unique ID for this server process, sent to clients so they can detect restarts
_instance_id = str(uuid.uuid4())


@app.on_event("startup")
async def startup():
    global _db
    _db = await dbmod.get_db()
    await dbmod.init_db(_db)


@app.on_event("shutdown")
async def shutdown():
    if _db:
        await _db.close()


def _get_db() -> aiosqlite.Connection:
    assert _db is not None
    return _db


# -- helpers ---------------------------------------------------------------


async def _broadcast(sockets: list[WebSocket], msg: dict) -> None:
    dead = []
    for ws in sockets:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        try:
            sockets.remove(ws)
        except ValueError:
            pass


async def _pty_writer(sandbox_id: str) -> asyncio.StreamWriter | None:
    """Return a cached StreamWriter to the runner's PTY input socket, or None."""
    writer = _pty_input_writers.get(sandbox_id)
    if writer is not None and not writer.is_closing():
        return writer
    from .runner import _pty_input_sock_path

    sock_path = _pty_input_sock_path(sandbox_id)
    if not sock_path.exists():
        return None
    try:
        _, writer = await asyncio.open_unix_connection(str(sock_path))
        _pty_input_writers[sandbox_id] = writer
        return writer
    except OSError:
        return None


async def write_pty_input(sandbox_id: str, data: bytes) -> None:
    # fast path: runner embedded in same process
    fd = _pty_fds.get(sandbox_id)
    if fd is not None:
        try:
            os.write(fd, data)
        except OSError:
            pass
        return
    # cross-process: forward via Unix socket
    writer = await _pty_writer(sandbox_id)
    if writer is not None:
        try:
            writer.write(data)
            await writer.drain()
        except OSError:
            _pty_input_writers.pop(sandbox_id, None)


async def resize_pty(sandbox_id: str, cols: int, rows: int) -> None:
    from .runner import resize_pty as _runner_resize

    # same-process path
    if sandbox_id in _pty_fds:
        _runner_resize(sandbox_id, cols, rows)
        return
    # cross-process: send a resize escape sequence the runner interprets
    # encode as a special framing: \x00R<cols_hi><cols_lo><rows_hi><rows_lo>
    data = b"\x00R" + struct.pack(">HH", cols, rows)
    writer = await _pty_writer(sandbox_id)
    if writer is not None:
        try:
            writer.write(data)
            await writer.drain()
        except OSError:
            _pty_input_writers.pop(sandbox_id, None)


# -- REST: sandboxes -------------------------------------------------------


@app.get("/api/current-sandbox")
async def get_current_sandbox():
    return {"id": _current_sandbox_id}


@app.get("/api/sandboxes/{sandbox_id}")
async def get_sandbox(sandbox_id: str):
    s = await dbmod.get_sandbox(_get_db(), sandbox_id)
    if not s:
        raise HTTPException(404)
    domains = await dbmod.get_domain_decisions(_get_db(), sandbox_id)
    return {**s, "domains": domains}


@app.get("/api/sandboxes/{sandbox_id}/terminal-log")
async def get_terminal_log(sandbox_id: str):
    rows = await dbmod.get_terminal_log(_get_db(), sandbox_id)
    # return as base64-encoded chunks
    import base64

    return [{"ts": r["ts"], "data": base64.b64encode(r["data"]).decode()} for r in rows]


# -- REST: filesystem binds ------------------------------------------------


def _profile_binds(profile_name: str, cwd: str) -> list[dict]:
    from .profiles import PROFILES

    p = PROFILES.get(profile_name)
    if p is None:
        return []
    result = [{"host": h, "dest": d, "mode": "rw"} for h, d in p.extra_binds]
    result += [{"host": h, "dest": d, "mode": "ro"} for h, d in p.extra_ro_binds]
    for rel in p.ensure_home_dirs:
        result.append(
            {"host": str(Path.home() / rel), "dest": str(Path(cwd) / rel), "mode": "rw"}
        )
    return result


def _read_fs_binds_file(cwd: str) -> list[dict]:
    p = Path(cwd) / ".aleash-fs-binds.json"
    try:
        data = json.loads(p.read_text())
        return [
            {
                "host": b["host"],
                "dest": b.get("dest", b["host"]),
                "mode": b.get("mode", "ro"),
            }
            for b in data.get("binds", [])
        ]
    except (OSError, json.JSONDecodeError, KeyError):
        return []


def _write_fs_binds_file(cwd: str, binds: list[dict]) -> None:
    data = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "binds": binds,
    }
    try:
        (Path(cwd) / ".aleash-fs-binds.json").write_text(
            json.dumps(data, indent=2) + "\n"
        )
    except OSError:
        pass


@app.get("/api/sandboxes/{sandbox_id}/filesystem")
async def get_filesystem_binds(sandbox_id: str):
    from .bwrap import SYSTEM_BIND_DISPLAY

    s = await dbmod.get_sandbox(_get_db(), sandbox_id)
    if not s:
        raise HTTPException(404)
    system = [
        {"host": h, "dest": d, "mode": m}
        for h, d, m in SYSTEM_BIND_DISPLAY
        if Path(h).exists()
    ]
    system.append({"host": s["cwd"], "dest": s["cwd"], "mode": "rw"})
    return {
        "system": system,
        "profile": _profile_binds(s["profile"], s["cwd"]),
        "user": _read_fs_binds_file(s["cwd"]),
    }


class FilesystemBindsRequest(BaseModel):
    binds: list[dict]


@app.put("/api/sandboxes/{sandbox_id}/filesystem")
async def put_filesystem_binds(sandbox_id: str, req: FilesystemBindsRequest):
    s = await dbmod.get_sandbox(_get_db(), sandbox_id)
    if not s:
        raise HTTPException(404)
    _write_fs_binds_file(s["cwd"], req.binds)
    return {"ok": True}


# -- REST: services --------------------------------------------------------


def _read_services_config(cwd: str) -> dict:
    p = Path(cwd) / ".aleash-services.json"
    try:
        data = json.loads(p.read_text())
        return data.get("services", {})
    except (OSError, json.JSONDecodeError):
        return {}


def _write_services_config(cwd: str, services: dict) -> None:
    data = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "services": services,
    }
    try:
        (Path(cwd) / ".aleash-services.json").write_text(
            json.dumps(data, indent=2) + "\n"
        )
    except OSError:
        pass


def _services_status(cwd: str) -> list[dict]:
    config = _read_services_config(cwd)
    result = []
    for svc_id, svc_def in SERVICES.items():
        sock = svc_def.resolve_socket()
        result.append(
            {
                "id": svc_id,
                "label": svc_def.label,
                "description": svc_def.description,
                "enabled": config.get(svc_id, {}).get("enabled", svc_def.default_enabled),
                "available": sock is not None,
                "socket": sock,
            }
        )
    return result


@app.get("/api/sandboxes/{sandbox_id}/services")
async def get_services(sandbox_id: str):
    s = await dbmod.get_sandbox(_get_db(), sandbox_id)
    if not s:
        raise HTTPException(404)
    return {"services": _services_status(s["cwd"])}


class ServicesRequest(BaseModel):
    services: dict


@app.put("/api/sandboxes/{sandbox_id}/services")
async def put_services(sandbox_id: str, req: ServicesRequest):
    s = await dbmod.get_sandbox(_get_db(), sandbox_id)
    if not s:
        raise HTTPException(404)
    enabled = {k for k, v in req.services.items() if v.get("enabled", False)}
    if len(enabled & SSH_AGENT_SERVICES) > 1:
        raise HTTPException(422, "ssh_agent and onepassword are mutually exclusive")
    _write_services_config(s["cwd"], req.services)
    return {"ok": True}


# -- REST: proxy approval ---------------------------------------------------


class CheckRequest(BaseModel):
    sandbox_id: str
    domain: str


@app.post("/api/proxy/request-approval")
async def request_approval(req: CheckRequest):
    # check existing decision
    decision = await dbmod.get_domain_decision(_get_db(), req.sandbox_id, req.domain)
    if decision is not None:
        return {"action": "allow" if decision else "block"}

    domain_key = (req.sandbox_id, req.domain)

    # if another request is already waiting for this domain, share its future
    existing_fut = _pending_by_domain.get(domain_key)
    if existing_fut is not None:
        try:
            allowed = await asyncio.wait_for(asyncio.shield(existing_fut), timeout=60.0)
        except asyncio.TimeoutError:
            allowed = False
        return {"action": "allow" if allowed else "block"}

    # first request for this domain: register future before any await so
    # concurrent requests see it in _pending_by_domain immediately
    approval_id = str(uuid.uuid4())
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _approval_futures[approval_id] = fut
    _pending_by_domain[domain_key] = fut

    try:
        now = int(time.time() * 1000)
        await dbmod.create_pending_approval(
            _get_db(), approval_id, req.sandbox_id, req.domain, now
        )

        msg = {
            "type": "approval_request",
            "approval_id": approval_id,
            "sandbox_id": req.sandbox_id,
            "domain": req.domain,
        }
        await _broadcast(_approval_sockets, msg)

        allowed = await asyncio.wait_for(asyncio.shield(fut), timeout=60.0)
    except asyncio.TimeoutError:
        allowed = False
    finally:
        _approval_futures.pop(approval_id, None)
        _pending_by_domain.pop(domain_key, None)

    return {"action": "allow" if allowed else "block"}


class DecideRequest(BaseModel):
    approved: bool
    permanent: bool = True  # if False, don't store in domain_decisions


# -- REST: internal (called by runner process) -----------------------------


class SandboxStartPayload(BaseModel):
    id: str
    profile: str
    cwd: str
    cmd: str
    started_at: int
    browser_master: bool = False


class SandboxEndPayload(BaseModel):
    id: str
    ended_at: int
    exit_code: int


class TerminalResizePayload(BaseModel):
    id: str
    cols: int
    rows: int
    browser_master: bool = False


@app.post("/api/internal/sandbox-start")
async def internal_sandbox_start(payload: SandboxStartPayload):
    global _current_sandbox_id
    _current_sandbox_id = payload.id
    await dbmod.insert_sandbox(
        _get_db(),
        payload.id,
        payload.profile,
        payload.cwd,
        payload.cmd,
        payload.started_at,
    )
    _sandbox_browser_master[payload.id] = payload.browser_master
    perms_file = Path(payload.cwd) / ".aleash-network-permissions.json"
    if perms_file.exists():
        try:
            data = json.loads(perms_file.read_text())
            now = int(time.time() * 1000)
            for domain, verdict in data.get("domains", {}).items():
                await dbmod.set_domain_decision(
                    _get_db(), payload.id, domain, verdict == "allow", now
                )
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    await notify_sandbox_update()
    return {"ok": True}


@app.post("/api/internal/terminal-resize")
async def internal_terminal_resize(payload: TerminalResizePayload):
    _sandbox_sizes[payload.id] = (payload.cols, payload.rows)
    _sandbox_browser_master[payload.id] = payload.browser_master
    # push new size to all connected browser clients
    msg = {"type": "resize", "cols": payload.cols, "rows": payload.rows}
    await _broadcast(_terminal_sockets.get(payload.id, []), msg)
    return {"ok": True}


@app.post("/api/internal/sandbox-end")
async def internal_sandbox_end(payload: SandboxEndPayload):
    global _current_sandbox_id
    if payload.id == _current_sandbox_id:
        _current_sandbox_id = None
    await dbmod.finish_sandbox(
        _get_db(), payload.id, payload.ended_at, payload.exit_code
    )
    await notify_sandbox_update()
    writer = _pty_input_writers.pop(payload.id, None)
    if writer:
        writer.close()
    return {"ok": True}


class DomainUpdateRequest(BaseModel):
    allowed: bool


@app.put("/api/sandboxes/{sandbox_id}/domains/{domain}")
async def update_domain_decision(
    sandbox_id: str, domain: str, req: DomainUpdateRequest
):
    await dbmod.set_domain_decision(
        _get_db(), sandbox_id, domain, req.allowed, int(time.time() * 1000)
    )
    await _write_permissions_file(sandbox_id)
    await notify_sandbox_update()
    return {"ok": True}


@app.post("/api/approvals/{approval_id}/decide")
async def decide_approval(approval_id: str, req: DecideRequest):
    result = await dbmod.resolve_pending_approval(_get_db(), approval_id, req.approved)
    if result is None:
        raise HTTPException(404)
    sandbox_id, domain = result
    if req.permanent:
        await dbmod.set_domain_decision(
            _get_db(), sandbox_id, domain, req.approved, int(time.time() * 1000)
        )
        await _write_permissions_file(sandbox_id)
        await notify_sandbox_update()
    fut = _approval_futures.get(approval_id)
    if fut and not fut.done():
        fut.set_result(req.approved)
    return {"ok": True}


# -- WebSocket: terminal ---------------------------------------------------


@app.websocket("/ws/terminal/{sandbox_id}")
async def terminal_ws(websocket: WebSocket, sandbox_id: str):
    import base64

    await websocket.accept()
    _terminal_sockets.setdefault(sandbox_id, []).append(websocket)

    # Send init message: PTY size + master mode.
    cols, rows = _sandbox_sizes.get(sandbox_id, (220, 50))
    bm = _sandbox_browser_master.get(sandbox_id, False)
    await websocket.send_json(
        {
            "type": "init",
            "cols": cols,
            "rows": rows,
            "browser_master": bm,
            "instance_id": _instance_id,
        }
    )

    # Replay existing log then track last row id for incremental polling.
    # The runner writes to terminal_log directly (different process), so we
    # cannot use in-process broadcast — we poll the DB instead.
    log_rows = await dbmod.get_terminal_log(_get_db(), sandbox_id)
    for row in log_rows:
        await websocket.send_json(
            {"type": "output", "data": base64.b64encode(row["data"]).decode()}
        )
    last_id = await dbmod.get_terminal_log_last_id(_get_db(), sandbox_id)

    async def _poll_output():
        nonlocal last_id
        while True:
            await asyncio.sleep(0.05)
            new_rows = await dbmod.get_terminal_log_after(
                _get_db(), sandbox_id, last_id
            )
            for row in new_rows:
                last_id = row["id"]
                await websocket.send_json(
                    {"type": "output", "data": base64.b64encode(row["data"]).decode()}
                )

    async def _recv_input():
        while True:
            msg = await websocket.receive_json()
            if msg.get("type") == "input":
                await write_pty_input(sandbox_id, base64.b64decode(msg["data"]))
            elif msg.get("type") == "resize" and _sandbox_browser_master.get(
                sandbox_id, False
            ):
                await resize_pty(sandbox_id, msg.get("cols", 80), msg.get("rows", 24))

    poll_task = asyncio.create_task(_poll_output())
    try:
        await _recv_input()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        poll_task.cancel()
        try:
            await poll_task
        except (asyncio.CancelledError, Exception):
            pass
        sockets = _terminal_sockets.get(sandbox_id, [])
        try:
            sockets.remove(websocket)
        except ValueError:
            pass


# -- WebSocket: approvals --------------------------------------------------


@app.websocket("/ws/approvals")
async def approvals_ws(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json({"type": "hello", "instance_id": _instance_id})
    _approval_sockets.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            _approval_sockets.remove(websocket)
        except ValueError:
            pass


# -- WebSocket: sandbox list -----------------------------------------------


@app.websocket("/ws/sandboxes")
async def sandboxes_ws(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json({"type": "hello", "instance_id": _instance_id})
    _sandbox_list_sockets.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            _sandbox_list_sockets.remove(websocket)
        except ValueError:
            pass


# -- permissions file ------------------------------------------------------


async def _write_permissions_file(sandbox_id: str) -> None:
    sandbox = await dbmod.get_sandbox(_get_db(), sandbox_id)
    if sandbox is None:
        return
    decisions = await dbmod.get_domain_decisions(_get_db(), sandbox_id)
    domains = {d["domain"]: ("allow" if d["allowed"] else "block") for d in decisions}
    data = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "domains": domains,
    }
    try:
        (Path(sandbox["cwd"]) / ".aleash-network-permissions.json").write_text(
            json.dumps(data, indent=2) + "\n"
        )
    except OSError:
        pass


# -- helpers called by runner ----------------------------------------------


async def broadcast_terminal_output(sandbox_id: str, data: bytes) -> None:
    import base64

    sockets = _terminal_sockets.get(sandbox_id, [])
    await _broadcast(
        sockets, {"type": "output", "data": base64.b64encode(data).decode()}
    )


async def notify_sandbox_update() -> None:
    await _broadcast(_sandbox_list_sockets, {"type": "sandboxes_updated"})


# -- SPA static files ------------------------------------------------------

_assets_dir = STATIC_DIR / "assets"
if _assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str):
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return HTMLResponse(
            "<h1>Frontend not built</h1>"
            "<p>Run: <code>cd frontend && npm run build</code></p>"
        )
    return FileResponse(index)
