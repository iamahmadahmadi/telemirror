import logging
import re
import os
from urllib.parse import urlparse

import httpx
from pyrogram import Client
from pyrogram.types import Message

from ..helper.telegram_helper.bot_commands import BotCommands
from ..db.sqlite_db import check_limits_before_task, increment_ads_watched
from ..helper.telegram_helper.message_utils import build_over_quota_kb  # ✅ added

# Premium checks are optional: we import gracefully
try:
    # provides: check_user_quota, is_nsfw, has_nsfw_access
    from ..helper.ext_utils import premium_utils
    _HAS_PREMIUM = True
except Exception:
    premium_utils = None
    _HAS_PREMIUM = False

LOGGER = logging.getLogger(__name__)
_URL_RE = re.compile(r"https?://\S+")

# Treat these as non-file web pages (anything else with an extension is considered a direct file)
_NON_FILE_EXTS = {"html", "htm", "php"}


def _first_url(text: str) -> str | None:
    if not text:
        return None
    m = _URL_RE.search(text.strip())
    return m.group(0) if m else None


def _url_file_ext(u: str) -> str:
    """
    Return lowercase file extension (without dot) from URL path, '' if none.
    """
    try:
        path = urlparse(u).path  # e.g. /files/video.mp4
        ext = os.path.splitext(path)[1]  # '.mp4'
        return ext[1:].lower() if ext else ""
    except Exception:
        return ""


async def _estimate_direct_size_mb(url: str) -> int | None:
    """
    Try to estimate size for direct files using HTTP HEAD.
    Returns integer MB or None if unknown.
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0, verify=False) as client:
            resp = await client.head(url, headers={"User-Agent": "Mozilla/5.0"})
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit():
                size_bytes = int(cl)
                # ceil to MB
                return max(1, (size_bytes + (1024 * 1024 - 1)) // (1024 * 1024))
    except Exception as e:
        LOGGER.debug("[AUTO_LEECH] HEAD size probe failed for %s: %s", url, e)
    return None


async def auto_leech(client: Client, message: Message):
    """
    Auto-capture bare links sent to the bot (no leading slash)
    and dispatch them based on extension:
      - ends with real file extension (not html/php) -> /l (direct leech)
      - otherwise -> /yl (yt-dlp)

    This function is PUBLIC (no authorization gate).
    """
    text = message.text or ""
    uid = getattr(message.from_user, "id", None)
    LOGGER.info("[AUTO_LEECH] RAW from %s: %r", uid, text)

    # Ignore if user typed a slash command; this handler is for bare links only.
    if text.lstrip().startswith("/"):
        return

    url = _first_url(text)
    if not url:
        LOGGER.debug("[AUTO_LEECH] No URL detected in message.")
        return

    LOGGER.info("[AUTO_LEECH] Candidate URL for %s: %s", uid, url)

    # Optional: premium / quota checks (skipped if premium_utils missing)
    if _HAS_PREMIUM:
        try:
            if not await premium_utils.check_user_quota(uid):
                await message.reply(
                    "⚠️ Daily quota reached. Open ad below to continue.",
                    reply_markup=build_over_quota_kb(),
                )
                # mark ad flow and stop here
                await increment_ads_watched(uid)
                return
            if await premium_utils.is_nsfw(url) and not await premium_utils.has_nsfw_access(uid):
                await message.reply("🚫 NSFW content is restricted. Upgrade to enable NSFW downloads.")
                return
        except Exception as e:
            LOGGER.warning("[AUTO_LEECH] premium checks failed: %s", e)

    # ---------- Extension-based routing ----------
    ext = _url_file_ext(url)
    is_direct = bool(ext) and ext not in _NON_FILE_EXTS
    bc_attr = "LeechCommand" if is_direct else "YtdlLeechCommand"
    alias = getattr(BotCommands, bc_attr, None)
    if isinstance(alias, (list, tuple)) and alias:
        alias = min((str(x) for x in alias), key=len)  # shortest alias (e.g., "l" or "yl")
    alias = str(alias or ("l" if is_direct else "yl"))

    # --- A) Enforce limits BEFORE starting task ---
    est_size_mb = await _estimate_direct_size_mb(url) if is_direct else None
    ok, reason = await check_limits_before_task(uid, est_size_mb)
    if not ok:
        # unify UI: always show Ads/Upgrade keyboard
        await message.reply(
            f"🚫 {reason}\n\nWatch an ad or upgrade:",
            reply_markup=build_over_quota_kb(),
        )
        # Optionally flag ad-view intent for analytics/unlocks
        try:
            await increment_ads_watched(uid)
        except Exception:
            pass
        return
    # ---------------------------------------------

    cmd_text = f"/{alias} {url}"
    LOGGER.info("[AUTO_LEECH] Will run: %s (ext=%r direct=%s est=%sMB)", cmd_text, ext, is_direct, est_size_mb)
    # ---------------------------------------------

    # Direct-call the target handler (more reliable for public usage)
    try:
        if is_direct:
            from .mirror_leech import leech as _target
        else:
            from .ytdlp import ytdl_leech as _target
    except Exception as e:
        LOGGER.exception("[AUTO_LEECH] Could not import target: %s", e)
        await message.reply(f"❌ Auto-route failed: {e}")
        return

    # Shim message so downstream parser sees what it expects
    try:
        try:
            message.command = [alias, url]  # emulate pyrogram's parsed command tokens
        except Exception:
            pass
        message.text = cmd_text
        await _target(client, message)
        LOGGER.info("[AUTO_LEECH] Executed via direct-call")
    except Exception as e:
        LOGGER.exception("[AUTO_LEECH] Unhandled error")
        try:
            await message.reply(f"❌ Auto-route failed: {e}")
        except Exception:
            pass
