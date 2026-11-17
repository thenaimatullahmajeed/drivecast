"""SQLite storage. One file, WAL mode, a connection per thread.

Every table that belongs to a person carries ``workspace_id`` so one account can
never read or change another account's rows.
"""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager

_local = threading.local()
_path = None
_write_lock = threading.RLock()

SCHEMA = [
    # version 1
    """
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS workspaces (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL,
        google_client_id TEXT, google_client_secret_enc TEXT,
        drive_token_enc TEXT, drive_email TEXT, drive_root_id TEXT, drive_root_name TEXT,
        folders_json TEXT, folders_scanned_at REAL, folders_error TEXT
    );
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
        email TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL, pw_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('owner','student')),
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
        timezone TEXT NOT NULL DEFAULT 'Asia/Karachi',
        limits_json TEXT NOT NULL DEFAULT '{}', invite_code TEXT,
        created_at REAL NOT NULL, last_login REAL
    );
    CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at REAL NOT NULL, expires_at REAL NOT NULL, user_agent TEXT
    );
    CREATE TABLE IF NOT EXISTS invites (
        code TEXT PRIMARY KEY, label TEXT NOT NULL, limits_json TEXT NOT NULL,
        created_at REAL NOT NULL, expires_at REAL NOT NULL, used_by INTEGER, used_at REAL,
        revoked INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS ip_groups (
        id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
        number INTEGER NOT NULL, host TEXT NOT NULL, port INTEGER NOT NULL,
        username TEXT, password_enc TEXT, max_channels INTEGER NOT NULL DEFAULT 5,
        priority TEXT NOT NULL DEFAULT 'normal',
        status TEXT NOT NULL DEFAULT 'untested', outgoing_ip TEXT, latency_ms INTEGER,
        last_check REAL, last_error TEXT, fail_count INTEGER NOT NULL DEFAULT 0,
        latency_json TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL,
        UNIQUE (workspace_id, number)
    );
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
        platform TEXT NOT NULL DEFAULT 'youtube',
        external_id TEXT NOT NULL, title TEXT NOT NULL, handle TEXT, thumbnail TEXT,
        signin_email TEXT, token_enc TEXT, group_id INTEGER REFERENCES ip_groups(id),
        folder_id TEXT, folder_name TEXT,
        plan_json TEXT NOT NULL DEFAULT '{}',
        state TEXT NOT NULL DEFAULT 'setup' CHECK (state IN ('setup','live','paused','held','attention')),
        state_reason TEXT, consecutive_failures INTEGER NOT NULL DEFAULT 0, paused_until REAL,
        subscribers INTEGER, total_views INTEGER, stats_at REAL,
        queue_count INTEGER, queue_checked_at REAL,
        created_at REAL NOT NULL, live_since REAL,
        UNIQUE (workspace_id, platform, external_id)
    );
    CREATE TABLE IF NOT EXISTS connect_links (
        code TEXT PRIMARY KEY, workspace_id INTEGER NOT NULL, purpose TEXT NOT NULL,
        group_id INTEGER, created_at REAL NOT NULL, expires_at REAL NOT NULL,
        used_at REAL, result_json TEXT, error TEXT
    );
    CREATE TABLE IF NOT EXISTS slots (
        id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        day TEXT NOT NULL, idx INTEGER NOT NULL, due_at REAL NOT NULL, window_end REAL NOT NULL,
        market TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'planned'
            CHECK (status IN ('planned','queued','running','done','missed','held','skipped','failed','uncertain')),
        note TEXT, upload_id INTEGER, updated_at REAL,
        UNIQUE (channel_id, day, idx)
    );
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        slot_id INTEGER, kind TEXT NOT NULL DEFAULT 'publish',
        status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
        lease_owner TEXT, lease_until REAL, created_at REAL NOT NULL, finished_at REAL, error TEXT
    );
    CREATE TABLE IF NOT EXISTS uploads (
        id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
        destination TEXT NOT NULL, file_id TEXT NOT NULL, file_name TEXT NOT NULL, title TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('reserved','uploading','published','failed','uncertain')),
        external_video_id TEXT, visibility TEXT, error TEXT, outgoing_ip TEXT,
        reserved_at REAL NOT NULL, published_at REAL,
        views INTEGER, likes INTEGER, comments INTEGER, stats_at REAL,
        UNIQUE (file_id, destination)
    );
    CREATE TABLE IF NOT EXISTS stat_points (
        upload_id INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
        day TEXT NOT NULL, views INTEGER NOT NULL, PRIMARY KEY (upload_id, day)
    );
    CREATE TABLE IF NOT EXISTS channel_points (
        channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        day TEXT NOT NULL, views INTEGER, subscribers INTEGER, PRIMARY KEY (channel_id, day)
    );
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY, workspace_id INTEGER, channel_id INTEGER, level TEXT NOT NULL,
        kind TEXT NOT NULL, message TEXT NOT NULL, at REAL NOT NULL, resolved INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS ix_slots_due ON slots(status, due_at);
    CREATE INDEX IF NOT EXISTS ix_uploads_ws ON uploads(workspace_id, published_at);
    CREATE INDEX IF NOT EXISTS ix_events_ws ON events(workspace_id, at);
    CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
    """,
    # version 2: where each proxy IP is
    """
    ALTER TABLE ip_groups ADD COLUMN country TEXT;
    ALTER TABLE ip_groups ADD COLUMN country_name TEXT;
    ALTER TABLE ip_groups ADD COLUMN city TEXT;
    ALTER TABLE ip_groups ADD COLUMN isp TEXT;
    """,
    # version 3: every stats reading (not one per day), so views show the same day
    """
    CREATE TABLE IF NOT EXISTS channel_ticks (
        channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        at REAL NOT NULL, views INTEGER, subscribers INTEGER);
    CREATE INDEX IF NOT EXISTS ix_channel_ticks ON channel_ticks(channel_id, at);
    CREATE TABLE IF NOT EXISTS upload_ticks (
        upload_id INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
        at REAL NOT NULL, views INTEGER);
    CREATE INDEX IF NOT EXISTS ix_upload_ticks ON upload_ticks(upload_id, at);
    INSERT INTO channel_ticks(channel_id, at, views, subscribers)
        SELECT channel_id, MIN(CAST(strftime('%s', day || ' 23:59:59') AS REAL), CAST(strftime('%s','now') AS REAL)), views, subscribers
        FROM channel_points;
    INSERT INTO upload_ticks(upload_id, at, views)
        SELECT upload_id, MIN(CAST(strftime('%s', day || ' 23:59:59') AS REAL), CAST(strftime('%s','now') AS REAL)), views
        FROM stat_points;
    """,
    # version 4: Instagram source, media ledger, error codes
    """
    CREATE TABLE IF NOT EXISTS ig_accounts (
        id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
        group_id INTEGER NOT NULL UNIQUE REFERENCES ip_groups(id) ON DELETE CASCADE,
        username TEXT, ig_user_id TEXT, cookies_enc TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ok', error_code TEXT, error_detail TEXT,
        checked_at REAL, paused_until REAL, backoff INTEGER NOT NULL DEFAULT 0,
        day TEXT, day_count INTEGER NOT NULL DEFAULT 0, last_request REAL,
        created_at REAL NOT NULL, updated_at REAL
    );
    CREATE TABLE IF NOT EXISTS sources (
        id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        kind TEXT NOT NULL DEFAULT 'instagram',
        handle TEXT NOT NULL, ig_user_id TEXT, full_name TEXT, picture TEXT,
        content_type TEXT NOT NULL DEFAULT 'videos' CHECK (content_type IN ('videos','mix','photos')),
        include_old INTEGER NOT NULL DEFAULT 1, daily_cap INTEGER NOT NULL DEFAULT 6,
        state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','paused','removed')),
        started_at REAL NOT NULL, newest_seen REAL, backfill_cursor TEXT, backfill_done INTEGER NOT NULL DEFAULT 0,
        last_check REAL, next_check REAL, day TEXT, day_count INTEGER NOT NULL DEFAULT 0,
        error_code TEXT, error_detail TEXT, total_found INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, updated_at REAL
    );
    CREATE INDEX IF NOT EXISTS ix_sources_ch ON sources(channel_id, state);
    CREATE TABLE IF NOT EXISTS media (
        id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL, channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        source_id INTEGER, origin TEXT NOT NULL CHECK (origin IN ('instagram','inbox','drive')),
        ext_id TEXT NOT NULL, code TEXT, kind TEXT NOT NULL DEFAULT 'video' CHECK (kind IN ('video','photo','carousel')),
        lane TEXT NOT NULL DEFAULT 'backfill' CHECK (lane IN ('fresh','drive','backfill')),
        taken_at REAL, caption TEXT, title TEXT, ref TEXT UNIQUE,
        width INTEGER, height INTEGER, duration REAL, has_audio INTEGER,
        local_path TEXT, local_bytes INTEGER, drive_file_id TEXT,
        state TEXT NOT NULL DEFAULT 'found' CHECK (state IN ('found','downloading','cleaned','in_drive','published','skipped','failed','waiting_facebook')),
        skip_reason TEXT, error_code TEXT, attempts INTEGER NOT NULL DEFAULT 0, next_try REAL,
        published_at REAL, local_deleted_at REAL, created_at REAL NOT NULL, updated_at REAL,
        UNIQUE (channel_id, origin, ext_id)
    );
    CREATE INDEX IF NOT EXISTS ix_media_state ON media(channel_id, state, lane);
    CREATE INDEX IF NOT EXISTS ix_media_drive ON media(drive_file_id);
    ALTER TABLE events ADD COLUMN code TEXT;
    ALTER TABLE events ADD COLUMN detail TEXT;
    ALTER TABLE channels ADD COLUMN encore INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE channels ADD COLUMN titles_json TEXT;
    ALTER TABLE uploads ADD COLUMN media_id INTEGER;
    ALTER TABLE uploads ADD COLUMN encore_of INTEGER;
    ALTER TABLE channels ADD COLUMN inbox_id TEXT;
    """,
    # version 5: pause everything (vacation), Instagram account identity
    """
    ALTER TABLE workspaces ADD COLUMN paused_until REAL;
    ALTER TABLE workspaces ADD COLUMN paused_at REAL;
    ALTER TABLE workspaces ADD COLUMN pause_collect INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE ig_accounts ADD COLUMN user_agent TEXT;
    ALTER TABLE ig_accounts ADD COLUMN language TEXT;
    """,
    # version 6: Facebook Pages via the Graph API
    """
    ALTER TABLE workspaces ADD COLUMN fb_app_id TEXT;
    ALTER TABLE workspaces ADD COLUMN fb_app_secret_enc TEXT;
    CREATE TABLE IF NOT EXISTS fb_accounts (
        id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
        group_id INTEGER NOT NULL UNIQUE REFERENCES ip_groups(id) ON DELETE CASCADE,
        fb_user_id TEXT, name TEXT, token_enc TEXT NOT NULL, token_expires REAL,
        status TEXT NOT NULL DEFAULT 'ok', error_code TEXT, error_detail TEXT,
        checked_at REAL, created_at REAL NOT NULL, updated_at REAL
    );
    CREATE TABLE IF NOT EXISTS fb_pages (
        id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL, fb_account_id INTEGER NOT NULL REFERENCES fb_accounts(id) ON DELETE CASCADE,
        page_id TEXT NOT NULL, name TEXT, category TEXT, picture TEXT, token_enc TEXT NOT NULL, can_post INTEGER NOT NULL DEFAULT 1,
        channel_id INTEGER REFERENCES channels(id) ON DELETE SET NULL,
        created_at REAL NOT NULL, updated_at REAL,
        UNIQUE (fb_account_id, page_id)
    );
    CREATE INDEX IF NOT EXISTS ix_fb_pages_ch ON fb_pages(channel_id);
    ALTER TABLE channels ADD COLUMN fb_page_id TEXT;
    ALTER TABLE channels ADD COLUMN youtube_on INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE uploads ADD COLUMN post_id TEXT;
    """,
]


