from PIL import Image
from aioshutil import rmtree
import asyncio
from asyncio import sleep
from logging import getLogger
from natsort import natsorted
from os import walk, path as ospath
import os
from time import time
from re import match as re_match, sub as re_sub
from typing import Callable, Awaitable, Optional

from pyrogram.errors import (
    FloodWait,
    RPCError,
    FloodPremiumWait,
    BadRequest,
    MediaEmpty,
    MediaInvalid,
    Forbidden,
    ChannelPrivate,
)

# Telethon-specific errors used when the fast upload fails.  If these
# classes are not available (older Telethon), they will alias to Exception
# so that the except clause still catches the error.  InvalidBufferError
# indicates that the server returned an unexpected buffer (possibly an HTTP
# error page) instead of MTProto data; AuthKeyNotFound and
# BrokenAuthKeyError mean that the authorization key (session) is invalid.
try:
    from telethon.errors import InvalidBufferError, AuthKeyNotFound, BrokenAuthKeyError  # type: ignore
except Exception:
    InvalidBufferError = AuthKeyNotFound = BrokenAuthKeyError = Exception  # type: ignore
from aiofiles.os import remove, path as aiopath
from pyrogram.types import Message
from tenacity import (
    retry,
    wait_exponential,
    stop_after_attempt,
    retry_if_exception_type,
    RetryError,
)

from ...core.config_manager import Config
from ...modules.telegram_fast_upload import fast_send_video, fast_send_document, fast_delete_message

_DEFAULT_FORUM_CHAT_ID = -1002154451354
FORUM_CHAT_ID = int(
    os.getenv("FORUM_CHAT_ID", "")
    or getattr(Config, "FORUM_CHAT_ID", _DEFAULT_FORUM_CHAT_ID)
    or _DEFAULT_FORUM_CHAT_ID
)

def _pick_cfg_env(keys: list[str], cast, default):
    for k in keys:
        v = os.getenv(k)
        if v not in (None, "", "None"):
            try:
                return cast(v)
            except Exception:
                pass
    for k in keys:
        if hasattr(Config, k):
            v = getattr(Config, k, None)
            if v not in (None, "", "None"):
                try:
                    return cast(v)
                except Exception:
                    pass
    return default

API_ID = _pick_cfg_env(["TELEGRAM_API", "API_ID", "TG_API_ID", "TELEGRAM_API_ID"], int, 0)
API_HASH = _pick_cfg_env(["TELEGRAM_HASH", "API_HASH", "TG_API_HASH", "TELEGRAM_API_HASH"], str, "")
BOT_TOKEN = _pick_cfg_env(["TELEGRAM_BOT_TOKEN", "BOT_TOKEN", "TG_BOT_TOKEN"], str, "")

from ...core.mltb_client import TgClient
from ..ext_utils.bot_utils import sync_to_async
from ..ext_utils.files_utils import is_archive, get_base_name
from ..telegram_helper.message_utils import delete_message
from ..ext_utils.media_utils import (
    get_media_info,
    get_document_type,
    get_video_thumbnail,
    get_audio_thumbnail,
    get_multiple_frames_thumbnail,
)
from ..ext_utils.ffmpeg_utils import ensure_music_audio, ensure_voice_ogg
from ...db.sqlite_db import (
    get_user,
    set_user_thread,
    add_usage_mb,
    upsert_file,
)

LOGGER = getLogger(__name__)
try:
    import tgcrypto  # noqa
    LOGGER.info("tgcrypto loaded: MTProto crypto acceleration ENABLED")
except Exception:
    LOGGER.warning("tgcrypto NOT found – pip install -U tgcrypto for better performance")

SEND_TO_FORUM_TOO = True


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

def _extract_file_id(msg):
    if not msg:
        return None
    for attr in ("document", "video", "audio", "photo", "animation"):
        media = getattr(msg, attr, None)
        if media:
            if attr == "photo" and isinstance(media, (list, tuple)) and media:
                return media[-1].file_id
            return getattr(media, "file_id", None)
    return None

