from asyncio import sleep
from pyrogram.errors import FloodWait, FloodPremiumWait
from ...core.config_manager import Config
from ..telegram_helper.bot_commands import BotCommands
from re import match as re_match
from time import time
import re
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton  # ✅ import

# -------- MINIFY TRANSFER STATUS --------
_BAR_RE = re.compile(r"(?:█|■|\[[^\]]*%[^\]]*\])")
_HEADER_RE = re.compile(r"^1\.(?:Download|Upload):\s", re.I)

def _premium_button() -> InlineKeyboardButton:
    """
    Prefer a real URL (web checkout) if provided.
    Fallback to Telegram deep-link ?start=premium if BOT_USERNAME is set.
    Last resort: a callback the bot can handle.
    """
    try:
        url = getattr(Config, "PREMIUM_URL", "") or ""
        if url:
            return InlineKeyboardButton("💎 Upgrade to Premium", url=url)
        bot_un = getattr(Config, "BOT_USERNAME", "") or ""
        if bot_un:
            return InlineKeyboardButton("💎 Upgrade to Premium", url=f"https://t.me/leechflixbot?start=premium")
    except Exception:
        pass
    return InlineKeyboardButton("💎 Upgrade to Premium", url="https://t.me/leechflixbot?start=premium")

def build_over_quota_kb():
    rows = []

    rows.append([InlineKeyboardButton("🎯 Watch Ad (Reset Usage)", url="https://t.me/leechflixbot/app?startapp=watchad")])
    rows.append([_premium_button()])
    return InlineKeyboardMarkup(rows)

# ✨ single place to add your signature
def _with_signature(caption: str | None) -> str:
    return f"Saved by @Leechflixbot\n\n{caption or ''}"

def _minify_transfer_status(text: str) -> str:
    """
    Keep only:
      - First line: "1.Download/Upload: <name>"
      - A progress bar line (█/■ or "[...%...]")
      - 'Size:', 'ETA:', 'Speed:' lines
    """
    try:
        if not text or not isinstance(text, str):
            return text

        lines = [ln.rstrip() for ln in text.splitlines()]
        if not lines or not _HEADER_RE.match(lines[0]):
            return text

        keep = [lines[0]]  # header

        # find a progress bar
        bar_line = None
        for ln in lines[1:]:
            if _BAR_RE.search(ln):
                bar_line = ln
                break
        if bar_line:
            keep.append(bar_line)

        # keep K/V lines
        for ln in lines:
            if ln.startswith(("Size:", "ETA:", "Speed:")):
                keep.append(ln)

        # de-dupe, keep order
        dedup, seen = [], set()
        for ln in keep:
            if ln not in seen:
                seen.add(ln)
                dedup.append(ln)

        return "\n".join(dedup)
    except Exception:
        return text

# -------- /MINIFY TRANSFER STATUS --------

from ... import LOGGER, status_dict, task_dict_lock, intervals, DOWNLOAD_DIR
from ...core.mltb_client import TgClient
from ..ext_utils.bot_utils import SetInterval
from ..ext_utils.exceptions import TgLinkException
from ..ext_utils.status_utils import get_readable_message

async def send_message(message, text, buttons=None, reply_markup=None, block=True):
    # alias support: reply_markup → buttons
    if reply_markup is not None and buttons is None:
        buttons = reply_markup
    try:
        trimmed = _minify_transfer_status(text)
        if trimmed is not None and trimmed is not text:
            text = trimmed
            buttons = None
    except Exception:
        pass

    try:
        return await message.reply(
            text=text,
            quote=True,
            disable_web_page_preview=True,
            disable_notification=True,
            reply_markup=buttons,
        )
    except FloodWait as f:
        LOGGER.warning(str(f))
        if not block:
            return str(f)
        await sleep(f.value * 1.2)
        return await send_message(message, text, buttons)
    except Exception as e:
        LOGGER.error(str(e))
        return str(e)