def init(path):
    global _path
    _path = path
    with _write_lock:
        c = conn()
        c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        row = c.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        current = int(row[0]) if row else 0
        for version, sql in enumerate(SCHEMA, start=1):
            if version > current:
                # executescript manages its own transaction; each statement is idempotent
                c.executescript("BEGIN;" + sql + f";INSERT OR REPLACE INTO meta(key,value) VALUES('schema','{version}');COMMIT;")


def conn():
    c = getattr(_local, "conn", None)
    if c is None:
        if _path is None:
            raise RuntimeError("database not initialised")
        c = sqlite3.connect(_path, timeout=30, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=30000")
        _local.conn = c
    return c


@contextmanager
def tx():
    """Serialised write transaction. Reads outside tx are fine (WAL)."""
    with _write_lock:
        c = conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise


def all(sql, args=()):  # noqa: A001 - short helper name is intentional
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def run(sql, args=()):
    with tx() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def meta_get(key, default=None):
    r = one("SELECT value FROM meta WHERE key=?", (key,))
    return r["value"] if r else default


def meta_set(key, value):
    run("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))


def event(level, kind, message, workspace_id=None, channel_id=None, code=None, detail=None):
    """Record something a person may need to see. Never raises.
    Events with a code are de-duplicated: the same open problem updates one row."""
    try:
        now = time.time()
        if code:
            old = one("""SELECT id FROM events WHERE code=? AND resolved=0 AND IFNULL(workspace_id,0)=IFNULL(?,0)
                         AND IFNULL(channel_id,0)=IFNULL(?,0) AND at>? ORDER BY at DESC LIMIT 1""",
                      (code, workspace_id, channel_id, now - 24 * 3600))
            if old:
                run("UPDATE events SET message=?, detail=?, at=?, level=? WHERE id=?",
                    (message[:500], (detail or "")[:2000] or None, now, level, old["id"]))
                return
        run("INSERT INTO events(workspace_id,channel_id,level,kind,message,at,code,detail) VALUES(?,?,?,?,?,?,?,?)",
            (workspace_id, channel_id, level, kind, message[:500], now, code, (detail or "")[:2000] or None))
    except Exception:  # an event must never break the caller
        pass


def resolve(code, workspace_id=None, channel_id=None):
    """A problem went away by itself (e.g. new cookies passed): close its open events."""
    try:
        run("UPDATE events SET resolved=1 WHERE code=? AND resolved=0 AND IFNULL(workspace_id,0)=IFNULL(?,0) AND IFNULL(channel_id,0)=IFNULL(?,0)",
            (code, workspace_id, channel_id))
    except Exception:
        pass


def jload(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
