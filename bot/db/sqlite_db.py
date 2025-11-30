# bot/db/sqlite_db.py
import aiosqlite
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any

from ..core.config_manager import Config

LOGGER = logging.getLogger(__name__)
if not LOGGER.handlers:
    h = logging.StreamHandler()
    fmt = logging.Formatter("[sqlite_db] %(levelname)s: %(message)s")
    h.setFormatter(fmt)
    LOGGER.addHandler(h)
LOGGER.setLevel(logging.INFO)

# IMPORTANT: make the DB path explicit; allow override via env
_DB_PATH = os.environ.get("MIRROR_DB", "botdata.sqlite3")
_ABS_DB_PATH = os.path.abspath(_DB_PATH)

_CONN: Optional[aiosqlite.Connection] = None
_LOCK = asyncio.Lock()

# --- schema ---
CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
  user_id        INTEGER PRIMARY KEY,
  daily_usage_mb INTEGER NOT NULL DEFAULT 0,
  premium_active INTEGER NOT NULL DEFAULT 0,
  referrals      INTEGER NOT NULL DEFAULT 0,
  referrer       INTEGER,
  ads_watched    INTEGER NOT NULL DEFAULT 0,
  thread         INTEGER NOT NULL DEFAULT 0
);
"""

CREATE_FILES = """
CREATE TABLE IF NOT EXISTS files (
  file_id        TEXT PRIMARY KEY,
  name           TEXT,
  leecher_id     INTEGER NOT NULL,
  timestamp      INTEGER NOT NULL,
  download_count INTEGER NOT NULL DEFAULT 0,
  nsfw           INTEGER NOT NULL DEFAULT 0,
  thumbnail      TEXT,
  quality        TEXT,
  source         TEXT
);
"""

CREATE_FILE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_files_leecher_id ON files(leecher_id);
"""

DAILY_LIMIT_MB = 60 * 1024
PER_FILE_LIMIT_MB = 4096


async def _column_exists(conn: aiosqlite.Connection, table: str, column: str) -> bool:
    try:
        cur = await conn.execute(f"PRAGMA table_info({table})")
        rows = await cur.fetchall()
        return any((row[1] == column) for row in rows)  # row[1] is 'name'
    except Exception:
        return False


async def connect() -> aiosqlite.Connection:
    """
    Open (or reuse) the SQLite connection and ensure schema.
    Also run tiny migrations to add missing columns.
    """
    global _CONN
    if _CONN:
        return _CONN
    async with _LOCK:
        if _CONN:
            return _CONN

        LOGGER.info(f"Opening SQLite DB at: {_ABS_DB_PATH}")
        _CONN = await aiosqlite.connect(_ABS_DB_PATH)
        # Helpful when debugging result rows
        _CONN.row_factory = aiosqlite.Row

        # Bot-friendly pragmas
        await _CONN.execute("PRAGMA busy_timeout=5000")          # wait up to 5s for locks
        await _CONN.execute("PRAGMA journal_mode=WAL")           # better concurrency
        await _CONN.execute("PRAGMA synchronous=NORMAL")
        await _CONN.execute("PRAGMA foreign_keys=ON")

        # Base schema
        await _CONN.execute(CREATE_USERS)
        await _CONN.execute(CREATE_FILES)
        await _CONN.execute(CREATE_FILE_INDEXES)

        # Migrations (only if missing)
        if not await _column_exists(_CONN, "files", "source"):
            try:
                await _CONN.execute("ALTER TABLE files ADD COLUMN source TEXT")
                LOGGER.info("Migration: added files.source")
            except Exception as e:
                LOGGER.warning("Migration files.source failed (maybe exists): %s", e)

        if not await _column_exists(_CONN, "users", "thread"):
            try:
                await _CONN.execute("ALTER TABLE users ADD COLUMN thread INTEGER NOT NULL DEFAULT 0")
                LOGGER.info("Migration: added users.thread")
            except Exception as e:
                LOGGER.warning("Migration users.thread failed (maybe exists): %s", e)

        await _CONN.commit()
        LOGGER.info("DB ready.")
    return _CONN


