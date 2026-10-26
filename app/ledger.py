"""The ledger: every write we observed, in SQLite. Queries use parameters only."""
import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS writes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seen_at TEXT NOT NULL,
    written_at TEXT NOT NULL,
    system TEXT NOT NULL,
    model TEXT NOT NULL,
    record_ref TEXT NOT NULL,
    identity TEXT NOT NULL,
    action TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE (system, model, record_ref, written_at, identity)
);
CREATE TABLE IF NOT EXISTS cursors (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries (webhook_id TEXT PRIMARY KEY, seen_at TEXT NOT NULL);
"""
FILTERS = ("system", "model", "identity")
UNATTRIBUTED = "unattributed"


def identity(name) -> str:
    """The identity to store. 'unattributed' only when the system named nobody: a writer
    actually called that is stored in quotes, so it can't hide behind the sentinel."""
    if not name:
        return UNATTRIBUTED
    return f'"{name}"' if name == UNATTRIBUTED else name


def utc(value: str) -> str:
    """Any ISO-ish timestamp (naive means UTC, as Odoo sends) as 2026-10-26T10:00:00Z."""
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now() -> str:
    return utc(datetime.now(timezone.utc).isoformat())


def connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def record_write(conn, write: dict) -> bool:
    """Store a write; False if it was already in the ledger (same record, moment and writer)."""
    with conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO writes (seen_at, written_at, system, model, record_ref,"
            " identity, action, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now(), write["written_at"], write["system"], write["model"], write["record_ref"],
             write["identity"], write["action"], json.dumps(write["payload"], sort_keys=True)),
        )
    return cur.rowcount == 1


def latest_writes(conn, limit=200, **filters) -> list[dict]:
    if unknown := set(filters) - set(FILTERS):
        raise ValueError(f"unknown filter {unknown}")
    where = [(f"{k} = ?", v) for k, v in filters.items() if v]
    sql = "SELECT * FROM writes" + (" WHERE " + " AND ".join(w for w, _ in where) if where else "")
    rows = conn.execute(sql + " ORDER BY written_at DESC, id DESC LIMIT ?", [v for _, v in where] + [limit])
    return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]


def filter_options(conn) -> dict[str, list[str]]:
    return {k: [r[0] for r in conn.execute(f"SELECT DISTINCT {k} FROM writes ORDER BY 1")] for k in FILTERS}


def activity(conn) -> dict[tuple[str, str], dict]:
    rows = conn.execute("SELECT system, identity, count(*), max(written_at) FROM writes GROUP BY 1, 2")
    return {(s, i): {"writes": n, "last_seen": last} for s, i, n, last in rows}


def get_cursor(conn, name: str) -> str | None:
    row = conn.execute("SELECT value FROM cursors WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def set_cursor(conn, name: str, value: str) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO cursors (name, value) VALUES (?, ?)", (name, value))


def first_delivery(conn, webhook_id: str) -> bool:
    """True the first time a Shopify delivery id is seen; False for a redelivery."""
    with conn:
        cur = conn.execute("INSERT OR IGNORE INTO deliveries VALUES (?, ?)", (webhook_id, now()))
    return cur.rowcount == 1
