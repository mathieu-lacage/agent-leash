import re
import aiosqlite
from pathlib import Path

DB_PATH = Path.home() / ".aleash" / "data.db"

_ANSI_RE = re.compile(rb"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi(data: bytes) -> str:
    return _ANSI_RE.sub(b"", data).decode("utf-8", errors="replace")


async def get_db() -> aiosqlite.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db(db: aiosqlite.Connection) -> None:
    # Check if terminal_fts already exists before creating it (for backfill detection).
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='terminal_fts'"
    ) as cur:
        fts_existed = await cur.fetchone() is not None

    await db.executescript("""
        CREATE TABLE IF NOT EXISTS sandboxes (
            id          TEXT PRIMARY KEY,
            profile     TEXT NOT NULL,
            cwd         TEXT NOT NULL,
            cmd         TEXT NOT NULL,
            started_at  INTEGER NOT NULL,
            ended_at    INTEGER,
            exit_code   INTEGER
        );

        CREATE TABLE IF NOT EXISTS terminal_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sandbox_id  TEXT NOT NULL REFERENCES sandboxes(id),
            ts          INTEGER NOT NULL,
            data        BLOB NOT NULL
        );

        CREATE TABLE IF NOT EXISTS domain_decisions (
            sandbox_id  TEXT NOT NULL REFERENCES sandboxes(id),
            domain      TEXT NOT NULL,
            allowed     INTEGER NOT NULL,
            decided_at  INTEGER NOT NULL,
            PRIMARY KEY (sandbox_id, domain)
        );

        CREATE TABLE IF NOT EXISTS pending_approvals (
            id          TEXT PRIMARY KEY,
            sandbox_id  TEXT NOT NULL REFERENCES sandboxes(id),
            domain      TEXT NOT NULL,
            requested_at INTEGER NOT NULL,
            decided     INTEGER
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS terminal_fts USING fts5(
            sandbox_id UNINDEXED,
            text,
            tokenize='unicode61'
        );
    """)
    await db.commit()

    # One-time backfill: populate FTS from existing terminal_log rows.
    if not fts_existed:
        async with db.execute("SELECT sandbox_id, data FROM terminal_log") as cur:
            async for row in cur:
                text = _strip_ansi(bytes(row["data"]))
                if text.strip():
                    await db.execute(
                        "INSERT INTO terminal_fts(sandbox_id, text) VALUES (?,?)",
                        (row["sandbox_id"], text),
                    )
        await db.commit()


async def insert_sandbox(
    db: aiosqlite.Connection,
    sandbox_id: str,
    profile: str,
    cwd: str,
    cmd: str,
    started_at: int,
) -> None:
    await db.execute(
        "INSERT INTO sandboxes (id, profile, cwd, cmd, started_at) VALUES (?,?,?,?,?)",
        (sandbox_id, profile, cwd, cmd, started_at),
    )
    await db.commit()


async def finish_sandbox(
    db: aiosqlite.Connection, sandbox_id: str, ended_at: int, exit_code: int
) -> None:
    await db.execute(
        "UPDATE sandboxes SET ended_at=?, exit_code=? WHERE id=?",
        (ended_at, exit_code, sandbox_id),
    )
    await db.commit()


async def append_terminal_log(
    db: aiosqlite.Connection, sandbox_id: str, ts: int, data: bytes
) -> None:
    await db.execute(
        "INSERT INTO terminal_log (sandbox_id, ts, data) VALUES (?,?,?)",
        (sandbox_id, ts, data),
    )
    text = _strip_ansi(data)
    if text.strip():
        await db.execute(
            "INSERT INTO terminal_fts(sandbox_id, text) VALUES (?,?)",
            (sandbox_id, text),
        )
    await db.commit()


async def get_terminal_log_last_id(db: aiosqlite.Connection, sandbox_id: str) -> int:
    async with db.execute(
        "SELECT COALESCE(MAX(id), 0) as last_id FROM terminal_log WHERE sandbox_id=?",
        (sandbox_id,),
    ) as cur:
        row = await cur.fetchone()
        assert row is not None
        return int(row["last_id"])


async def get_terminal_log_after(
    db: aiosqlite.Connection, sandbox_id: str, after_id: int
) -> list[dict]:
    async with db.execute(
        "SELECT id, data FROM terminal_log WHERE sandbox_id=? AND id>? ORDER BY id LIMIT 200",
        (sandbox_id, after_id),
    ) as cur:
        return [{"id": r["id"], "data": bytes(r["data"])} for r in await cur.fetchall()]


async def get_terminal_log(db: aiosqlite.Connection, sandbox_id: str) -> list[dict]:
    async with db.execute(
        "SELECT ts, data FROM terminal_log WHERE sandbox_id=? ORDER BY id",
        (sandbox_id,),
    ) as cur:
        return [{"ts": r["ts"], "data": bytes(r["data"])} for r in await cur.fetchall()]


async def get_domain_decision(
    db: aiosqlite.Connection, sandbox_id: str, domain: str
) -> int | None:
    async with db.execute(
        "SELECT allowed FROM domain_decisions WHERE sandbox_id=? AND domain=?",
        (sandbox_id, domain),
    ) as cur:
        row = await cur.fetchone()
        return int(row["allowed"]) if row else None


async def set_domain_decision(
    db: aiosqlite.Connection,
    sandbox_id: str,
    domain: str,
    allowed: bool,
    decided_at: int,
) -> None:
    await db.execute(
        """INSERT INTO domain_decisions (sandbox_id, domain, allowed, decided_at)
           VALUES (?,?,?,?)
           ON CONFLICT(sandbox_id, domain) DO UPDATE SET allowed=excluded.allowed, decided_at=excluded.decided_at""",
        (sandbox_id, domain, int(allowed), decided_at),
    )
    await db.commit()


async def create_pending_approval(
    db: aiosqlite.Connection,
    approval_id: str,
    sandbox_id: str,
    domain: str,
    requested_at: int,
) -> None:
    await db.execute(
        "INSERT INTO pending_approvals (id, sandbox_id, domain, requested_at) VALUES (?,?,?,?)",
        (approval_id, sandbox_id, domain, requested_at),
    )
    await db.commit()


async def resolve_pending_approval(
    db: aiosqlite.Connection, approval_id: str, decided: bool
) -> tuple[str, str] | None:
    async with db.execute(
        "SELECT sandbox_id, domain FROM pending_approvals WHERE id=?", (approval_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    await db.execute(
        "UPDATE pending_approvals SET decided=? WHERE id=?",
        (int(decided), approval_id),
    )
    await db.commit()
    return row["sandbox_id"], row["domain"]


async def get_sandbox(db: aiosqlite.Connection, sandbox_id: str) -> dict | None:
    async with db.execute("SELECT * FROM sandboxes WHERE id=?", (sandbox_id,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_domain_decisions(db: aiosqlite.Connection, sandbox_id: str) -> list[dict]:
    async with db.execute(
        "SELECT domain, allowed, decided_at FROM domain_decisions WHERE sandbox_id=? ORDER BY rowid",
        (sandbox_id,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]