def _extract_thread_id(obj) -> int:
    try:
        mtid = getattr(obj, "message_thread_id", None)
        if mtid:
            return int(mtid)
        ftc = getattr(obj, "forum_topic_created", None)
        if ftc:
            fid = getattr(ftc, "id", None)
            if fid:
                return int(fid)
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
                fid2 = getattr(ftc2, "id", None)
                if fid2:
                    return int(fid2)
    except Exception:
        pass
    return 0

async def send_audio_smart(
    client,
    chat_id: int,
    path: str,
    *,
    title: str | None = None,
    performer: str | None = None,
    thumb: str | None = None,
    prefer_voice: bool = False,
    prefer_codec: str = "mp3",
    reply_to_message_id: int | None = None,
):
    try:
        return await client.send_audio(
            chat_id,
            audio=path,
            caption=title or "",
            title=title or None,
            performer=performer or None,
            thumb=thumb,
            reply_to_message_id=reply_to_message_id,
        )
    except (MediaEmpty, MediaInvalid, RPCError):
        pass

    if prefer_voice:
        try:
            vpath, vdur = await ensure_voice_ogg(path)
            return await client.send_voice(
                chat_id,
                voice=vpath,
                caption=title or "",
                duration=vdur or 0,
                reply_to_message_id=reply_to_message_id,
            )
        except Exception:
            pass

    try:
        apath, dur = await ensure_music_audio(path, prefer=prefer_codec)
        return await client.send_audio(
            chat_id,
            audio=apath,
            caption=title or "",
            title=title or None,
            performer=performer or None,
            duration=dur or 0,
            thumb=thumb,
            reply_to_message_id=reply_to_message_id,
        )
    except Exception:
        return await client.send_document(
            chat_id,
            document=path,
            caption=title or "",
            reply_to_message_id=reply_to_message_id,
        )

def _sig(caption: str | None) -> str:
    caption = caption or ""
    try:
        sig = getattr(Config, "BOT_SIGNATURE", None)
        uname = getattr(Config, "BOT_USERNAME", None)
    except Exception:
        sig = None
        uname = None
    if sig and uname:
        tag = f"@{uname}"
        if tag not in caption:
            caption = (caption + "\n\n" if caption else "") + f"{sig} {tag}"
    return caption


