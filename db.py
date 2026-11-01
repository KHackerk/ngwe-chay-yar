"""Ngwe Chay Yar — database layer.

Dual backend:
- Postgres when DATABASE_URL starts with "postgres" (psycopg, lazy import).
- SQLite when DATABASE_URL starts with "sqlite:///" (that file path),
  otherwise a local file at data/app.db relative to the app root.

All SQL is written with ``?`` placeholders; they are converted to ``%s``
for psycopg. Rows are returned as plain dicts on both backends.
Connections are per-call (open, use, close) — no global connection.
"""
import os
import sqlite3

APP_ROOT = os.path.dirname(os.path.abspath(__file__))


def _database_url():
    return os.environ.get("DATABASE_URL", "") or ""


def _is_pg():
    return _database_url().startswith("postgres")


def _sqlite_path():
    url = _database_url()
    if url.startswith("sqlite:///"):
        path = url[len("sqlite:///"):]
        # sqlite:////abs/path -> /abs/path ; sqlite:///rel/path -> rel/path
        if not path.startswith("/"):
            path = "/" + path if url.startswith("sqlite:////") else path
        if not os.path.isabs(path):
            path = os.path.join(APP_ROOT, path)
        return path
    return os.path.join(APP_ROOT, "data", "app.db")


def _connect():
    """Return (connection, is_pg). Caller must close."""
    if _is_pg():
        import psycopg
        from psycopg.rows import dict_row

        url = _database_url()
        # psycopg accepts libpq-style URLs directly.
        conn = psycopg.connect(url, row_factory=dict_row)
        return conn, True
    path = _sqlite_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn, False


def _convert(sql, is_pg):
    if is_pg:
        return sql.replace("?", "%s")
    return sql


def q(sql, params=()):
    """Run a SELECT and return a list of dict rows."""
    conn, is_pg = _connect()
    try:
        cur = conn.execute(_convert(sql, is_pg), tuple(params))
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def q1(sql, params=()):
    """Run a SELECT and return the first row as a dict, or None."""
    rows = q(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    """Run an INSERT/UPDATE/DELETE. Returns lastrowid where available."""
    conn, is_pg = _connect()
    try:
        cur = conn.execute(_convert(sql, is_pg), tuple(params))
        conn.commit()
        return getattr(cur, "lastrowid", None) or 0
    finally:
        conn.close()


def insert(sql, params=()):
    """Run an INSERT and return the new row id (both backends)."""
    conn, is_pg = _connect()
    try:
        if is_pg:
            stmt = _convert(sql, is_pg)
            if "returning" not in stmt.lower():
                stmt = stmt.rstrip().rstrip(";") + " RETURNING id"
            cur = conn.execute(stmt, tuple(params))
            row = cur.fetchone()
            conn.commit()
            if row is None:
                return 0
            if isinstance(row, dict):
                return int(row.get("id") or 0)
            return int(row[0])
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_setting(key):
    row = q1("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else None


def set_setting(key, value):
    conn, is_pg = _connect()
    try:
        if is_pg:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, str(value)),
            )
        else:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