# --- diagnostics (call on demand from your handlers if needed) ---

def get_db_path() -> str:
    """Absolute path of the sqlite file, for sanity checks."""
    return _ABS_DB_PATH

async def debug_dump_schema() -> None:
    conn = await connect()
    LOGGER.info("=== SCHEMA DUMP @ %s ===", _ABS_DB_PATH)
    async with conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'") as cur:
        async for row in cur:
            LOGGER.info("table=%s sql=%s", row["name"], row["sql"])
    async with conn.execute("PRAGMA table_info(users)") as cur:
        cols = await cur.fetchall()
        LOGGER.info("users columns: %s", [dict(zip([c[0] for c in cur.description], r)) for r in cols])

async def debug_dump_user(user_id: int) -> None:
    conn = await connect()
    async with conn.execute("SELECT * FROM users WHERE user_id=?", (int(user_id),)) as cur:
        rows = await cur.fetchall()
        if not rows:
            LOGGER.info("debug_dump_user: no rows for user_id=%s", user_id)
        for r in rows:
            LOGGER.info("debug_dump_user row: %s", dict(r))


async def ensure_user(user_id: int) -> None:
    user_id = int(user_id)
    conn = await connect()
    await conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    await conn.commit()
    LOGGER.debug(f"ensure_user: user_id={user_id} ensured.")


# ----- THREAD (forum topic per user) -----

async def get_user_thread(user_id: int) -> int:
    """
    Return user's stored forum topic thread id (0 if none).
    """
    user_id = int(user_id)
    await ensure_user(user_id)
    conn = await connect()
    cur = await conn.execute("SELECT thread FROM users WHERE user_id = ?", (user_id,))
    row = await cur.fetchone()
    try:
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


async def set_user_thread(user_id: int, thread_id: int) -> None:
    """
    Persist user's forum topic thread id, then verify with a read-back.
    """
    user_id = int(user_id)
    thread_id = int(thread_id or 0)
    await ensure_user(user_id)
    conn = await connect()

    await conn.execute("UPDATE users SET thread = ? WHERE user_id = ?", (thread_id, user_id))
    await conn.commit()

    # verification read-back (logs what actually stuck)
    cur = await conn.execute("SELECT thread FROM users WHERE user_id = ?", (user_id,))
    row = await cur.fetchone()
    seen = int(row[0]) if row and row[0] is not None else -1
    LOGGER.info("set_user_thread: user_id=%s thread_id=%s (verify now=%s)", user_id, thread_id, seen)


# ----- USERS -----

async def get_user(user_id: int) -> Dict[str, Any]:
    user_id = int(user_id)
    await ensure_user(user_id)
    conn = await connect()
    cur = await conn.execute(
        "SELECT user_id, daily_usage_mb, premium_active, referrals, referrer, ads_watched, thread "
        "FROM users WHERE user_id=?",
        (user_id,),
    )
    row = await cur.fetchone()
    if not row:
        # Shouldn’t happen because ensure_user() inserts, but keep safe defaults.
        return {
            "user_id": user_id,
            "daily_usage_mb": 0,
            "premium_active": 0,
            "referrals": 0,
            "referrer": None,
            "ads_watched": 0,
            "thread": 0,
        }
    # normalize types
    return {
        "user_id": int(row[0]),
        "daily_usage_mb": int(row[1] or 0),
        "premium_active": int(row[2] or 0),
        "referrals": int(row[3] or 0),
        "referrer": int(row[4]) if row[4] is not None else None,
        "ads_watched": int(row[5] or 0),
        "thread": int(row[6] or 0),
    }


async def add_usage_mb(user_id: int, used_mb: int) -> None:
    used_mb = max(0, int(used_mb))
    user_id = int(user_id)
    conn = await connect()
    await ensure_user(user_id)
    await conn.execute(
        "UPDATE users SET daily_usage_mb = daily_usage_mb + ? WHERE user_id=?",
        (used_mb, user_id),
    )
    await conn.commit()
    LOGGER.info(f"add_usage_mb: +{used_mb} MB for user_id={user_id}")


