from pyrogram.errors import MediaEmpty, MediaInvalid, RPCError
from ..helper.ext_utils.bot_utils import new_task
from ..helper.telegram_helper.message_utils import send_message
from ..db.sqlite_db import resolve_file_id_by_prefix, get_file, increment_download
from ..core.mltb_client import TgClient


@new_task
async def get_cached(_, message):
    """
    Usage: /get_<shortOrFullId>
    - Resolves a short file_id prefix to the user's full file_id
    - Sends the cached media back to the chat
    """
    text = (message.text or "").strip()
    if not text.startswith("/get_"):
        return

    # Extract token after /get_
    try:
        token = text.split("_", 1)[1].strip()
    except Exception:
        return await send_message(message, "Usage: /get_<id>")

    uid = message.from_user.id
    full_id = await resolve_file_id_by_prefix(uid, token) or token

    rec = await get_file(full_id)
    if not rec:
        return await send_message(message, "Not found (check the ID or ownership).")

    chat_id = message.chat.id
    caption = rec.get("name") or ""
    thumb = rec.get("thumbnail") or None

    # Strategy:
    # 1) Try as VIDEO first (better inline playback).
    # 2) If that fails, fall back to DOCUMENT.
    try:
        await TgClient.bot.send_video(
            chat_id,
            full_id,
            caption=caption,
            thumb=thumb,
            supports_streaming=True,
        )
    except (MediaEmpty, MediaInvalid, RPCError, Exception):
        try:
            await TgClient.bot.send_document(
                chat_id,
                full_id,
                caption=caption,
                thumb=thumb,
            )
        except Exception as e:
            return await send_message(message, f"Failed to send cached media: {e}")

    # Increment download counter (best-effort)
    try:
        await increment_download(full_id)
    except Exception:
        pass
