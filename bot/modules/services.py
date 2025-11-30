import logging
import os
from time import time

from ..db.sqlite_db import (
    get_user,
    get_daily_limit_mb,
    set_referrer,
    add_premium_days,
    get_user_thread,
    set_user_thread,
)
from ..helper.ext_utils.bot_utils import new_task
from ..helper.telegram_helper.message_utils import (
    send_message,
    edit_message,
    send_file,
)
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# Prefer config manager, with env fallbacks for portability
try:
    from ..core.config_manager import Config
except Exception:  # pragma: no cover
    class _Dummy:  # minimal fallback if Config is unavailable
        pass
    Config = _Dummy()

LOGGER = logging.getLogger(__name__)

# Bot username for links
BOT_USERNAME = (
    getattr(Config, "BOT_USERNAME", None)
    or os.getenv("BOT_USERNAME", "leechflixbot")
).lstrip("@")

# Forum (topics) target (supergroup with Topics enabled)
FORUM_CHAT_ID = int(
    getattr(Config, "FORUM_CHAT_ID", 0)
    or os.getenv("FORUM_CHAT_ID", "0")
    or 0
)

# Try to load premium_shop for proper Crypto Bot invoices
try:
    from . import premium_shop
    _PREMIUM_SHOP = premium_shop
    LOGGER.info("premium_shop loaded successfully.")
except Exception as _imp_err:
    _PREMIUM_SHOP = None
    LOGGER.warning("premium_shop not found, using fallback only. err=%s", _imp_err)

# ----- plans -----
_PRICE_LABELS = {
    "1m":  "1 month – $2",
    "3m":  "3 months – $5 (15% Off)",
    "6m":  "6 months – $8 (30% Off)",
    "12m": "12 months – $15 (50% Off)",
}
_PLAN_DAYS = {
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "12m": 365,
}


async def ensure_user_thread(client, user_id: int) -> int:
    """
    Create (once) or reuse a per-user topic and return its thread id.

    Handles variants where create_forum_topic returns:
      - Message(message_thread_id=...)
      - Message(forum_topic_created=ForumTopicCreated(id=...))
      - ForumTopicCreated(id=...)
      - ForumTopic(id=... or message_thread_id=...)
      - Wrapped: .message.message_thread_id
    """
    if not FORUM_CHAT_ID:
        LOGGER.info("Topics disabled: FORUM_CHAT_ID is not set")
        return 0

    # 1) Reuse if already stored
    try:
        existing = int(await get_user_thread(user_id) or 0)
    except Exception as e:
        LOGGER.warning("ensure_user_thread: get_user_thread failed uid=%s: %s", user_id, e)
        existing = 0
    if existing > 0:
        return existing

    def _extract_thread_id(obj) -> int:
        """Robustly extract a thread id from pyrogram return types."""
        try:
            mtid = getattr(obj, "message_thread_id", None)
            if mtid:
                return int(mtid)
            ftc = getattr(obj, "forum_topic_created", None)
            if ftc:
                ftc_id = getattr(ftc, "id", None)
                if ftc_id:
                    return int(ftc_id)
            direct_id = getattr(obj, "id", None)
            if isinstance(direct_id, int) and direct_id > 0:
                return direct_id
            msg = getattr(obj, "message", None)
            if msg:
                mtid2 = getattr(msg, "message_thread_id", None)
                if mtid2:
                    return int(mtid2)
                ftc2 = getattr(msg, "forum_topic_created", None)
                if ftc2:
                    ftc2_id = getattr(ftc2, "id", None)
                    if ftc2_id:
                        return int(ftc2_id)
        except Exception:
            pass
        return 0

    async def _create_once() -> int:
        try:
            LOGGER.info(
                "ensure_user_thread: creating topic for uid=%s in chat_id=%s",
                user_id, FORUM_CHAT_ID
            )
            # Use positional args for broader compatibility
            topic = await client.create_forum_topic(FORUM_CHAT_ID, str(user_id))
            thread_id = _extract_thread_id(topic)
            LOGGER.info(
                "ensure_user_thread: created topic uid=%s -> thread_id=%s (raw=%r)",
                user_id, thread_id, topic
            )
            return thread_id
        except Exception as e:
            LOGGER.warning("ensure_user_thread: create_forum_topic failed uid=%s: %s", user_id, e)
            return 0

    # 2) Create ONCE (no retries to avoid duplicate topics)
    thread_id = await _create_once()
    if thread_id <= 0:
        LOGGER.warning(
            "ensure_user_thread: could not extract thread id for uid=%s. "
            "Check bot permissions / Pyrogram version. Not retrying to prevent topic spam.",
            user_id
        )
        return 0

    # 3) Persist
    try:
        await set_user_thread(user_id, thread_id)
        LOGGER.info("ensure_user_thread: stored uid=%s thread_id=%s", user_id, thread_id)
    except Exception as e:
        LOGGER.warning("ensure_user_thread: set_user_thread failed uid=%s: %s", user_id, e)

    return thread_id


