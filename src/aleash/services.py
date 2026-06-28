import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class ServiceDef:
    id: str
    label: str
    description: str
    _resolve_fn: Callable[[], str | None] = field(repr=False)
    _bwrap_fn: Callable[[str], tuple[list[tuple[str, str, str]], dict[str, str]]] = (
        field(repr=False)
    )
    default_enabled: bool = False

    def resolve_socket(self) -> str | None:
        return self._resolve_fn()

    def bwrap_args(
        self, socket: str
    ) -> tuple[list[tuple[str, str, str]], dict[str, str]]:
        return self._bwrap_fn(socket)


def _resolve_ssh_agent() -> str | None:
    sock = os.environ.get("SSH_AUTH_SOCK", "")
    if sock and Path(sock).is_socket():
        return sock
    return None


def _resolve_gpg_agent() -> str | None:
    try:
        result = subprocess.check_output(
            ["gpgconf", "--list-dirs", "agent-socket"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if result and Path(result).is_socket():
            return result
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass
    fallback = Path.home() / ".gnupg" / "S.gpg-agent"
    if fallback.is_socket():
        return str(fallback)
    return None


def _bwrap_gpg(socket: str) -> tuple[list[tuple[str, str, str]], dict[str, str]]:
    binds: list[tuple[str, str, str]] = [(socket, socket, "rw")]
    gnupg = Path.home() / ".gnupg"
    if gnupg.is_dir():
        binds.append((str(gnupg), str(gnupg), "ro"))
    return binds, {"GPG_AGENT_INFO": f"{socket}:0:1"}


def _resolve_docker() -> str | None:
    docker_host = os.environ.get("DOCKER_HOST", "")
    if docker_host.startswith("unix://"):
        sock = docker_host[len("unix://") :]
    elif docker_host and not docker_host.startswith("tcp://"):
        sock = docker_host
    else:
        sock = "/var/run/docker.sock"
    if sock and Path(sock).is_socket():
        return sock
    return None


def _resolve_onepassword() -> str | None:
    sock = Path.home() / ".1password" / "agent.sock"
    if sock.is_socket():
        return str(sock)
    return None


def _resolve_podman() -> str | None:
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    sock = Path(xdg_runtime) / "podman" / "podman.sock"
    if sock.is_socket():
        return str(sock)
    return None


def _resolve_browser() -> str | None:
    return shutil.which("xdg-dbus-proxy")


SERVICES: dict[str, ServiceDef] = {
    "ssh_agent": ServiceDef(
        id="ssh_agent",
        label="SSH Agent",
        description="Forward SSH agent socket for git SSH remotes and remote login",
        _resolve_fn=_resolve_ssh_agent,
        _bwrap_fn=lambda s: ([(s, s, "rw")], {"SSH_AUTH_SOCK": s}),
    ),
    "gpg_agent": ServiceDef(
        id="gpg_agent",
        label="GPG Agent",
        description="Forward GPG agent socket for signed commits and encrypted secrets",
        _resolve_fn=_resolve_gpg_agent,
        _bwrap_fn=_bwrap_gpg,
    ),
    "docker": ServiceDef(
        id="docker",
        label="Docker",
        description="Forward Docker daemon socket for container operations",
        _resolve_fn=_resolve_docker,
        _bwrap_fn=lambda s: ([(s, s, "rw")], {"DOCKER_HOST": f"unix://{s}"}),
    ),
    "onepassword": ServiceDef(
        id="onepassword",
        label="1Password SSH Agent",
        description="Use 1Password as SSH agent (sets SSH_AUTH_SOCK)",
        _resolve_fn=_resolve_onepassword,
        _bwrap_fn=lambda s: ([(s, s, "rw")], {"SSH_AUTH_SOCK": s}),
    ),
    "podman": ServiceDef(
        id="podman",
        label="Podman",
        description="Forward Podman daemon socket for container operations",
        _resolve_fn=_resolve_podman,
        _bwrap_fn=lambda s: ([(s, s, "rw")], {"CONTAINER_HOST": f"unix://{s}"}),
    ),
    "browser": ServiceDef(
        id="browser",
        label="Browser",
        description="Open URLs from the sandbox in the host browser via xdg-open",
        _resolve_fn=_resolve_browser,
        _bwrap_fn=lambda _: ([], {}),
        default_enabled=True,
    ),
    "notifications": ServiceDef(
        id="notifications",
        label="Notifications",
        description="Forward desktop notifications from sandbox to host via notify-send",
        _resolve_fn=_resolve_browser,
        _bwrap_fn=lambda _: ([], {}),
        default_enabled=True,
    ),
}

# Services that set SSH_AUTH_SOCK — only one may be enabled at a time
SSH_AGENT_SERVICES = {"ssh_agent", "onepassword"}
