from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Profile:
    name: str
    binary_names: list[str]
    extra_binds: list[tuple[str, str]] = field(default_factory=list)
    extra_ro_binds: list[tuple[str, str]] = field(default_factory=list)
    extra_env: dict[str, str] = field(default_factory=dict)
    # Paths under $HOME to create (rw tmpfs) if they don't exist yet
    ensure_home_dirs: list[str] = field(default_factory=list)


def _home(*parts: str) -> str:
    return str(Path.home().joinpath(*parts))


PROFILES: dict[str, Profile] = {}


def _register(p: Profile) -> Profile:
    PROFILES[p.name] = p
    return p


_register(Profile(
    name="claude",
    binary_names=["claude"],
    extra_binds=[
        (_home(".claude"), _home(".claude")),
        (_home(".claude.json"), _home(".claude.json")),
        (_home(".gitconfig"), _home(".gitconfig")),
        (_home(".local", "share", "claude"), _home(".local", "share", "claude")),
    ],
))

_register(Profile(
    name="opencode",
    binary_names=["opencode"],
    extra_binds=[
        (_home(".gitconfig"), _home(".gitconfig")),
        (_home(".opencode"), _home(".opencode")),
    ],
    ensure_home_dirs=[
        ".config/opencode",
        ".local/share/opencode",
        ".local/state/opencode",
        ".cache/opencode",
    ],
))

_register(Profile(
    name="generic",
    binary_names=[],
))


def detect_profile(binary: str) -> Profile:
    for p in PROFILES.values():
        if binary in p.binary_names:
            return p
    return PROFILES["generic"]
