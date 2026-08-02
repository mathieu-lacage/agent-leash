import asyncio
import os
import socket
import sys
from pathlib import Path

import click

DAEMON_DIR = Path.home() / ".aleash"


def _free_port(preferred: int = 7612) -> int:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server_background(port: int):
    import threading
    import uvicorn
    from .server import app as _app

    ready = threading.Event()
    config = uvicorn.Config(_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    orig_startup = server.startup

    async def _patched_startup(sockets=None):
        await orig_startup(sockets)
        ready.set()

    server.startup = _patched_startup
    threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True).start()
    ready.wait(timeout=5.0)
    return server


@click.group()
@click.option(
    "--profile", default=None, metavar="PROFILE", help="Override sandbox profile."
)
@click.option(
    "--browser-master",
    is_flag=True,
    default=False,
    help="Let the browser control terminal size.",
)
@click.version_option(package_name="agent-leash")
@click.pass_context
def main(ctx, profile, browser_master):
    """Sandbox runner for AI coding agents."""
    from . import db as _db

    if db_path_env := os.environ.get("ALEASH_DB_PATH"):
        _db.DB_PATH = Path(db_path_env)
    else:
        _db.DB_PATH = Path.cwd() / ".aleash" / "data.db"
    _db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    ctx.ensure_object(dict)
    ctx.obj["profile"] = profile
    ctx.obj["browser_master"] = browser_master


@main.command(name="list")
def list_sandboxes():
    """List sandboxes."""
    import sqlite3
    from .db import DB_PATH

    if not DB_PATH.exists():
        click.echo("No sandboxes yet.")
        return
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM sandboxes ORDER BY started_at DESC"
        ).fetchall()
    if not rows:
        click.echo("No sandboxes yet.")
        return
    for s in rows:
        status = "running" if s["ended_at"] is None else f"exited({s['exit_code']})"
        click.echo(f"{s['id'][:8]}  {status:12}  {s['profile']:10}  {s['cwd']}")


def _run_agent(
    profile_name: str,
    extra_args: tuple,
    profile_override: str | None,
    browser_master: bool = False,
    forward_stdin: bool = False,
):
    from .profiles import PROFILES
    from .runner import run_sandbox

    profile_key = profile_override or profile_name
    if profile_key not in PROFILES:
        click.echo(
            f"Unknown profile '{profile_key}'. Available: {', '.join(PROFILES)}",
            err=True,
        )
        sys.exit(1)
    profile = PROFILES[profile_key]

    import shutil

    binary = shutil.which(profile_name)
    if not binary:
        click.echo(f"'{profile_name}' not found on PATH", err=True)
        sys.exit(1)

    port = _free_port()
    server = _start_server_background(port)
    if not os.environ.get("ALEASH_NO_BROWSER"):
        import subprocess

        click.echo(f"Sandbox UI available on http://localhost:{port}/")
        subprocess.Popen(
            ["xdg-open", f"http://localhost:{port}/"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    try:
        exit_code = asyncio.run(
            run_sandbox(
                profile=profile,
                cwd=os.getcwd(),
                cmd=[binary, *extra_args],
                browser_master=browser_master,
                server_url=f"http://localhost:{port}",
                forward_stdin=forward_stdin,
            )
        )
    finally:
        server.should_exit = True
    sys.exit(exit_code)


@main.command(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.option(
    "--forward-stdin",
    is_flag=True,
    default=False,
    help="Forward host stdin to the sandbox (e.g. for piping data in).",
)
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def claude(ctx, forward_stdin, extra_args):
    """Run claude in a sandbox."""
    _run_agent(
        "claude",
        extra_args,
        ctx.obj.get("profile"),
        ctx.obj.get("browser_master", False),
        forward_stdin=forward_stdin,
    )


@main.command(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.option(
    "--forward-stdin",
    is_flag=True,
    default=False,
    help="Forward host stdin to the sandbox (e.g. for piping data in).",
)
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def opencode(ctx, forward_stdin, extra_args):
    """Run opencode in a sandbox."""
    _run_agent(
        "opencode",
        extra_args,
        ctx.obj.get("profile"),
        ctx.obj.get("browser_master", False),
        forward_stdin=forward_stdin,
    )


@main.command(
    name="run",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.option(
    "--forward-stdin",
    is_flag=True,
    default=False,
    help="Forward host stdin to the sandbox (e.g. for piping data in).",
)
@click.argument("cmd", nargs=-1, type=click.UNPROCESSED, required=True)
@click.pass_context
def run_cmd(ctx, forward_stdin, cmd):
    """Run an arbitrary command in a sandbox."""
    from .profiles import PROFILES
    from .runner import run_sandbox

    profile = ctx.obj.get("profile") or "generic"
    if profile not in PROFILES:
        click.echo(
            f"Unknown profile '{profile}'. Available: {', '.join(PROFILES)}", err=True
        )
        sys.exit(1)

    server_url = os.environ.get("ALEASH_SERVER_URL")
    server = None
    if server_url is None:
        port = _free_port()
        server = _start_server_background(port)
        server_url = f"http://localhost:{port}"
        if not os.environ.get("ALEASH_NO_BROWSER"):
            import subprocess

            click.echo(f"Sandbox UI available on {server_url}/")
            subprocess.Popen(
                ["xdg-open", f"{server_url}/"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    try:
        exit_code = asyncio.run(
            run_sandbox(
                profile=PROFILES[profile],
                cwd=os.getcwd(),
                cmd=list(cmd),
                browser_master=ctx.obj.get("browser_master", False),
                server_url=server_url,
                forward_stdin=forward_stdin,
            )
        )
    finally:
        if server is not None:
            server.should_exit = True
    sys.exit(exit_code)