# ----- helpers -----
def _plural(n: int, unit: str) -> str:
    return f"{n} {unit}{'' if n == 1 else 's'}"


async def _premium_plans_kb(uid: int) -> InlineKeyboardMarkup:
    """
    Build a keyboard with URL buttons pointing to Crypto Bot invoices.
    Uses premium_shop if available, otherwise falls back to /start deep-links.
    """
    if _PREMIUM_SHOP:
        try:
            kb = await _PREMIUM_SHOP._build_invoice_keyboard(uid)
            return kb
        except Exception as e:
            LOGGER.error("premium_shop._build_invoice_keyboard failed: %s", e)

    rows = []
    for code, label in _PRICE_LABELS.items():
        url = f"https://t.me/{BOT_USERNAME}?start=premium-{code}-{uid}"
        rows.append([InlineKeyboardButton(label, url=url)])
    return InlineKeyboardMarkup(rows)


def _invite_text(uid: int, invited_count: int) -> str:
    return (
        "🤝 Invite Friends\n\n"
        "For every 100 new users you bring, you will receive a ⭐️ Premium plan for 1 month. "
        "If a user you referred purchases a premium subscription, you will receive 10% of the amount "
        "as an extension of your existing plan or as a new premium subscription.\n\n"
        f"Invited users: {invited_count:,}\n\n"
        "Your personal link:\n"
        f"https://t.me/{BOT_USERNAME}?start=ref{uid}"
    )


def _help_text() -> str:
    return (
        "📕 Help\n\n"
        "Send links from any platform here, choose the quality, and I’ll send the media back to you. "
        "You can always find your previous leeched files using /myfiles.\n\n"
        "If you need more traffic, /invite your friends or try /premium.\n"
        "If you face an issue or bug, please forward the message to @FaucetSupportBot or send a screenshot.\n\n"
        "Supported sites:\nhttps://telegramfaucet.me/supportedsites.md"
    )


def _about_text() -> str:
    return (
        "🤖 About us\n\n"
        "This bot is made with ❤️ and unlimited ☕️ by @FMDEVELOPER\n"
        "📊 Buy Ads: @FMDev\n"
        "⚙️ Report Bugs: @FaucetSupportBot\n\n"
        "Other bots:\n"
        "@TONEmpiresBot\n"
        "@TeleFlixBot"
    )


# ----- message handlers -----
async def help(_, message):
    await send_message(message, _help_text())


async def about(_, message):
    await send_message(message, _about_text())