_SQLITE_DDL = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL DEFAULT '',
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT 'user',
        username TEXT UNIQUE,
        photo TEXT,
        suspended INTEGER NOT NULL DEFAULT 0,
        session_version INTEGER NOT NULL DEFAULT 0,
        lang TEXT NOT NULL DEFAULT 'my',
        theme TEXT NOT NULL DEFAULT 'light',
        created_at TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        kind TEXT NOT NULL DEFAULT 'expense',
        amount INTEGER NOT NULL DEFAULT 0,
        category TEXT NOT NULL DEFAULT '',
        txn_date TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        account_id INTEGER,
        created_at TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL DEFAULT '',
        type TEXT NOT NULL DEFAULT '',
        brand TEXT NOT NULL DEFAULT '',
        balance INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL DEFAULT '',
        type TEXT NOT NULL DEFAULT '',
        purchase_price INTEGER NOT NULL DEFAULT 0,
        current_value INTEGER NOT NULL DEFAULT 0,
        location TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS debts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL DEFAULT '',
        amount INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER NOT NULL REFERENCES users(id),
        recipient_id INTEGER NOT NULL REFERENCES users(id),
        type TEXT NOT NULL DEFAULT 'text',
        text TEXT NOT NULL DEFAULT '',
        file_path TEXT NOT NULL DEFAULT '',
        file_name TEXT NOT NULL DEFAULT '',
        file_size INTEGER NOT NULL DEFAULT 0,
        card TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS support_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        sender TEXT NOT NULL DEFAULT 'user',
        type TEXT NOT NULL DEFAULT 'text',
        text TEXT NOT NULL DEFAULT '',
        file_path TEXT NOT NULL DEFAULT '',
        file_name TEXT NOT NULL DEFAULT '',
        file_size INTEGER NOT NULL DEFAULT 0,
        pinned INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    )
    """,
]

_PG_DDL = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT 'user',
        username TEXT UNIQUE,
        photo TEXT,
        suspended INTEGER NOT NULL DEFAULT 0,
        session_version INTEGER NOT NULL DEFAULT 0,
        lang TEXT NOT NULL DEFAULT 'my',
        theme TEXT NOT NULL DEFAULT 'light',
        created_at TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transactions (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        kind TEXT NOT NULL DEFAULT 'expense',
        amount INTEGER NOT NULL DEFAULT 0,
        category TEXT NOT NULL DEFAULT '',
        txn_date TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        account_id INTEGER,
        created_at TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS accounts (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL DEFAULT '',
        type TEXT NOT NULL DEFAULT '',
        brand TEXT NOT NULL DEFAULT '',
        balance INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assets (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL DEFAULT '',
        type TEXT NOT NULL DEFAULT '',
        purchase_price INTEGER NOT NULL DEFAULT 0,
        current_value INTEGER NOT NULL DEFAULT 0,
        location TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS debts (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL DEFAULT '',
        amount INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id SERIAL PRIMARY KEY,
        sender_id INTEGER NOT NULL REFERENCES users(id),
        recipient_id INTEGER NOT NULL REFERENCES users(id),
        type TEXT NOT NULL DEFAULT 'text',
        text TEXT NOT NULL DEFAULT '',
        file_path TEXT NOT NULL DEFAULT '',
        file_name TEXT NOT NULL DEFAULT '',
        file_size INTEGER NOT NULL DEFAULT 0,
        card TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS support_messages (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        sender TEXT NOT NULL DEFAULT 'user',
        type TEXT NOT NULL DEFAULT 'text',
        text TEXT NOT NULL DEFAULT '',
        file_path TEXT NOT NULL DEFAULT '',
        file_name TEXT NOT NULL DEFAULT '',
        file_size INTEGER NOT NULL DEFAULT 0,
        pinned INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    )
    """,
]

_DEFAULT_SETTINGS = {
    "registration_open": "1",
    "maintenance": "0",
    "allow_messages": "1",
}


def _add_column(conn, is_pg, table, column, ddl):
    """ALTER TABLE ... ADD COLUMN, idempotent on both backends."""
    if is_pg:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
    else:
        cols = [r[1] for r in
                conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _migrate(conn, is_pg):
    """Idempotent migrations for databases created by older builds.

    transactions.account_id (wallet linkage) is a plain nullable INTEGER —
    no REFERENCES clause, because the transactions DDL runs before the
    accounts table exists and Postgres requires referenced tables to
    pre-exist. Wallet ownership is enforced in the API layer instead.

    users.session_version backs session invalidation on admin password
    reset: every login stamps the version into the session cookie, and
    current_user()/the admin gate reject a session whose stamp is stale.
    """
    _add_column(conn, is_pg, "transactions", "account_id", "INTEGER")
    _add_column(conn, is_pg, "users", "session_version",
                "INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def init_db():
    """Create all tables if absent; seed ONLY the settings defaults."""
    conn, is_pg = _connect()
    try:
        ddl = _PG_DDL if is_pg else _SQLITE_DDL
        for stmt in ddl:
            conn.execute(stmt)
        conn.commit()
        _migrate(conn, is_pg)
    finally:
        conn.close()
    # Seed settings defaults only — never any user/demo content.
    for key, value in _DEFAULT_SETTINGS.items():
        if get_setting(key) is None:
            set_setting(key, value)