async def reset_daily_usage_all() -> int:
    conn = await connect()
    cur = await conn.execute("UPDATE users SET daily_usage_mb = 0 WHERE daily_usage_mb != 0")
    await conn.commit()
    return max(cur.rowcount or 0, 0)


async def set_premium_days(user_id: int, days: int) -> None:
    user_id = int(user_id)
    days = int(days)
    conn = await connect()
    await ensure_user(user_id)
    await conn.execute("UPDATE users SET premium_active=? WHERE user_id=?", (days, user_id))
    await conn.commit()


async def add_premium_days(user_id: int, days: int) -> None:
    user_id = int(user_id)
    days = int(days)
    conn = await connect()
    await ensure_user(user_id)
    await conn.execute(
        "UPDATE users SET premium_active = premium_active + ? WHERE user_id=?",
        (days, user_id),
    )
    await conn.commit()


async def set_referrer(user_id: int, referrer_id: int) -> bool:
    """
    Atomically set referrer once and increment referrer’s counter.
    Returns True only the first time.
    """
    user_id = int(user_id)
    referrer_id = int(referrer_id)
    conn = await connect()
    await ensure_user(user_id)
    await ensure_user(referrer_id)

    cur = await conn.execute(
        "UPDATE users SET referrer=? WHERE user_id=? AND referrer IS NULL",
        (referrer_id, user_id),
    )
    updated = cur.rowcount or 0
    if updated:
        await conn.execute(
            "UPDATE users SET referrals = referrals + 1 WHERE user_id=?",
            (referrer_id,),
        )
        await conn.commit()
        return True

    await conn.commit()
    return False


async def reward_referrer_on_purchase(user_id: int, purchased_days: int) -> None:
    """
    Optional 10% time bonus for the referrer when a referral buys premium.
    Kept for compatibility with earlier logic.
    """
    purchased_days = int(purchased_days or 0)
    if purchased_days <= 0:
        return
    user = await get_user(int(user_id))
    ref = user.get("referrer")
    if ref:
        bonus = max(1, int(purchased_days * 0.1))
        await add_premium_days(int(ref), bonus)


async def increment_ads_watched(user_id: int) -> None:
    user_id = int(user_id)
    conn = await connect()
    await ensure_user(user_id)
    await conn.execute("UPDATE users SET ads_watched = ads_watched + 1 WHERE user_id=?", (user_id,))
    await conn.commit()


async def get_daily_limit_mb(user_id: int) -> int:
    """
    Return per-user daily limit based on premium status.
    """
    u = await get_user(int(user_id))
    prem_days = int(u.get("premium_active") or 0)
    return (
        int(Config.DAILY_LIMIT_PREMIUM_MB)
        if prem_days > 0
        else int(Config.DAILY_LIMIT_FREE_MB)
    )


async def get_remaining_today_mb(user_id: int) -> int:
    user = await get_user(int(user_id))
    daily_limit = await get_daily_limit_mb(int(user_id))
    return max(0, int(daily_limit) - int(user.get("daily_usage_mb") or 0))


async def check_limits_before_task(user_id: int, est_size_mb: Optional[int]) -> Tuple[bool, str]:
    user = await get_user(int(user_id))
    daily_limit = await get_daily_limit_mb(int(user_id))
    if est_size_mb is not None and est_size_mb > PER_FILE_LIMIT_MB:
        return False, (f"Per-file limit is {PER_FILE_LIMIT_MB} MB (4 GB). "
                       f"This item is ~{est_size_mb} MB.")
    if est_size_mb is not None and (user["daily_usage_mb"] + est_size_mb) > daily_limit:
        remain = max(0, daily_limit - user["daily_usage_mb"])
        return False, (f"Daily limit is {daily_limit} MB. "
                       f"Remaining today: {remain} MB.")
    return True, ""


# ----- FILES (cache) -----

