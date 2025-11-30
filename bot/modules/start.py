"""
import logging
from ..db.sqlite_db import get_user, get_daily_limit_mb, set_referrer, add_premium_days
from ..helper.ext_utils.bot_utils import new_task
from ..helper.telegram_helper.message_utils import send_message

LOGGER = logging.getLogger(__name__)

@new_task
async def start(client, message):
    uid = getattr(getattr(message, "from_user", None), "id", None) or message.chat.id

    # Parse /start arguments safely
    raw_text = message.text or ""
    parts = raw_text.split()
    payload = parts[1].strip().lower() if len(parts) > 1 else ""

    # If opened via deep link ?start=premium, show premium onboarding instead of the normal start
    if payload == "premium":
        premium_text = (
            "💎 **Premium**\n\n"
            "Thanks for your interest! Premium gives you:\n"
            "• Higher daily quota\n"
            "• Faster queue / priority uploads\n"
            "• Early access to new features\n\n"
            "Use /premium to upgrade now"
        )
        await send_message(message, premium_text, parse_mode="Markdown")
        return

    # Otherwise, treat payload as a possible numeric referrer id
    try:
        ref_id = int(payload) if payload.isdigit() else None
    except Exception:
        ref_id = None

    # Fetch/create user & process referral bonus
    try:
        u = await get_user(uid)
        if ref_id and ref_id != uid:
            added = await set_referrer(uid, ref_id)
            if added:
                # Reward the referrer
                await add_premium_days(ref_id, 1)
    except Exception as e:
        LOGGER.warning(f"/start get_user failed for {uid}: {e}")
        u = {"user_id": uid, "premium_active": 0, "daily_usage_mb": 0}

    # Build status text
    premium_days = int(u.get("premium_active") or 0)
    tier = "Free account" if premium_days <= 0 else f"Premium ({premium_days} days left)"
    used_mb = int(u.get("daily_usage_mb") or 0)
    daily_limit = await get_daily_limit_mb(uid)

    text = (
        f"ID: {u['user_id']}\n"
        f"{tier}\n\n"
        "I can extract and download for you photos/images/audio/files/archives from YouTube, Instagram, TikTok, "
        "Facebook, SoundCloud, Vimeo, VK, Twitter posts and 1000+ audio/video hostings. "
        "Just send me a URL to a post with media or a direct link.\n\n"
        "Daily usage:\n"
        f"Traffic: {used_mb} / {daily_limit} MB\n\n"
        "Need more? Use /premium to upgrade."
    )

    await send_message(message, text)
	"""
