from __future__ import annotations
import asyncio, sqlite3
from pathlib import Path
from typing import Tuple
from datetime import datetime, timezone

# SQLite file under ./data/
BASE_DIR = Path(__file__).resolve().parents[3]  # .../telemirror
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "quota.sqlite3"

FREE_LIMIT_BYTES = 2_048 * 1024 * 1024        # 2 GiB
PREMIUM_LIMIT_BYTES = 50_000 * 1024 * 1024    # 50 GiB

def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usage_daily (
            user_id INTEGER NOT NULL,
            date    TEXT    NOT NULL,
            used    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, date)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS premium_users (
            user_id INTEGER PRIMARY KEY,
            premium INTEGER NOT NULL DEFAULT 0
        )
    """)
    con.commit()
    con.close()

_init_db()

def _get_usage_sync(user_id: int) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT used FROM usage_daily WHERE user_id=? AND date=?", (user_id, _today_str()))
    row = cur.fetchone()
    con.close()
    return int(row[0]) if row else 0

def _add_usage_sync(user_id: int, bytes_used: int) -> None:
    if bytes_used <= 0:
        return
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO usage_daily (user_id, date, used) VALUES (?, ?, ?)
        ON CONFLICT(user_id, date) DO UPDATE SET used = used + excluded.used
    """, (user_id, _today_str(), int(bytes_used)))
    con.commit()
    con.close()

def _reset_usage_sync(user_id: int) -> None:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO usage_daily (user_id, date, used) VALUES (?, ?, 0)
        ON CONFLICT(user_id, date) DO UPDATE SET used = 0
    """, (user_id, _today_str()))
    con.commit()
    con.close()

def _is_premium_sync(user_id: int) -> bool:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT premium FROM premium_users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    return bool(row and row[0])

def _set_premium_sync(user_id: int, value: bool) -> None:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO premium_users (user_id, premium) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET premium=excluded.premium
    """, (user_id, int(bool(value))))
    con.commit()
    con.close()

async def get_usage(user_id: int) -> int:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_usage_sync, user_id)

async def add_usage(user_id: int, bytes_used: int) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _add_usage_sync, user_id, bytes_used)

async def reset_usage(user_id: int) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _reset_usage_sync, user_id)

async def is_premium(user_id: int) -> bool:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _is_premium_sync, user_id)

async def set_premium(user_id: int, value: bool) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _set_premium_sync, user_id, value)

async def get_limits(user_id: int) -> Tuple[bool, int]:
    prem = await is_premium(user_id)
    return prem, (PREMIUM_LIMIT_BYTES if prem else FREE_LIMIT_BYTES)