@new_task
async def start(client, message):
    uid = getattr(getattr(message, "from_user", None), "id", None) or message.chat.id

    # Parse /start payload (e.g. "/start premium", "/start ref84895335", "/start 84895335")
    raw_text = message.text or ""
    parts = raw_text.split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    payload_lc = payload.lower()

    # Proactively ensure a per-user topic exists in the forum group.
    try:
        if FORUM_CHAT_ID:
            await ensure_user_thread(client, uid)
    except Exception as e:
        LOGGER.warning("/start ensure_user_thread failed uid=%s: %s", uid, e)

    # --- Premium deep-link handling ---
    if payload_lc == "premium" or payload_lc.startswith("premium-"):
        if _PREMIUM_SHOP:
            try:
                await _PREMIUM_SHOP._send_invoice_menu(message)
                return
            except Exception as e:
                LOGGER.warning("premium_shop._send_invoice_menu failed: %s", e)

        premium_text = (
            "💎 Premium\n\n"
            "— Daily usage limit: 60 GB\n"
            "— Video duration: up to 10 hours\n"
            "— Downloading requests in parallel: 10\n"
            "— Increased priority in processing queue\n"
            "— Disabled NSFW filter\n\n"
            "👉 Choose a plan below and complete your purchase:"
        )
        await send_message(message, premium_text, await _premium_plans_kb(uid))
        return

    # --- Referral handling ---
    ref_id = None
    try:
        if payload_lc.startswith("ref"):
            rest = payload_lc[3:]
            if rest.isdigit():
                ref_id = int(rest)
        elif payload_lc.isdigit():
            ref_id = int(payload_lc)
    except Exception:
        ref_id = None

    try:
        u = await get_user(uid)

        if ref_id and ref_id != uid:
            try:
                added = await set_referrer(uid, ref_id)
                if added:
                    try:
                        ref_u = await get_user(ref_id)
                        total_refs = int(ref_u.get("referrals") or 0)
                        if total_refs > 0 and total_refs % 100 == 0:
                            await add_premium_days(ref_id, 30)
                            LOGGER.info(
                                "Milestone reached: referrer=%s referrals=%s -> +30 days",
                                ref_id, total_refs
                            )
                    except Exception as e:
                        LOGGER.warning(
                            "Failed to check/award referral milestone (referrer=%s): %s",
                            ref_id, e
                        )
            except Exception as e:
                LOGGER.warning("/start referral set failed for uid=%s, ref=%s: %s", uid, ref_id, e)

    except Exception as e:
        LOGGER.warning("/start get_user failed for %s: %s", uid, e)
        u = {"user_id": uid, "premium_active": 0, "daily_usage_mb": 0}

    premium_days = int(u.get("premium_active") or 0)
    tier = "Free account" if premium_days <= 0 else f"Premium ({_plural(premium_days, 'day')} left)"
    used_mb = int(u.get("daily_usage_mb") or 0) / 1000
    daily_limit = (await get_daily_limit_mb(uid)) / 1000

    text = (
        f"ID: {u['user_id']}\n"
        f"{tier}\n\n"
        "I can extract and download for you photos/images/audio/files/archives from YouTube, Instagram, TikTok, "
        "Facebook, SoundCloud, Vimeo, VK, Twitter posts, and 1000+ audio/video hostings.\n\n"
        "Daily usage:\n"
        f"{used_mb} / {daily_limit} GB\n\n"
        "Need more traffic? Use /premium to upgrade or /invite friends."
    )
    await send_message(message, text)


@new_task
async def invite(client, message):
    uid = getattr(getattr(message, "from_user", None), "id", None) or message.chat.id
    try:
        u = await get_user(uid)
        invited = int(u.get("referrals") or 0)
    except Exception as e:
        LOGGER.warning("/invite get_user failed uid=%s: %s", uid, e)
        invited = 0

    text = _invite_text(uid, invited)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🤝 Invite a friend",
            url=(
                "https://t.me/share/url"
                f"?url=https%3A%2F%2Ft.me%2F{BOT_USERNAME}%3Fstart%3Dref{uid}"
                "&text=I%20can%20extract%20and%20download%20media%20from%201000%2B%20sites.%20"
                "Send%20me%20a%20URL%20to%20get%20your%20file."
            )
        )],
        # NOTE: 'copy_text' requires a recent Bot API; remove this button if unsupported on your stack.
        [InlineKeyboardButton("Copy link", copy_text=f"https://t.me/{BOT_USERNAME}?start=ref{uid}")]
    ])

    await send_message(message, text, kb)


@new_task
async def ping(_, message):
    start_ms = int(round(time() * 1000))
    reply = await send_message(message, "Starting Ping")
    end_ms = int(round(time() * 1000))
    await edit_message(reply, f"{end_ms - start_ms} ms")


@new_task
async def log(_, message):
    try:
        await send_file(message, "log.txt")
    except Exception as e:
        LOGGER.error("Failed to send log.txt: %s", e)
        await send_message(message, f"Could not send log: {e}")


# ----- callback handler for inline buttons -----
@new_task
async def premium_callback(client, cq: CallbackQuery):
    """
    Handles premium menu re-open (premium_open).
    URL buttons are used for real payments, so no buy_* callback needed anymore.
    """
    data = (cq.data or "").strip().lower()
    uid = cq.from_user.id if cq.from_user else cq.message.chat.id

    try:
        await cq.answer()
    except Exception:
        pass

    if data == "premium_open":
        premium_text = (
            "💎 Premium\n\n"
            "— Daily usage limit: 60 GB\n"
            "— Video duration: up to 10 hours\n"
            "— Downloading requests in parallel: 10\n"
            "— Increased priority in processing queue\n"
            "— Disabled NSFW filter\n\n"
            "👉 Choose a plan below and complete your purchase:"
        )
        await edit_message(cq.message, premium_text, await _premium_plans_kb(uid))
        return

    # default/fallback: just show plans again
    await edit_message(cq.message, "💎 Premium\n\n👉 Choose a plan below:", await _premium_plans_kb(uid))