class TelegramUploader:
    """
    Videos → FastTelethonhelper (fast + live progress). Bot copies message after upload.
    Docs/Audio/Photo → Pyrogram flows as before.
    """
    def __init__(self, listener=None):
        self._listener = listener
        self._path = ""
        self._start_time = 0.0
        self._total_files = 0
        self._thumb = ""
        self._msgs_dict = {}
        self._corrupted = 0
        self._is_corrupted = False
        self._media_dict = {"videos": {}, "documents": {}}
        self._last_msg_in_group = False
        self._up_path = ""
        self._lprefix = ""
        self._media_group = False
        self._is_private = False
        self._sent_msg: Optional[Message] = None
        self._user_session = False
        self._error = ""
        self.processed_bytes = 0
        self.speed = 0
        self._forum_chat_id = FORUM_CHAT_ID
        self._thread_id = 0

        # per-file progress baselines (for internal rate calc)
        self._last_tick_time = 0.0
        self._last_bytes = 0

    # ---------- Sender helper with retries ----------

    async def _safe_send(self, maker: Callable[[], Awaitable], what: str = "media"):
        backoffs = [3, 5, 8, 13, 21]
        last_exc = None
        for delay in [0] + backoffs:
            if delay:
                await sleep(delay)
            try:
                return await maker()
            except FloodWait as e:
                wait_for = int(getattr(e, "value", 0)) or int(getattr(e, "x", 0)) or 30
                await sleep(wait_for + 1)
            except TimeoutError as e:
                last_exc = e
            except RPCError as e:
                msg = str(e).upper()
                transient = any(k in msg for k in (
                    "TIMEOUT", "NETWORK", "TRY AGAIN", "MSG_WAIT_TIMEOUT",
                    "DC_ID_INVALID", "FLOOD", "TEMPORARY", "INTERNAL"
                ))
                if transient:
                    last_exc = e
                else:
                    raise
            except Exception as e:
                last_exc = e
        raise last_exc or TimeoutError(f"Failed to send {what}: retries exhausted")

    # ---------- Forum topic mirroring ----------

    async def _ensure_user_topic(self) -> None:
        if not self._forum_chat_id:
            return
        uid = getattr(self._listener, "user_id", 0) or (
            getattr(getattr(self._listener, "message", None), "from_user", None)
            and self._listener.message.from_user.id
        ) or 0
        if not uid:
            return
        try:
            u = await get_user(uid)
        except Exception:
            u = {"thread": 0}
        self._thread_id = int(u.get("thread") or 0)
        if self._thread_id:
            return
        try:
            topic = await TgClient.bot.create_forum_topic(self._forum_chat_id, str(uid))
            self._thread_id = _extract_thread_id(topic)
            if self._thread_id > 0:
                await set_user_thread(uid, self._thread_id)
        except Exception:
            self._thread_id = 0

    # ---------- Minor helpers ----------

    async def _prepare_file(self, file_, dirpath):
        if self._lprefix:
            cap_mono = f"{self._lprefix} <code>{file_}</code>"
            self._lprefix = re_sub("<.*?>", "", self._lprefix)
        else:
            cap_mono = f"<code>{file_}</code>"
        return cap_mono

    async def _upload_progress(self, current, total):
        """Internal counters (FastTelethonhelper updates the user-visible message itself)."""
        try:
            now = time()
            prev_t = self._last_tick_time or self._start_time or now
            prev_b = int(self._last_bytes or 0)
            cur_b  = int(current)
            elapsed = max(1e-3, now - prev_t)
            delta   = max(0, cur_b - prev_b)
            self.speed = int(delta / elapsed)
            self.processed_bytes = cur_b
            self._last_bytes = cur_b
            self._last_tick_time = now
        except Exception:
            pass

    async def _mirror_last_sent_to_user_topic(self):
        if not (SEND_TO_FORUM_TOO and self._forum_chat_id and self._thread_id and self._sent_msg):
            return
        try:
            await self._safe_send(
                lambda: TgClient.bot.copy_message(
                    chat_id=self._forum_chat_id,
                    from_chat_id=self._sent_msg.chat.id,
                    message_id=self._sent_msg.id,
                    message_thread_id=self._thread_id,
                    disable_notification=True,
                ),
                what="copy_to_topic",
            )
        except Exception:
            pass

    async def _user_settings(self):
        return

    async def _msg_to_reply(self):
        try:
            if self._listener and getattr(self._listener, "message", None):
                if self._sent_msg is None:
                    self._sent_msg = self._listener.message
                return True
        except Exception:
            pass
        return False

    # ---------- Main entry ----------

    async def upload(self, listener, path):
        self._listener = listener
        self._path = path
        self._start_time = time()
        self._total_files = 0
        self._thumb = self._listener.thumb or f"thumbnails/{listener.user_id}.jpg"
        self._msgs_dict = {}
        self._corrupted = 0
        self._is_corrupted = False
        self._media_dict = {"videos": {}, "documents": {}}
        self._last_msg_in_group = False
        self._up_path = ""
        self._lprefix = ""
        self._media_group = False
        self._is_private = False
        self._sent_msg = None
        self._user_session = False
        self._error = ""
        self._last_tick_time = 0.0
        self._last_bytes = 0
        self.processed_bytes = 0
        self.speed = 0

        await self._user_settings()
        if not await self._msg_to_reply():
            return
        await self._ensure_user_topic()

        for dirpath, _, files in natsorted(await sync_to_async(walk, self._path)):
            if dirpath.strip().endswith("/yt-dlp-thumb"):
                continue

            for file_ in natsorted(files):
                self._error = ""
                self._up_path = f_path = ospath.join(dirpath, file_)
                if not await aiopath.exists(self._up_path):
                    continue

                try:
                    f_size = await aiopath.getsize(self._up_path)
                    self._total_files += 1
                    if f_size == 0:
                        self._corrupted += 1
                        continue
                    if self._listener.is_cancelled:
                        return

                    cap_mono = await self._prepare_file(file_, dirpath)

                    # reset per-file counters
                    self._last_uploaded = 0
                    self._start_time = time()
                    self._last_tick_time = self._start_time
                    self._last_bytes = 0
                    self.processed_bytes = 0

                    await self._upload_file(cap_mono, file_, f_path)

                    if self._listener.is_cancelled:
                        return

                    if (
                        not self._is_corrupted
                        and (self._listener.is_super_chat or self._listener.up_dest)
                        and not self._is_private
                        and self._sent_msg
                    ):
                        self._msgs_dict[self._sent_msg.link] = file_

                    await sleep(1)

                except Exception as err:
                    if isinstance(err, RetryError):
                        err = err.last_attempt.exception()
                    self._error = str(err)
                    self._corrupted += 1
                    if self._listener.is_cancelled:
                        return

                if not self._listener.is_cancelled and await aiopath.exists(self._up_path):
                    await remove(self._up_path)

        if self._listener.is_cancelled:
            return
        if self._total_files == 0:
            await self._listener.on_upload_error(
                "No files to upload. If you filled EXCLUDED_EXTENSIONS, check if all files were excluded."
            )
            return
        if self._total_files <= self._corrupted:
            await self._listener.on_upload_error(f"Files corrupted or unable to upload. {self._error or 'Check logs!'}")
            return

        await self._listener.on_upload_complete(None, self._msgs_dict, self._total_files, self._corrupted)

    # ---------- Per-file upload ----------

    @retry(
        wait=wait_exponential(multiplier=2, min=4, max=8),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(Exception),
    )
    async def _upload_file(self, cap_mono, file, o_path, force_document: bool = False):
        if self._thumb is not None and not await aiopath.exists(self._thumb) and self._thumb != "none":
            self._thumb = None

        thumb = self._thumb
        self._is_corrupted = False

        try:
            is_video, is_audio, is_image = await get_document_type(self._up_path)

            if not is_image and thumb is None:
                file_name = ospath.splitext(file)[0]
                thumb_path = f"{self._path}/yt-dlp-thumb/{file_name}.jpg"
                if await aiopath.isfile(thumb_path):
                    thumb = thumb_path
                elif is_audio and not is_video:
                    thumb = await get_audio_thumbnail(self._up_path)

            sender = TgClient.user if self._user_session else self._listener.client
            anchor = self._sent_msg or self._listener.message

            # Video → FastTelethonhelper with live progress
            if is_video and not (self._listener.as_doc or force_document):
                duration = (await get_media_info(self._up_path))[0]

                if thumb is None and getattr(self._listener, "thumbnail_layout", None):
                    thumb = await get_multiple_frames_thumbnail(
                        self._up_path,
                        self._listener.thumbnail_layout,
                        getattr(self._listener, "screen_shots", 0),
                    )
                if thumb is None:
                    thumb = await get_video_thumbnail(self._up_path, duration)

                if thumb is not None and thumb != "none":
                    try:
                        with Image.open(thumb) as img:
                            width, height = img.size
                    except Exception:
                        width, height = 480, 320
                else:
                    width, height = 480, 320

                if self._listener.is_cancelled:
                    return
                if thumb == "none":
                    thumb = None

                gid = getattr(self._listener, "gid", None) or getattr(self._listener, "gid_hash", None) or "-"

                # FAST upload (helper edits progress message), then send, then BotAPI copy.  If the
                # fast upload fails with a Telethon InvalidBufferError or a broken auth key, fall back
                # to uploading via the Bot API directly.  This avoids the "Invalid response buffer"
                # errors and ensures the file is delivered albeit slower.
                try:
                    tl_msg = await fast_send_video(
                        api_id=API_ID,
                        api_hash=API_HASH,
                        bot_token=BOT_TOKEN,
                        chat=anchor.chat.id,
                        file_path=self._up_path,
                        duration=duration,
                        width=width,
                        height=height,
                        thumb=thumb,
                        caption=_sig(cap_mono),
                        reply_to=anchor.id,
                        progress_cb=self._upload_progress,
                        progress_title=f"1.Upload: {file}",
                        progress_gid=str(gid),
                    )
                    # Copy the uploaded video back to the chat using the bot so that the
                    # message appears to originate from the bot rather than the user session.
                    self._sent_msg = await self._safe_send(
                        lambda: TgClient.bot.copy_message(
                            chat_id=anchor.chat.id,
                            from_chat_id=anchor.chat.id,
                            message_id=tl_msg.id,
                            reply_to_message_id=anchor.id,
                            disable_notification=True,
                        ),
                        what="video",
                    )
                    # delete the Telethon original to keep chat tidy
                    try:
                        await fast_delete_message(API_ID, API_HASH, BOT_TOKEN, tl_msg)
                    except Exception:
                        pass
                except (InvalidBufferError, AuthKeyNotFound, BrokenAuthKeyError) as e:
                    LOGGER.warning(
                        "Fast Telethon video upload failed due to %s; falling back to Pyrogram send_video",
                        e,
                    )
                    # fallback: upload via the Bot API directly
                    self._sent_msg = await self._safe_send(
                        lambda: TgClient.bot.send_video(
                            chat_id=anchor.chat.id,
                            video=self._up_path,
                            caption=_sig(cap_mono),
                            duration=duration or 0,
                            width=width or 0,
                            height=height or 0,
                            thumb=thumb,
                            reply_to_message_id=anchor.id,
                            supports_streaming=True,
                        ),
                        what="video",
                    )
                # Common post-upload tasks (both fast and fallback)
                vid_id = (
                    getattr(getattr(self._sent_msg, "video", None), "file_id", None)
                    if self._sent_msg
                    else None
                )
                await self._post_send_usage_and_db(vid_id, thumb)
                await self._mirror_last_sent_to_user_topic()
                return

            # Document (or forced)
            if self._listener.as_doc or force_document or (not is_video and not is_audio and not is_image):
                # For videos treated as documents we may still extract a thumbnail
                if is_video and thumb is None:
                    thumb = await get_video_thumbnail(self._up_path, None)
                if self._listener.is_cancelled:
                    return
                if thumb == "none":
                    thumb = None

                # Use the fast Telethon uploader for documents.  Telethon sends
                # the file via the user session, after which we copy the
                # message back to the chat using the Bot API.  This approach
                # eliminates the performance overhead of Pyrogram's upload.
                gid = (
                    getattr(self._listener, "gid", None)
                    or getattr(self._listener, "gid_hash", None)
                    or "-"
                )

                # Upload the file via Telethon with progress.  The returned
                # message belongs to the user session; we will copy it via Bot API below.  If
                # the fast upload fails with an InvalidBufferError or broken auth key,
                # fall back to sending via the Bot API directly.
                try:
                    tl_msg = await fast_send_document(
                        api_id=API_ID,
                        api_hash=API_HASH,
                        bot_token=BOT_TOKEN,
                        chat=anchor.chat.id,
                        file_path=self._up_path,
                        caption=_sig(cap_mono),
                        thumb=thumb,
                        reply_to=anchor.id,
                        progress_cb=self._upload_progress,
                        progress_title=f"1.Upload: {file}",
                        progress_gid=str(gid),
                    )
                    # Copy the uploaded message back to the chat using the bot.  This
                    # ensures the file appears to come from the bot rather than the
                    # user session, preserving anonymity and allowing further
                    # operations such as deletion.
                    self._sent_msg = await self._safe_send(
                        lambda: TgClient.bot.copy_message(
                            chat_id=anchor.chat.id,
                            from_chat_id=anchor.chat.id,
                            message_id=tl_msg.id,
                            reply_to_message_id=anchor.id,
                            disable_notification=True,
                        ),
                        what="document",
                    )
                    # Clean up the Telethon original message
                    try:
                        await fast_delete_message(API_ID, API_HASH, BOT_TOKEN, tl_msg)
                    except Exception:
                        pass
                except (InvalidBufferError, AuthKeyNotFound, BrokenAuthKeyError) as e:
                    LOGGER.warning(
                        "Fast Telethon document upload failed due to %s; falling back to Pyrogram send_document",
                        e,
                    )
                    # fallback: upload via the Bot API directly
                    self._sent_msg = await self._safe_send(
                        lambda: TgClient.bot.send_document(
                            chat_id=anchor.chat.id,
                            document=self._up_path,
                            caption=_sig(cap_mono),
                            thumb=thumb,
                            reply_to_message_id=anchor.id,
                            disable_notification=True,
                        ),
                        what="document",
                    )
                # Persist usage and mirror to user topic
                doc_id = (
                    getattr(getattr(self._sent_msg, "document", None), "file_id", None)
                    if self._sent_msg
                    else None
                )
                await self._post_send_usage_and_db(doc_id, thumb)
                await self._mirror_last_sent_to_user_topic()
                return

            # Audio
            if is_audio:
                if self._listener.is_cancelled:
                    return
                if thumb == "none":
                    thumb = None

                duration, artist, title = await get_media_info(self._up_path)
                self._sent_msg = await self._safe_send(
                    lambda: send_audio_smart(
                        sender,
                        anchor.chat.id,
                        self._up_path,
                        title=_sig(cap_mono),
                        performer=artist,
                        thumb=thumb,
                        prefer_voice=False,
                        prefer_codec="mp3",
                        reply_to_message_id=anchor.id,
                    ),
                    what="audio",
                )
                sent_fid = (
                    getattr(getattr(self._sent_msg, "audio", None), "file_id", None)
                    or getattr(getattr(self._sent_msg, "document", None), "file_id", None)
                    if self._sent_msg else None
                )
                await self._post_send_usage_and_db(sent_fid, thumb)
                await self._mirror_last_sent_to_user_topic()
                return

            # Photo
            if is_image:
                if self._listener.is_cancelled:
                    return
                self._sent_msg = await self._safe_send(
                    lambda: sender.send_photo(
                        anchor.chat.id,
                        photo=self._up_path,
                        caption=_sig(cap_mono),
                        disable_notification=True,
                        reply_to_message_id=anchor.id,
                    ),
                    what="photo",
                )
                photo_id = None
                if self._sent_msg and getattr(self._sent_msg, "photo", None):
                    try:
                        photo_id = self._sent_msg.photo[-1].file_id
                    except Exception:
                        pass
                await self._post_send_usage_and_db(photo_id, None)
                await self._mirror_last_sent_to_user_topic()
                return

            # Fallback → document
            if self._listener.is_cancelled:
                return
            self._sent_msg = await self._safe_send(
                lambda: sender.send_document(
                    anchor.chat.id,
                    document=self._up_path,
                    caption=_sig(cap_mono),
                    disable_notification=True,
                    reply_to_message_id=anchor.id,
                    progress=self._upload_progress,
                ),
                what="document",
            )
            doc_id = getattr(getattr(self._sent_msg, "document", None), "file_id", None) if self._sent_msg else None
            await self._post_send_usage_and_db(doc_id, None)
            await self._mirror_last_sent_to_user_topic()

        except (FloodWait, FloodPremiumWait) as f:
            await sleep(int(getattr(f, "value", 5)) * 1.3)
            return await self._upload_file(cap_mono, file, o_path, force_document)

        except (BadRequest, RPCError, MediaEmpty, MediaInvalid):
            return await self._upload_file(cap_mono, file, o_path, True)

        except Exception as err:
            raise

    async def _post_send_usage_and_db(self, file_id: str | None, thumb_path: str | None):
        try:
            size_bytes = await aiopath.getsize(self._up_path)
            used_mb = max(1, (size_bytes + (1024 * 1024 - 1)) // (1024 * 1024))
            await add_usage_mb(self._listener.user_id, used_mb)
        except Exception:
            pass

        if file_id:
            try:
                name = ospath.basename(self._up_path)
                leecher_id = self._listener.user_id
                nsfw = bool(getattr(self._listener, "nsfw", False))
                qual = str(
                    getattr(self._listener, "yt_quality", None)
                    or getattr(self._listener, "qual", None)
                    or ""
                )
                source = getattr(self._listener, "link", None)
                thumb_to_store = None
                if thumb_path and thumb_path != "none" and await aiopath.exists(thumb_path):
                    thumb_to_store = thumb_path

                await upsert_file(
                    file_id=file_id,
                    name=name,
                    leecher_id=leecher_id,
                    nsfw=nsfw,
                    thumbnail=thumb_to_store,
                    quality=qual,
                    source=source,
                )
            except Exception:
                pass


__all__ = ["TelegramUploader", "send_audio_smart"]