async def upsert_file(
    file_id: str,
    name: str,
    leecher_id: int,
    nsfw: bool = False,
    thumbnail: Optional[str] = None,
    quality: Optional[str] = None,
    source: Optional[str] = None,
) -> None:
    conn = await connect()
    ts = int(datetime.now(timezone.utc).timestamp())
    await conn.execute(
        """INSERT INTO files (file_id, name, leecher_id, timestamp, nsfw, thumbnail, quality, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(file_id) DO UPDATE SET
             name=excluded.name,
             leecher_id=excluded.leecher_id,
             timestamp=excluded.timestamp,
             nsfw=excluded.nsfw,
             thumbnail=excluded.thumbnail,
             quality=excluded.quality,
             source=excluded.source
        """,
        (file_id, name, int(leecher_id), ts, 1 if nsfw else 0, thumbnail, quality, source),
    )
    await conn.commit()


async def get_file(file_id: str) -> Optional[Dict[str, Any]]:
    conn = await connect()
    cur = await conn.execute(
        "SELECT file_id, name, leecher_id, timestamp, download_count, nsfw, thumbnail, quality, source "
        "FROM files WHERE file_id=?",
        (file_id,),
    )
    row = await cur.fetchone()
    if not row:
        return None
    return {
        "file_id": row[0],
        "name": row[1],
        "leecher_id": int(row[2]),
        "timestamp": int(row[3]),
        "download_count": int(row[4]),
        "nsfw": bool(row[5]),
        "thumbnail": row[6],
        "quality": row[7],
        "source": row[8],
    }


async def increment_download(file_id: str) -> None:
    conn = await connect()
    await conn.execute("UPDATE files SET download_count = download_count + 1 WHERE file_id=?", (file_id,))
    await conn.commit()


async def get_file_by_source_quality(source: str, quality: str) -> Optional[Dict[str, Any]]:
    conn = await connect()
    cur = await conn.execute(
        "SELECT file_id, name, leecher_id, timestamp, download_count, nsfw, thumbnail, quality, source "
        "FROM files WHERE source=? AND quality=?",
        (source, quality),
    )
    row = await cur.fetchone()
    if not row:
        return None
    return {
        "file_id": row[0],
        "name": row[1],
        "leecher_id": int(row[2]),
        "timestamp": int(row[3]),
        "download_count": int(row[4]),
        "nsfw": bool(row[5]),
        "thumbnail": row[6],
        "quality": row[7],
        "source": row[8],
    }


async def get_files_by_user(user_id: int) -> list[Dict[str, Any]]:
    conn = await connect()
    cur = await conn.execute(
        "SELECT file_id, name, quality FROM files WHERE leecher_id=? ORDER BY timestamp DESC",
        (int(user_id),),
    )
    rows = await cur.fetchall()
    return [{"file_id": r[0], "name": r[1], "quality": r[2]} for r in rows]


# ---- NEW: paginated listing ----

async def get_files_count_by_user(user_id: int) -> int:
    conn = await connect()
    cur = await conn.execute("SELECT COUNT(*) FROM files WHERE leecher_id=?", (int(user_id),))
    row = await cur.fetchone()
    return int(row[0] if row and row[0] is not None else 0)


async def get_files_by_user_paged(user_id: int, page: int, page_size: int) -> list[Dict[str, Any]]:
    page = max(1, int(page or 1))
    page_size = max(1, min(50, int(page_size or 10)))
    offset = (page - 1) * page_size
    conn = await connect()
    cur = await conn.execute(
        "SELECT file_id, name, quality "
        "FROM files WHERE leecher_id=? "
        "ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        (int(user_id), page_size, offset),
    )
    rows = await cur.fetchall()
    return [{"file_id": r[0], "name": r[1], "quality": r[2]} for r in rows]


async def resolve_file_id_by_prefix(user_id: int, short_prefix: str) -> Optional[str]:
    """
    Return the full file_id for this user that starts with the given prefix.
    If multiple match, return the most recent by timestamp.
    """
    short_prefix = str(short_prefix or "").strip()
    if not short_prefix:
        return None
    conn = await connect()
    cur = await conn.execute(
        """
        SELECT file_id
        FROM files
        WHERE leecher_id = ? AND file_id LIKE ?
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (int(user_id), f"{short_prefix}%"),
    )
    row = await cur.fetchone()
    return row[0] if row else None
