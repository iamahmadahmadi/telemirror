import asyncio
import logging
import os
import time
from typing import Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError
# Additional error classes for robust error handling.  InvalidBufferError
# occurs when the server returns an unexpected buffer (often an HTTP error
# page) instead of MTProto data.  AuthKeyNotFound and BrokenAuthKeyError
# indicate that the current authorization key (session) is no longer valid.
try:
    from telethon.errors import InvalidBufferError
except Exception:
    InvalidBufferError = None  # type: ignore
try:
    # Telethon 1.28+ defines AuthKeyNotFound for invalid sessions; earlier
    # versions may not have it, so guard import.
    from telethon.errors import AuthKeyNotFound, BrokenAuthKeyError
except Exception:
    AuthKeyNotFound = BrokenAuthKeyError = None  # type: ignore
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


async def _reset_client():
    """Dispose of the cached client on fatal errors so the next attempt rebuilds it."""

    global _client
    if _client is None:
        return
    try:
        await _client.disconnect()
    except Exception:
        pass
    _client = None


async def _get_client(api_id: int, api_hash: str, bot_token: str) -> TelegramClient:
    """Return a connected, authorized Telethon client (bot session)."""

    global _client
    async with _client_lock:
        if _client and _client.is_connected():
            return _client

        _client = TelegramClient("fast_uploader", api_id, api_hash)
        await _client.connect()
        try:
            if not await _client.is_user_authorized():
                await _client.sign_in(bot_token=bot_token)
        except (AuthKeyNotFound, BrokenAuthKeyError):
            # Corrupted session, rebuild from scratch
            await _reset_client()
            _client = TelegramClient("fast_uploader", api_id, api_hash)
            await _client.connect()
            await _client.sign_in(bot_token=bot_token)
        return _client


async def _resolve_entity(client: TelegramClient, chat: EntityLike):
    """
    Resolve a chat given Bot API-style IDs (-100..) or usernames/links.
    Supports supergroups/channels, basic groups, users, usernames/links.
    """
    if isinstance(chat, int):
        s = str(chat)
        if s.startswith("-100"):      # supergroup/channel id (Bot API format)
            return types.PeerChannel(int(s[4:]))
        elif chat < 0:                 # basic group
            return types.PeerChat(-chat)
        return chat                    # positive user id
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


async def _upload_with_retry(
    client: TelegramClient,
    api_id: int,
    api_hash: str,
    bot_token: str,
    file_path: str,
    *,
    progress_cb=None,
    progress_msg=None,
    progress_text_fn=None,
):
    """
    Upload a file with retries, handling transient MTProto/network glitches and
    automatically rebuilding the client session on fatal auth issues. Falls back
    to native Telethon uploads if FastTelethonhelper is unavailable.
    """

    last_exc: Exception | None = None
    auth_errors = tuple(
        err for err in (InvalidBufferError, AuthKeyNotFound, BrokenAuthKeyError) if isinstance(err, type)
    )

    for attempt in range(1, 5):
        try:
            if fast_upload is not None and progress_msg is not None and progress_text_fn:
                return await fast_upload(
                    client,
                    file_path,
                    name=os.path.basename(file_path),
                    reply=progress_msg,
                    progress_bar_function=progress_text_fn,
                )
            return await client.upload_file(file=file_path, progress_callback=progress_cb)
        except Exception as exc:
            if isinstance(exc, FloodWaitError):
                await asyncio.sleep(exc.seconds)
                continue
            if auth_errors and isinstance(exc, auth_errors):
                last_exc = exc
                await _reset_client()
                client = await _get_client(api_id, api_hash, bot_token)
                continue

            last_exc = exc
            # brief exponential backoff to smooth over transient network issues
            await asyncio.sleep(min(30, 2 ** attempt))

    if last_exc:
        raise last_exc
    raise RuntimeError("Upload retry loop exited unexpectedly")


async def fast_send_video(
    api_id: int,
    api_hash: str,
    bot_token: str,
    chat: EntityLike,
    file_path: str,
    *,
    duration: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    thumb: Optional[str] = None,
    caption: Optional[str] = None,
    reply_to: Optional[int] = None,
    progress_cb=None,                   # (sent, total) -> None (for your internal counters)
    progress_title: Optional[str] = None,
    progress_gid: Optional[str] = None,
):
    """
    Fast upload with live progress using FastTelethonhelper. Creates a Telethon
    progress message under `reply_to`, updates it per chunk, sends the media,
    and returns the Telethon Message (you can BotAPI copy afterward).
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

    input_file = await _upload_with_retry(
        client,
        api_id,
        api_hash,
        bot_token,
        file_path,
        progress_cb=progress_cb,
        progress_msg=progress_msg,
        progress_text_fn=_progress_text,
    )

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
    """Delete a Telethon message previously sent via fast_send_video."""
    client = await _get_client(api_id, api_hash, bot_token)
    try:
        await client.delete_messages(msg.chat_id, [msg.id])
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
        await client.delete_messages(msg.chat_id, [msg.id])


async def fast_send_document(
    api_id: int,
    api_hash: str,
    bot_token: str,
    chat: EntityLike,
    file_path: str,
    *,
    caption: Optional[str] = None,
    thumb: Optional[str] = None,
    reply_to: Optional[int] = None,
    progress_cb=None,
    progress_title: Optional[str] = None,
    progress_gid: Optional[str] = None,
) -> types.Message:
    """
    Fast upload of a generic file (document) with live progress using
    FastTelethonhelper.  Similar to ``fast_send_video`` but without video
    attributes.  A temporary progress message is created under
    ``reply_to`` and updated per chunk.  Once the file is sent the progress
    message is deleted.  Returns the Telethon Message for further
    processing.

    :param api_id: Telegram API ID
    :param api_hash: Telegram API hash
    :param bot_token: Bot token for logging in
    :param chat: Target chat (integer ID or username/link)
    :param file_path: Path to the file to upload
    :param caption: Optional caption for the sent message
    :param thumb: Optional thumbnail path
    :param reply_to: Optional message ID to reply to
    :param progress_cb: Callback to update internal counters (sent, total)
    :param progress_title: Optional title for progress message
    :param progress_gid: Optional group ID for progress message
    :returns: The sent Telethon Message object
    """
    client = await _get_client(api_id, api_hash, bot_token)
    peer = await _resolve_entity(client, chat)

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

    def _progress_text(done: int, total: int) -> str:
        nonlocal last_t, last_b
        try:
            if progress_cb:
                progress_cb(done, total)
        except Exception:
            pass
        now = time.time()
        dt = max(1e-3, now - last_t)
        inc = max(0, done - last_b)
        speed = inc / dt
        last_t = now
        last_b = done
        return _make_text(done, total, speed)

    input_file = await _upload_with_retry(
        client,
        api_id,
        api_hash,
        bot_token,
        file_path,
        progress_cb=progress_cb,
        progress_msg=progress_msg,
        progress_text_fn=_progress_text,
    )

    sent = await client.send_file(
        peer,
        input_file,
        caption=caption,
        thumb=thumb,
        force_document=True,
        reply_to=reply_to,
    )

    # Remove progress message after sending
    try:
        await client.delete_messages(progress_msg.chat_id, [progress_msg.id])
    except Exception:
        pass

    return sent
