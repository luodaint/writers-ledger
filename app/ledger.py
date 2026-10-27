"""The ledger: every write we observed and the findings of the last check, in SQLite.

Queries use parameters only."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from app.register import UNATTRIBUTED

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
CREATE TABLE IF NOT EXISTS findings (
    fingerprint TEXT PRIMARY KEY, rule TEXT NOT NULL, severity TEXT NOT NULL,
    system TEXT NOT NULL, model TEXT NOT NULL, identity TEXT NOT NULL, writer_id TEXT, owner TEXT NOT NULL,
    summary TEXT NOT NULL, facts TEXT NOT NULL, count INTEGER NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, status TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS triage (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calls (run TEXT NOT NULL, fingerprint TEXT NOT NULL, model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cost_usd REAL);
"""
FINDING = ("fingerprint", "rule", "severity", "system", "model", "identity", "writer_id", "owner", "summary", "facts", "count", "first_seen", "last_seen")
UPDATE = ", ".join(f"{c} = excluded.{c}" for c in FINDING if c not in ("fingerprint", "first_seen"))
FILTERS = ("system", "model", "identity")


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
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def now(ago: timedelta = timedelta()) -> str:
    """Now, or `ago` before now, in the ledger's format."""
    return utc((datetime.now(UTC) - ago).isoformat())


def connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def record_write(conn, write: dict) -> bool:
    """Store a write; False if the ledger already had it (same record, second and writer).
    One collector reads Odoo in order, so an Odoo record read again in the same second with a new state,
    say a stock count applied within the second it was set, keeps the newer. Shopify's first delivery stays."""
    with conn:
        cur = conn.execute(
            "INSERT INTO writes (seen_at, written_at, system, model, record_ref, identity, action, payload)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (system, model, record_ref, written_at, identity)"
            " DO UPDATE SET payload = excluded.payload WHERE system = 'odoo' AND payload != excluded.payload",
            (
                now(),
                write["written_at"],
                write["system"],
                write["model"],
                write["record_ref"],
                write["identity"],
                write["action"],
                json.dumps(write["payload"], sort_keys=True),
            ),
        )
    return cur.rowcount == 1


def matching(filters: dict) -> tuple[str, list]:
    """A WHERE clause for exact-match filters. Column names come from FILTERS, values are parameters."""
    if unknown := set(filters) - set(FILTERS):
        raise ValueError(f"unknown filter {unknown}")
    used = {k: v for k, v in filters.items() if v}
    return (" WHERE " + " AND ".join(f"{k} = ?" for k in used) if used else ""), list(used.values())


def latest_writes(conn, limit=200, **filters) -> list[dict]:
    where, values = matching(filters)
    rows = conn.execute(f"SELECT * FROM writes{where} ORDER BY written_at DESC, id DESC LIMIT ?", [*values, limit])
    return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]


def filter_options(conn, **filters) -> dict[str, list[tuple[str, int]]]:
    """Each filter's values and counts under the other filters: a filter never narrows its own list."""
    options = {}
    for k in FILTERS:
        where, values = matching(filters | {k: ""})
        options[k] = [tuple(r) for r in conn.execute(f"SELECT {k}, count(*) FROM writes{where} GROUP BY 1 ORDER BY 1", values)]
    return options


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


def writes_to_check(conn, since: str, stock_models: tuple) -> list[dict]:
    """The writes since `since`, plus every stock level: drift needs the newest one, however old."""
    rows = conn.execute(f"SELECT * FROM writes WHERE written_at >= ? OR model IN ({', '.join('?' * len(stock_models))})", (since, *stock_models))
    return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]


def save_check(conn, findings: list[dict], checked_at: str) -> None:
    """Store one check: upsert its findings by fingerprint and resolve the open ones it didn't find.
    The count is what this check counted, never a sum across checks."""
    with conn:
        conn.execute("UPDATE findings SET status = 'resolved', resolved_at = ? WHERE status = 'open'", (checked_at,))
        conn.executemany(
            f"INSERT INTO findings ({', '.join(FINDING)}, status) VALUES ({', '.join(':' + c for c in FINDING)}, 'open')"
            f" ON CONFLICT (fingerprint) DO UPDATE SET {UPDATE}, first_seen = min(first_seen, excluded.first_seen),"
            " status = 'open', resolved_at = NULL",
            [f | {"facts": json.dumps(f["facts"], sort_keys=True)} for f in findings],
        )
        conn.execute("INSERT OR REPLACE INTO cursors (name, value) VALUES ('check:last', ?)", (checked_at,))


def list_findings(conn) -> list[dict]:
    """Every finding, the most recently seen first."""
    rows = conn.execute("SELECT * FROM findings ORDER BY last_seen DESC")
    return [dict(r) | {"facts": json.loads(r["facts"])} for r in rows]


def save_triage(conn, key: str, fingerprint: str, result: dict) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO triage VALUES (?, ?, ?, ?)", (key, fingerprint, json.dumps(result, sort_keys=True), now()))


def triaged_keys(conn) -> set[str]:
    return {key for (key,) in conn.execute("SELECT key FROM triage")}


def latest_triage(conn) -> dict[str, dict]:
    """The latest usable answer per fingerprint: one Claude declined ({}) never hides an earlier answer."""
    rows = conn.execute("SELECT fingerprint, result FROM triage WHERE result != '{}' ORDER BY at, rowid")
    return {fingerprint: json.loads(result) for fingerprint, result in rows}


def save_calls(conn, run: str, fingerprint: str, calls: list[dict]) -> None:
    """Log the tokens and cost of every call to Claude (never the prompt or the answer)."""
    with conn:
        conn.executemany(
            "INSERT INTO calls VALUES (:run, :fingerprint, :model, :input_tokens, :output_tokens, :cost_usd)", [c | {"run": run, "fingerprint": fingerprint} for c in calls]
        )


def last_run_cost(conn) -> float | None:
    """Dollars spent by the last triage run that called Claude."""
    return conn.execute("SELECT sum(cost_usd) FROM calls WHERE run = (SELECT max(run) FROM calls)").fetchone()[0]