async def edit_message(message, text, buttons=None, reply_markup=None, block=True):
    # alias support: reply_markup → buttons
    if reply_markup is not None and buttons is None:
        buttons = reply_markup
    try:
        trimmed = _minify_transfer_status(text)
        if trimmed is not None and trimmed is not text:
            text = trimmed
            buttons = None
    except Exception:
        pass

    try:
        return await message.edit(
            text=text,
            disable_web_page_preview=True,
            reply_markup=buttons,
        )
    except FloodWait as f:
        LOGGER.warning(str(f))
        if not block:
            return str(f)
        await sleep(f.value * 1.2)
        return await edit_message(message, text, buttons)
    except Exception as e:
        LOGGER.error(str(e))
        return str(e)

async def send_file(message, file, caption=""):
    try:
        return await message.reply_document(
            document=file,
            quote=True,
            caption=_with_signature(caption),
            disable_notification=True
        )
    except FloodWait as f:
        LOGGER.warning(str(f))
        await sleep(f.value * 1.2)
        return await send_file(message, file, caption)
    except Exception as e:
        LOGGER.error(str(e))
        return str(e)

async def send_rss(text, chat_id, thread_id):
    try:
        app = TgClient.user or TgClient.bot
        return await app.send_message(
            chat_id=chat_id,
            text=text,
            disable_web_page_preview=True,
            message_thread_id=thread_id,
            disable_notification=True,
        )
    except (FloodWait, FloodPremiumWait) as f:
        LOGGER.warning(str(f))
        await sleep(f.value * 1.2)
        return await send_rss(text)
    except Exception as e:
        LOGGER.error(str(e))
        return str(e)

async def delete_message(message):
    try:
        await message.delete()
    except Exception as e:
        LOGGER.error(str(e))

async def auto_delete_message(cmd_message=None, bot_message=None):
    await sleep(60)
    if cmd_message is not None:
        await delete_message(cmd_message)
    if bot_message is not None:
        await delete_message(bot_message)

async def delete_status():
    async with task_dict_lock:
        for key, data in list(status_dict.items()):
            try:
                await delete_message(data["message"])
                del status_dict[key]
            except Exception as e:
                LOGGER.error(str(e))

async def get_tg_link_message(link):
    message = None
    links = []
    if link.startswith("https://t.me/"):
        private = False
        msg = re_match(r"https:\/\/t\.me\/(?:c\/)?([^\/]+)(?:\/[^\/]+)?\/([0-9-]+)", link)
    else:
        private = True
        msg = re_match(r"tg:\/\/openmessage\?user_id=([0-9]+)&message_id=([0-9-]+)", link)
        if not TgClient.user:
            raise TgLinkException("USER_SESSION_STRING required for this private link!")

    chat = msg[1]
    msg_id = msg[2]
    if "-" in msg_id:
        start_id, end_id = msg_id.split("-")
        msg_id = start_id = int(start_id)
        end_id = int(end_id)
        btw = end_id - start_id
        if private:
            link = link.split("&message_id=")[0]
            links.append(f"{link}&message_id={start_id}")
            for _ in range(btw):
                start_id += 1
                links.append(f"{link}&message_id={start_id}")
        else:
            link = link.rsplit("/", 1)[0]
            links.append(f"{link}/{start_id}")
            for _ in range(btw):
                start_id += 1
                links.append(f"{link}/{start_id}")
    else:
        msg_id = int(msg_id)

    if chat.isdigit():
        chat = int(chat) if private else int(f"-100{chat}")

    if not private:
        try:
            message = await TgClient.bot.get_messages(chat_id=chat, message_ids=msg_id)
            if message.empty:
                private = True
        except Exception as e:
            private = True
            if not TgClient.user:
                raise e

    if not private:
        return (links, "bot") if links else (message, "bot")
    elif TgClient.user:
        try:
            user_message = await TgClient.user.get_messages(chat_id=chat, message_ids=msg_id)
        except Exception as e:
            raise TgLinkException(f"You don't have access to this chat!. ERROR: {e}") from e
        if not user_message.empty:
            return (links, "user") if links else (user_message, "user")
    else:
        raise TgLinkException("Private: Please report!")

