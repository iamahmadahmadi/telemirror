import asyncio
import logging
import os
import time
from typing import Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon.tl import types

try:
    # Typing hint only; fine if missing
    from telethon.hints import EntityLike
except Exception:
    from typing import Any as EntityLike

# Use FastTelethonhelper for speed + live progress edits
try:
    from FastTelethonhelper import fast_upload
except Exception:
    fast_upload = None  # will fallback to native Telethon upload

# Silence extremely verbose connection logs
logging.getLogger("telethon.network.mtprotosender").setLevel(logging.WARNING)

# Singleton Telethon client (reused across uploads)
_client: Optional[TelegramClient] = None
_client_lock = asyncio.Lock()


def _get_user_session_string() -> str:
    """
    Read a user StringSession from common env var names.
    """
    for k in (
        "USER_SESSION_STRING",
        "TELETHON_STRING",
        "TELETHON_SESSION",
        "TG_USER_SESSION",
    ):
        v = os.getenv(k)
        if v and v.strip() and v.strip().lower() != "none":
            return v.strip()
    return ""


async def _get_client(api_id: int, api_hash: str, _bot_token_ignored: str) -> TelegramClient:
    """
    Create/return a Telethon client authenticated with a USER StringSession.
    NOTE: The 'bot_token' parameter is accepted to preserve the caller API,
    but is ignored. We always use the USER session here.
    """
    global _client
    async with _client_lock:
        if _client and _client.is_connected():
            return _client

        user_session_string = "1AZWarzwBu0nknj1A7d3h4IjxaDLrlXd211gkKvk7fH90r8Ui-M4VFubiyPWsM8h0XxvJDOtV9rWepmJnWhbVQcFy8iMm-K2pzK-3g-FxjrGiWS8P3P3Evcv1P41QCjE6RvQBef-kSijo1R00s5aL_K4LP9Vq38jHm2dTDx9BrfChupVK38QuOHRDAoze53Uz0YQrKSZcH0mtYgPf_Mr81IGu7gDt_HeLkcWAyjvAaDsFLf5C6yQUCGY3UJSgnVIIRSIoEKaOY5_rokuSeNi2RVpxm7hDT7wppYY94FyzDvVCH7OkpRcYXgnf09J0mbFdhH1JUPF-7Ffv_GvJKmt-R-K1-RLfAPw="
        if not user_session_string:
            raise RuntimeError(
                "USER_SESSION_STRING is missing/empty. "
                "Set USER_SESSION_STRING (or TELETHON_STRING/TELETHON_SESSION/TG_USER_SESSION) in the environment."
            )

        session = StringSession(user_session_string)
        _client = TelegramClient(session, api_id, api_hash)
        await _client.connect()

        # StringSession should already be authorized; if not, we cannot proceed without 2FA/phone code.
        if not await _client.is_user_authorized():
            raise RuntimeError(
                "Telethon user is not authorized. Please regenerate USER_SESSION_STRING with a logged-in account."
            )

        return _client


async def _resolve_entity(client: TelegramClient, chat: EntityLike):
    """
    Resolve any chat/user/channel robustly.

    Strategy:
      1) Try client.get_input_entity(chat) directly (works for usernames, links,
         and also for channel/group IDs when the user is a member).
      2) If that fails and 'chat' is an int, warm the dialog cache
         (client.get_dialogs(limit=None)), then try again.
      3) Handle Bot API -100... and -... fallbacks for channels/groups.
      4) If 'chat' is a positive int (PeerUser) and still unresolved, raise clear error.
    """
    # First attempt: direct resolution (handles @usernames, t.me links, already cached ids)
    try:
        return await client.get_input_entity(chat)
    except Exception:
        pass

    # If it's an int, try to warm cache by loading all dialogs, then resolve again
    if isinstance(chat, int):
        try:
            # Load all dialogs to populate Telethon's entity cache
            await client.get_dialogs(limit=None)
            # Retry resolution now that cache is warm
            return await client.get_input_entity(chat)
        except Exception:
            pass

        s = str(chat)
        # Supergroup/channel ids (-100XXXXXXXXXXXX)
        if s.startswith("-100"):
            try:
                return await client.get_input_entity(types.PeerChannel(int(s[4:])))
            except Exception:
                pass
        # Basic group (-XXXXXXXX)
        if chat < 0:
            try:
                return await client.get_input_entity(types.PeerChat(-chat))
            except Exception:
                pass

        # Positive int -> user id (needs access hash or cached entity)
        raise RuntimeError(
            f"Cannot resolve user id {chat} without access hash. "
            f"Use a username (e.g. '@username'), a t.me link, or ensure this user is in your contacts/dialogs. "
            f"If you intended a channel/group, pass its @username or full -100... id."
        )

    # Non-int and still unresolved: give it back (Telethon may accept later if cached)
    return chat


