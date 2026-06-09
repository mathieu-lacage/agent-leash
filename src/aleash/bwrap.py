import os
from pathlib import Path

from .profiles import Profile

# Prefixes already mounted inside the sandbox — binary paths under these need no extra bind.
_MOUNTED_PREFIXES = ("/usr", "/etc", "/lib", "/lib64", "/lib32", "/sys", "/run", "/dev", "/proc",
                     str(Path.home() / ".local" / "bin"))

# Real host-path bind mounts from the fixed section of build_bwrap_argv.
# Excludes --tmpfs, --proc, --symlink, and internal sockets/certs.
# Used by the server to display system mounts without duplicating this list.
SYSTEM_BIND_DISPLAY: list[tuple[str, str, str]] = [
    ("/dev",   "/dev",   "rw"),
    ("/usr",   "/usr",   "ro"),
    ("/etc",   "/etc",   "ro"),
    ("/lib",   "/lib",   "ro"),
    ("/lib64", "/lib64", "ro"),
    ("/lib32", "/lib32", "ro"),
    ("/sys",   "/sys",   "ro"),
]


def _outside_mounts(path: str) -> bool:
    return not any(path == p or path.startswith(p + "/") for p in _MOUNTED_PREFIXES)


FLATPAK_INFO_CONTENT = """\
[Application]
name=fake.app.Id

[Instance]
app-id=fake.app.Id
"""


def _uid() -> str:
    return str(os.getuid())


def _xdg_runtime() -> str:
    return os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{_uid()}")


def build_bwrap_argv(
    profile: Profile,
    cwd: str,
    proxy_port: int,
    xdg_proxy_sock: str,
    ca_cert_path: str,
    fake_flatpak_info: str,
    cmd: list[str],
    user_binds: list[tuple[str, str, str]] | None = None,
) -> list[str]:
    uid = _uid()
    xdg_runtime = _xdg_runtime()
    podman_sock = f"{xdg_runtime}/podman/podman.sock"
    ca_dest = "/tmp/aleash-ca.pem"
    proxy_url = f"http://127.0.0.1:{proxy_port}"

    args = [
        "bwrap",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-ipc",
        "--share-net",
        "--die-with-parent",
        "--dev-bind", "/dev", "/dev",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/etc", "/etc",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind-try", "/lib64", "/lib64",
        "--ro-bind-try", "/lib32", "/lib32",
        "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/sbin", "/sbin",
        "--ro-bind", "/sys", "/sys",
        "--tmpfs", "/sys/fs/cgroup",
        "--proc", "/proc",
        "--tmpfs", f"/run/user/{uid}",
        "--tmpfs", "/tmp",
        # CA cert: bind into /tmp (writable tmpfs, must come after --tmpfs /tmp above)
        "--ro-bind", ca_cert_path, ca_dest,
        # flatpak-info for portal auth
        "--ro-bind", fake_flatpak_info, f"/run/user/{uid}/flatpak-info",
        # xdg-dbus-proxy socket
        "--bind", xdg_proxy_sock, xdg_proxy_sock,
        # environment

        "--setenv", "http_proxy", proxy_url,
        "--setenv", "HTTP_PROXY", proxy_url,
        "--setenv", "https_proxy", proxy_url,
        "--setenv", "HTTPS_PROXY", proxy_url,
        "--setenv", "SSL_CERT_FILE", ca_dest,
        "--setenv", "REQUESTS_CA_BUNDLE", ca_dest,
        "--setenv", "NODE_EXTRA_CA_CERTS", ca_dest,
        "--setenv", "DBUS_SESSION_BUS_ADDRESS", f"unix:path={xdg_proxy_sock}",
        "--setenv", "CONTAINER_HOST", f"unix://{podman_sock}",
    ]

    # Collect content binds then sort shortest-dest-first so parent RO mounts
    # are always applied before child RW mounts. bwrap's --ro-bind uses
    # MS_RDONLY|MS_REC which would retroactively RO any child mount that
    # already existed — sorting prevents that.
    content: list[tuple[str, str, str]] = []  # (flag, host, dest)

    # ~/.local/bin — user-installed binaries (pipx, npm, cargo, etc.)
    local_bin = Path.home() / ".local" / "bin"
    if local_bin.is_dir():
        content.append(("--ro-bind", str(local_bin), str(local_bin)))

    # podman socket
    if Path(podman_sock).exists():
        content.append(("--bind", podman_sock, podman_sock))

    # working directory (writable)
    content.append(("--bind", cwd, cwd))

    # profile extra binds
    for host, dest in profile.extra_binds:
        if Path(host).exists():
            content.append(("--bind", host, dest))
    for host, dest in profile.extra_ro_binds:
        if Path(host).exists():
            content.append(("--ro-bind", host, dest))

    # profile ensure_home_dirs: bind real dirs (create if needed) into cwd/subpath
    for rel in profile.ensure_home_dirs:
        host_path = Path.home() / rel
        host_path.mkdir(parents=True, exist_ok=True)
        content.append(("--bind", str(host_path), str(Path(cwd) / rel)))

    # user-configured extra binds (from .aleash-fs-binds.json)
    for host, dest, mode in (user_binds or []):
        if Path(host).exists():
            content.append(("--bind" if mode == "rw" else "--ro-bind", host, dest))

    content.sort(key=lambda b: len(b[2]))
    for flag, host, dest in content:
        args += [flag, host, dest]

    args += ["--chdir", cwd]

    # extra env from profile
    for k, v in profile.extra_env.items():
        args += ["--setenv", k, v]

    # auto-bind the command binary if it lives outside already-mounted paths
    # (e.g. ~/.local/bin/claude installed via pipx/npm)
    binary = cmd[0]
    if os.path.isabs(binary):
        for p in dict.fromkeys([binary, os.path.realpath(binary)]):  # dedup, preserve order
            if _outside_mounts(p) and os.path.exists(p):
                args += ["--ro-bind", p, p]

    args += ["--", *cmd]
    return args


def write_fake_flatpak_info(tmp_dir: str) -> str:
    p = Path(tmp_dir) / "flatpak-info"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FLATPAK_INFO_CONTENT)
    return str(p)