async def temp_download(msg):
    path = f"{DOWNLOAD_DIR}temp"
    return await msg.download(file_name=f"{path}/")

async def update_status_message(sid, force=False):
    if intervals["stopAll"]:
        return
    async with task_dict_lock:
        data = status_dict.get(sid)
        if not data:
            if obj := intervals["status"].get(sid):
                obj.cancel()
                del intervals["status"][sid]
            return

        if not force and (time() - data["time"] < Config.STATUS_UPDATE_INTERVAL):
            return

        data["time"] = time()
        page_no = data["page_no"]
        status = data["status"]
        is_user = data["is_user"]
        page_step = data["page_step"]

        text, buttons = await get_readable_message(sid, is_user, page_no, status, page_step)
        if text is None:
            del status_dict[sid]
            if obj := intervals["status"].get(sid):
                obj.cancel()
                del intervals["status"][sid]
            return

        if text != data["message"].text:
            msg = await edit_message(data["message"], text, None, block=False)
            if isinstance(msg, str):
                if msg.startswith("Telegram says: [40"):
                    del status_dict[sid]
                    if obj := intervals["status"].get(sid):
                        obj.cancel()
                        del intervals["status"][sid]
                else:
                    LOGGER.error(f"Status with id: {sid} hasn't been updated. Error: {msg}")
                return
            data["message"].text = text
            data["time"] = time()

async def _send_to_chat_id(chat_id: int, text: str, buttons=None, block=True):
    """
    Send a message directly to a chat_id.
    """
    from ..telegram_helper.message_utils import _minify_transfer_status
    from ... import LOGGER
    from ...core.mltb_client import TgClient
    from asyncio import sleep
    from pyrogram.errors import FloodWait, FloodPremiumWait

    try:
        text = _minify_transfer_status(text)
        if isinstance(text, str) and (text.startswith("1.Download:") or text.startswith("1.Upload:")):
            buttons = None
    except Exception:
        pass

    app = TgClient.bot or TgClient.user
    try:
        return await app.send_message(
            chat_id=chat_id,
            text=text,
            disable_web_page_preview=True,
            reply_markup=buttons,
            disable_notification=True,
        )
    except (FloodWait, FloodPremiumWait) as f:
        LOGGER.warning(str(f))
        if not block:
            return str(f)
        await sleep(f.value * 1.2)
        return await _send_to_chat_id(chat_id, text, buttons, block)
    except Exception as e:
        LOGGER.error(str(e))
        return str(e)

async def send_status_message(msg, user_id=0):
    """
    DM status when possible; fallback to same chat.
    """
    if intervals["stopAll"]:
        return

    recipient_uid = getattr(getattr(msg, "from_user", None), "id", None)
    sid = user_id or recipient_uid or msg.chat.id
    is_user = bool(recipient_uid and sid == recipient_uid)

    async with task_dict_lock:
        text, buttons = await get_readable_message(sid, is_user)
        if text is None:
            return

        message_obj = None
        if recipient_uid:
            try:
                app = TgClient.bot or TgClient.user
                message_obj = await app.send_message(
                    chat_id=recipient_uid,
                    text=_minify_transfer_status(text),
                    disable_web_page_preview=True,
                )
            except Exception as e:
                LOGGER.warning(f"DM status failed, falling back to chat: {e}")

        if message_obj is None:
            message_obj = await send_message(msg, text, None, block=False)
            if isinstance(message_obj, str):
                LOGGER.error(f"Status with id: {sid} hasn't been sent. Error: {message_obj}")
                return

        message_obj.text = _minify_transfer_status(text) or text
        status_dict[sid] = {
            "message": message_obj,
            "time": time(),
            "page_no": 1,
            "page_step": 1,
            "status": "All",
            "is_user": is_user,
        }

        if not intervals["status"].get(sid):
            intervals["status"][sid] = SetInterval(
                Config.STATUS_UPDATE_INTERVAL, update_status_message, sid
            )