def _fmt_bytes(n: int) -> str:
    try:
        n = int(n)
        if n < 1024:
            return f"{n}B"
        units = ["KB", "MB", "GB", "TB", "PB"]
        v = float(n)
        for u in units:
            v /= 1024.0
            if v < 1024.0:
                return f"{v:.2f}{u}"
        return f"{v:.2f}EB"
    except Exception:
        return f"{n}B"


def _fmt_time(seconds: float | None) -> str:
    if seconds is None or seconds <= 0:
        return "-"
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


async def fast_send_video(
    api_id: int,
    api_hash: str,
    bot_token: str,           # kept for backward-compat; ignored internally
    chat: EntityLike,
    file_path: str,
    *,
    duration: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    thumb: Optional[str] = None,
    caption: Optional[str] = None,
    reply_to: Optional[int] = None,
    progress_cb=None,                   # (sent, total) -> None for your internal counters
    progress_title: Optional[str] = None,
    progress_gid: Optional[str] = None,
):
    """
    Fast upload with live progress using FastTelethonhelper as the USER.
    Creates a Telethon progress message under `reply_to`, updates it per chunk,
    sends the media, and returns the Telethon Message.
    """
    client = await _get_client(api_id, api_hash, bot_token)
    peer = await _resolve_entity(client, chat)

    attrs = [
        types.DocumentAttributeVideo(
            duration=duration or 0,
            w=width or 0,
            h=height or 0,
            supports_streaming=True,
        )
    ]

    total_size = 0
    try:
        total_size = os.path.getsize(file_path)
    except Exception:
        pass

    title = progress_title or f"1.Upload: {os.path.basename(file_path)}"
    gid_line = f"Gid:{progress_gid}" if progress_gid else "Gid:-"

    def _make_text(done: int, total: int, speed: float | None) -> str:
        pct = (done / total * 100.0) if total else 0.0
        segs = 12
        filled = int(round(pct / (100 / segs))) if total > 0 else 0
        bar = "■" * filled + "□" * (segs - filled)
        eta = ((total - done) / speed) if (speed and speed > 0 and total > 0) else None
        return "\n".join([
            title,
            f"[{bar}] {pct:.1f}%",
            f"Processed: {_fmt_bytes(done)}",
            f"Size: {_fmt_bytes(total)}",
            f"Speed: {_fmt_bytes(int(speed or 0))}/s",
            f"ETA: {_fmt_time(eta)}",
            gid_line,
        ])

    progress_msg = await client.send_message(
        peer,
        _make_text(0, total_size, 0.0),
        reply_to=reply_to,
        link_preview=False,
    )

    last_t = time.time()
    last_b = 0

    # FastTelethonhelper expects a function that RETURNS the text to display
    def _progress_text(done: int, total: int) -> str:
        nonlocal last_t, last_b
        try:
            if progress_cb:
                progress_cb(done, total)   # keep your internal counters fresh
        except Exception:
            pass
        now = time.time()
        dt = max(1e-3, now - last_t)
        inc = max(0, done - last_b)
        speed = inc / dt
        last_t = now
        last_b = done
        return _make_text(done, total, speed)

    if fast_upload is not None:
        input_file = await fast_upload(
            client,
            file_path,
            name=os.path.basename(file_path),
            reply=progress_msg,                     # <- enables live edits
            progress_bar_function=_progress_text,   # <- returns text for each update
        )
    else:
        # fallback (slower) – still functional if helper missing
        input_file = await client.upload_file(file=file_path, progress_callback=progress_cb)

    sent = await client.send_file(
        peer,
        input_file,
        caption=caption,
        thumb=thumb,
        attributes=attrs,
        force_document=False,
        reply_to=reply_to,
    )

    # remove progress message to keep chat clean
    try:
        await client.delete_messages(progress_msg.chat_id, [progress_msg.id])
    except Exception:
        pass

    return sent


async def fast_delete_message(api_id: int, api_hash: str, bot_token: str, msg):
    """
    Delete a Telethon message previously sent via fast_send_video using the user session.
    Signature keeps 'bot_token' for backward-compat; ignored internally.
    """
    client = await _get_client(api_id, api_hash, bot_token)
    try:
        await client.delete_messages(msg.chat_id, [msg.id])
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
        await client.delete_messages(msg.chat_id, [msg.id])
