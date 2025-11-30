from ..helper.ext_utils.db_handler import database
from ..helper.ext_utils.quota_manager import get_usage, get_limits
from ..helper.ext_utils.bot_utils import new_task
from ..helper.telegram_helper.message_utils import send_message

@new_task
async def buy_premium(_, message):
    text = (
        "💎 <b>Premium Plan</b>\n"
        "\n• Daily limit: 50 GB"
        "\n• Priority in queues"
        "\n• NSFW sources enabled"
        "\n\nTo purchase, contact the admin or use /setpremium if you're the owner."
    )
    await send_message(message, text)

@new_task
async def set_premium(_, message):
    parts = message.text.split()
    if len(parts) < 2 and not message.reply_to_message:
        return await send_message(message, "Usage: <code>/setpremium &lt;user_id&gt;</code> or reply to a user")
    uid = None
    if message.reply_to_message and message.reply_to_message.from_user:
        uid = message.reply_to_message.from_user.id
    if not uid:
        try:
            uid = int(parts[1])
        except Exception:
            return await send_message(message, "Give a numeric user id or reply to a user.")
    await qm_set_premium(uid, True)
    await send_message(message, f"✅ Set premium for <code>{uid}</code>")

@new_task
async def show_usage(_, message):
    uid = message.from_user.id
    used = await get_usage(uid)
    prem, limit = await get_limits(uid)
    remaining = max(0, limit - used)

    def fmt(b):
        b = float(b)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if b < 1024 or unit == "TB":
                return f"{b:.2f} {unit}"
            b /= 1024

    suffix = " [Premium]" if prem else ""
    await send_message(
        message,
        f"📊 Today's usage: {fmt(used)} / {fmt(limit)} "
        f"(remaining {fmt(remaining)}){suffix}"
    )
