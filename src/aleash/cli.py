import asyncio
import os
import signal
import sys
import time
from pathlib import Path

import click

DAEMON_DIR = Path.home() / ".aleash"
PID_FILE = DAEMON_DIR / "server.pid"
PORT = 7612


def _server_running() -> int | None:
    if not PID_FILE.exists():
        return None
    pid = int(PID_FILE.read_text().strip())
    try:
        os.kill(pid, 0)
        return pid
    except ProcessLookupError:
        PID_FILE.unlink(missing_ok=True)
        return None


@click.group()
@click.option("--profile", default=None, metavar="PROFILE", help="Override sandbox profile for the agent command.")
@click.option("--browser-master", is_flag=True, default=False, help="Let the browser control terminal size (default: local terminal is master).")
@click.pass_context
def main(ctx, profile, browser_master):
    """Sandbox runner for AI coding agents."""
    ctx.ensure_object(dict)
    ctx.obj["profile"] = profile
    ctx.obj["browser_master"] = browser_master


@main.command()
@click.option("--port", default=PORT, show_default=True)
@click.option("--foreground", "-f", is_flag=True, help="Run in foreground (don't daemonize)")
def start(port: int, foreground: bool):
    """Start the sandbox daemon."""
    if _server_running():
        click.echo(f"Sandbox already running. UI: http://localhost:{port}/")
        return

    DAEMON_DIR.mkdir(parents=True, exist_ok=True)

    if foreground:
        _run_server(port)
    else:
        _daemonize(port)


def _run_server(port: int):
    import uvicorn
    from .server import app
    PID_FILE.write_text(str(os.getpid()))
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    finally:
        PID_FILE.unlink(missing_ok=True)


def _daemonize(port: int):
    pid = os.fork()
    if pid > 0:
        # parent: wait briefly for server to start, then return
        time.sleep(0.8)
        click.echo(f"Sandbox UI available on http://localhost:{port}/")
        return

    # child: become daemon
    os.setsid()
    pid2 = os.fork()
    if pid2 > 0:
        os._exit(0)

    # grandchild: redirect stdio
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    log_path = DAEMON_DIR / "server.log"
    log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(devnull)
    os.close(log_fd)

    _run_server(port)
    os._exit(0)


@main.command()
def stop():
    """Stop the sandbox daemon."""
    pid = _server_running()
    if not pid:
        click.echo("Sandbox is not running.")
        return
    os.kill(pid, signal.SIGTERM)
    # wait up to 3s
    for _ in range(30):
        time.sleep(0.1)
        if not _server_running():
            click.echo("Sandbox stopped.")
            return
    click.echo("Sandbox did not stop in time; sending SIGKILL.")
    os.kill(pid, signal.SIGKILL)


@main.command(name="list")
def list_sandboxes():
    """List sandboxes."""
    import httpx
    try:
        r = httpx.get(f"http://localhost:{PORT}/api/sandboxes", timeout=3)
        sandboxes = r.json()
    except Exception:
        click.echo("Sandbox server not running. Try: aleash start")
        return
    if not sandboxes:
        click.echo("No sandboxes yet.")
        return
    for s in sandboxes:
        status = "running" if s["ended_at"] is None else f"exited({s['exit_code']})"
        click.echo(f"{s['id'][:8]}  {status:12}  {s['profile']:10}  {s['cwd']}")


def _run_agent(profile_name: str, extra_args: tuple, profile_override: str | None,
               browser_master: bool = False):
    from .profiles import PROFILES
    from .runner import run_sandbox

    profile_key = profile_override or profile_name
    if profile_key not in PROFILES:
        click.echo(f"Unknown profile '{profile_key}'. Available: {', '.join(PROFILES)}", err=True)
        sys.exit(1)
    profile = PROFILES[profile_key]

    import shutil
    binary = shutil.which(profile_name)
    if not binary:
        click.echo(f"'{profile_name}' not found on PATH", err=True)
        sys.exit(1)

    exit_code = asyncio.run(run_sandbox(
        profile=profile, cwd=os.getcwd(),
        cmd=[binary, *extra_args], browser_master=browser_master,
    ))
    sys.exit(exit_code)


@main.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def claude(ctx, extra_args):
    """Run claude in a sandbox."""
    _run_agent("claude", extra_args, ctx.obj.get("profile"), ctx.obj.get("browser_master", False))


@main.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def opencode(ctx, extra_args):
    """Run opencode in a sandbox."""
    _run_agent("opencode", extra_args, ctx.obj.get("profile"), ctx.obj.get("browser_master", False))


@main.command(name="run", context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.argument("cmd", nargs=-1, type=click.UNPROCESSED, required=True)
@click.pass_context
def run_cmd(ctx, cmd):
    """Run an arbitrary command in a sandbox."""
    from .profiles import PROFILES
    from .runner import run_sandbox

    profile = ctx.obj.get("profile") or "generic"
    if profile not in PROFILES:
        click.echo(f"Unknown profile '{profile}'. Available: {', '.join(PROFILES)}", err=True)
        sys.exit(1)

    exit_code = asyncio.run(run_sandbox(
        profile=PROFILES[profile], cwd=os.getcwd(),
        cmd=list(cmd), browser_master=ctx.obj.get("browser_master", False),
    ))
    sys.exit(exit_code)
