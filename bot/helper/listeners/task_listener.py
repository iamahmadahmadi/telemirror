import asyncio
import inspect
from aiofiles.os import path as aiopath, listdir, remove
from asyncio import sleep, gather
from html import escape
from requests import utils as rutils

from ... import (
    intervals,
    task_dict,
    task_dict_lock,
    LOGGER,
    non_queued_up,
    non_queued_dl,
    queued_up,
    queued_dl,
    queue_dict_lock,
    same_directory_lock,
    DOWNLOAD_DIR,
)
from ...core.config_manager import Config
from ...core.torrent_manager import TorrentManager
from ..common import TaskConfig
from ..ext_utils.bot_utils import sync_to_async
from ..ext_utils.db_handler import database
from ..ext_utils.files_utils import (
    get_path_size,
    clean_download,
    clean_target,
    join_files,
    create_recursive_symlink,
    remove_excluded_files,
    move_and_merge,
)
from ..ext_utils.links_utils import is_gdrive_id
from ..ext_utils.status_utils import get_readable_file_size
from ..ext_utils.task_manager import start_from_queued, check_running_tasks
from ..mirror_leech_utils.gdrive_utils.upload import GoogleDriveUpload
from ..mirror_leech_utils.rclone_utils.transfer import RcloneTransferHelper
from ..mirror_leech_utils.status_utils.gdrive_status import GoogleDriveStatus
from ..mirror_leech_utils.status_utils.queue_status import QueueStatus
from ..mirror_leech_utils.status_utils.rclone_status import RcloneStatus
from ..mirror_leech_utils.status_utils.telegram_status import TelegramStatus
from ..mirror_leech_utils.telegram_uploader import TelegramUploader
from ..telegram_helper.button_build import ButtonMaker
from ..telegram_helper.message_utils import (
    send_message,
    delete_status,
    update_status_message,
)
# usage accounting
from ...db.sqlite_db import add_usage_mb as add_usage
from ...core.mltb_client import TgClient

# Forum error routing
ERROR_THREAD_ID = 127
ERROR_FORUM_CHAT_ID = -1002154451354


async def _call_tg_upload(tg, listener, up_dir):
    """
    Robustly call the TelegramUploader's upload entrypoint.
    Tries common method names, supports (listener, up_dir) and (up_dir) signatures,
    and awaits if the result is awaitable.
    """
    candidates = [
        "upload",
        "start_upload",
        "upload_path",
        "upload_folder",
        "send",
        "run",
        "start",
        "execute",
    ]
    for name in candidates:
        method = getattr(tg, name, None)
        if callable(method):
            try:
                result = method(listener, up_dir)
            except TypeError:
                # Fallback to single-arg signatures
                result = method(up_dir)
            if inspect.isawaitable(result):
                return await result
            return result
    public = [a for a in dir(tg) if not a.startswith("_")]
    raise AttributeError(
        f"TelegramUploader has no supported upload method. "
        f"Tried {candidates}. Available: {public}"
    )


class TaskListener(TaskConfig):
    def __init__(self):
        super().__init__()

    async def clean(self):
        try:
            if st := intervals["status"]:
                for intvl in list(st.values()):
                    intvl.cancel()
            intervals["status"].clear()
            await gather(TorrentManager.aria2.purgeDownloadResult(), delete_status())
        except Exception:
            pass

    def clear(self):
        self.subname = ""
        self.subsize = 0
        self.files_to_proceed = []
        self.proceed_count = 0
        self.progress = True

    async def remove_from_same_dir(self):
        async with task_dict_lock:
            if (
                self.folder_name
                and self.same_dir
                and self.mid in self.same_dir[self.folder_name]["tasks"]
            ):
                self.same_dir[self.folder_name]["tasks"].remove(self.mid)
                self.same_dir[self.folder_name]["total"] -= 1

    async def on_download_start(self):
        if (
            self.is_super_chat
            and Config.INCOMPLETE_TASK_NOTIFIER
            and Config.DATABASE_URL
        ):
            await database.add_incomplete_task(
                self.message.chat.id, self.message.link, self.tag
            )

    async def on_download_complete(self):
        await sleep(2)
        if self.is_cancelled:
            return
        multi_links = False
        if (
            self.folder_name
            and self.same_dir
            and self.mid in self.same_dir[self.folder_name]["tasks"]
        ):
            async with same_directory_lock:
                while True:
                    async with task_dict_lock:
                        if self.mid not in self.same_dir[self.folder_name]["tasks"]:
                            return
                        if (
                            self.same_dir[self.folder_name]["total"] <= 1
                            or len(self.same_dir[self.folder_name]["tasks"]) > 1
                        ):
                            if self.same_dir[self.folder_name]["total"] > 1:
                                self.same_dir[self.folder_name]["tasks"].remove(
                                    self.mid
                                )
                                self.same_dir[self.folder_name]["total"] -= 1
                                spath = f"{self.dir}{self.folder_name}"
                                des_id = list(self.same_dir[self.folder_name]["tasks"])[
                                    0
                                ]
                                des_path = f"{DOWNLOAD_DIR}{des_id}{self.folder_name}"
                                LOGGER.info(f"Moving files from {self.mid} to {des_id}")
                                await move_and_merge(spath, des_path, self.mid)
                                multi_links = True
                            break
                    await sleep(1)
        async with task_dict_lock:
            if self.is_cancelled:
                return
            if self.mid not in task_dict:
                return
            download = task_dict[self.mid]
            self.name = download.name()
            gid = download.gid()
        LOGGER.info(f"Download completed: {self.name}")

        if not (self.is_torrent or self.is_qbit):
            self.seed = False

        if multi_links:
            self.seed = False
            await self.on_upload_error(
                f"{self.name} Downloaded!\n\nWaiting for other tasks to finish..."
            )
            return
        elif self.same_dir:
            self.seed = False

        if self.folder_name:
            self.name = self.folder_name.strip("/").split("/", 1)[0]

        if not await aiopath.exists(f"{self.dir}/{self.name}"):
            try:
                files = await listdir(self.dir)
                self.name = files[-1]
                if self.name == "yt-dlp-thumb":
                    self.name = files[0]
            except Exception as e:
                await self.on_upload_error(str(e))
                return

        dl_path = f"{self.dir}/{self.name}"
        self.size = await get_path_size(dl_path)
        self.is_file = await aiopath.isfile(dl_path)

        if self.seed:
            self.up_dir = f"{self.dir}10000"
            up_dir = self.up_dir
            up_path = f"{self.up_dir}/{self.name}"
            await create_recursive_symlink(self.dir, self.up_dir)
            LOGGER.info(f"Shortcut created: {dl_path} -> {up_path}")
        else:
            up_dir = self.dir
            up_path = dl_path

        await remove_excluded_files(self.up_dir or self.dir, self.excluded_extensions)

        if not Config.QUEUE_ALL:
            async with queue_dict_lock:
                if self.mid in non_queued_dl:
                    non_queued_dl.remove(self.mid)
            await start_from_queued()

        if self.join and not self.is_file:
            await join_files(up_path)

        if self.extract and not self.is_nzb:
            up_path = await self.proceed_extract(up_path, gid)
            if self.is_cancelled:
                return
            self.is_file = await aiopath.isfile(up_path)
            self.name = up_path.replace(f"{up_dir}/", "").split("/", 1)[0]
            self.size = await get_path_size(up_dir)
            self.clear()
            await remove_excluded_files(up_dir, self.excluded_extensions)

        if self.ffmpeg_cmds:
            up_path = await self.proceed_ffmpeg(up_path, gid)
            if self.is_cancelled:
                return
            self.is_file = await aiopath.isfile(up_path)
            self.name = up_path.replace(f"{up_dir}/", "").split("/", 1)[0]
            self.size = await get_path_size(up_dir)
            self.clear()

        if self.name_sub:
            up_path = await self.substitute(up_path)
            if self.is_cancelled:
                return
            self.is_file = await aiopath.isfile(up_path)
            self.name = up_path.replace(f"{up_dir}/", "").split("/", 1)[0]

        if self.screen_shots:
            up_path = await self.generate_screenshots(up_path)
            if self.is_cancelled:
                return
            self.is_file = await aiopath.isfile(up_path)
            self.name = up_path.replace(f"{up_dir}/", "").split("/", 1)[0]
            self.size = await get_path_size(up_dir)

        if self.convert_audio or self.convert_video:
            up_path = await self.convert_media(up_path, gid)
            if self.is_cancelled:
                return
            self.is_file = await aiopath.isfile(up_path)
            self.name = up_path.replace(f"{up_dir}/", "").split("/", 1)[0]
            self.size = await get_path_size(up_dir)
            self.clear()

        if self.sample_video:
            up_path = await self.generate_sample_video(up_path, gid)
            if self.is_cancelled:
                return
            self.is_file = await aiopath.isfile(up_path)
            self.name = up_path.replace(f"{up_dir}/", "").split("/", 1)[0]
            self.size = await get_path_size(up_dir)
            self.clear()

        if self.compress:
            up_path = await self.proceed_compress(up_path, gid)
            self.is_file = await aiopath.isfile(up_path)
            if self.is_cancelled:
                return
            self.clear()

        self.name = up_path.replace(f"{up_dir}/", "").split("/", 1)[0]
        self.size = await get_path_size(up_dir)

        if self.is_leech and not self.compress:
            await self.proceed_split(up_path, gid)
            if self.is_cancelled:
                return
            self.clear()

        self.subproc = None

        add_to_queue, event = await check_running_tasks(self, "up")
        await start_from_queued()
        if add_to_queue:
            LOGGER.info(f"Added to Queue/Upload: {self.name}")
            async with task_dict_lock:
                task_dict[self.mid] = QueueStatus(self, gid, "Up")
            await event.wait()
            if self.is_cancelled:
                return
            LOGGER.info(f"Start from Queued/Upload: {self.name}")

        self.size = await get_path_size(up_dir)

        if self.is_leech:
            LOGGER.info(f"Leech Name: {self.name}")
            tg = TelegramUploader()
            async with task_dict_lock:
                task_dict[self.mid] = TelegramStatus(self, tg, gid, "up")
            try:
                upload_coro = _call_tg_upload(tg, self, up_dir)
            except Exception as e:
                LOGGER.exception("TelegramUploader adapter failed for %s: %s", self.name, e)
                await update_status_message(self.message.chat.id)
            else:
                results = await asyncio.gather(
                    update_status_message(self.message.chat.id),
                    upload_coro,
                    return_exceptions=True,
                )
                for r in results:
                    if isinstance(r, Exception):
                        LOGGER.exception("Upload pipeline error for %s: %s", self.name, r)
            del tg

        elif is_gdrive_id(self.up_dest):
            LOGGER.info(f"Gdrive Upload Name: {self.name}")
            drive = GoogleDriveUpload(self, up_path)
            async with task_dict_lock:
                task_dict[self.mid] = GoogleDriveStatus(self, drive, gid, "up")
            try:
                await gather(
                    update_status_message(self.message.chat.id),
                    sync_to_async(drive.upload),
                )
            except Exception as e:
                LOGGER.exception("GDrive upload failed for %s: %s", self.name, e)
            del drive
        else:
            LOGGER.info(f"Rclone Upload Name: {self.name}")
            RCTransfer = RcloneTransferHelper(self)
            async with task_dict_lock:
                task_dict[self.mid] = RcloneStatus(self, RCTransfer, gid, "up")
            try:
                await gather(
                    update_status_message(self.message.chat.id),
                    RCTransfer.upload(up_path),
                )
            except Exception as e:
                LOGGER.exception("Rclone upload failed for %s: %s", self.name, e)
            del RCTransfer
        return

    async def on_upload_complete(
        self, link, files, folders, mime_type, rclone_path="", dir_id=""
    ):
        if (
            self.is_super_chat
            and Config.INCOMPLETE_TASK_NOTIFIER
            and Config.DATABASE_URL
        ):
            await database.rm_complete_task(self.message.link)
        msg = f"<b>Name: </b><code>{escape(self.name)}</code>\n\n<b>Size: </b>{get_readable_file_size(self.size)}"
        LOGGER.info(f"Task Done: {self.name}")
        if self.is_leech:
            msg += f"\n<b>Total Files: </b>{folders}"
            if mime_type != 0:
                msg += f"\n<b>Corrupted Files: </b>{mime_type}"
            msg += f"\nSaved by @Leechflixbot\n\n"
            if not files:
                await send_message(self.message, msg)
            else:
                fmsg = ""
                for index, (link, name) in enumerate(files.items(), start=1):
                    fmsg += f"{index}. <a href='{link}'>{name}</a>\n"
                    if len(fmsg.encode() + msg.encode()) > 4000:
                        await send_message(self.message, msg + fmsg)
                        await sleep(1)
                        fmsg = ""
                if fmsg != "":
                    await send_message(self.message, msg + fmsg)
        else:
            msg += f"\n\n<b>Type: </b>{mime_type}"
            if mime_type == "Folder":
                msg += f"\n<b>SubFolders: </b>{folders}"
                msg += f"\n<b>Files: </b>{files}"
            if (
                link
                or rclone_path
                and Config.RCLONE_SERVE_URL
                and not self.private_link
            ):
                buttons = ButtonMaker()
                if link:
                    buttons.url_button("☁️ Cloud Link", link)
                else:
                    msg += f"\n\nPath: <code>{rclone_path}</code>"
                if rclone_path and Config.RCLONE_SERVE_URL and not self.private_link:
                    remote, rpath = rclone_path.split(":", 1)
                    url_path = rutils.quote(f"{rpath}")
                    share_url = f"{Config.RCLONE_SERVE_URL}/{remote}/{url_path}"
                    if mime_type == "Folder":
                        share_url += "/"
                    buttons.url_button("🔗 Rclone Link", share_url)
                if not rclone_path and dir_id:
                    INDEX_URL = ""
                    if self.private_link:
                        INDEX_URL = self.user_dict.get("INDEX_URL", "") or ""
                    elif Config.INDEX_URL:
                        INDEX_URL = Config.INDEX_URL
                    if INDEX_URL:
                        share_url = f"{INDEX_URL}/findpath?id={dir_id}"
                        buttons.url_button("⚡ Index Link", share_url)
                        if mime_type.startswith(("image", "video", "audio")):
                            share_urls = f"{INDEX_URL}/findpath?id={dir_id}&view=true"
                            buttons.url_button("🌐 View Link", share_urls)
                button = buttons.build_menu(2)
            else:
                msg += f"\n\nPath: <code>{rclone_path}</code>"
                button = None
            msg += f"\n\n<b>cc: </b>{self.tag}"
            await send_message(self.message, msg, button)
        if self.seed:
            await clean_target(self.up_dir)
            async with queue_dict_lock:
                if self.mid in non_queued_up:
                    non_queued_up.remove(self.mid)
            await start_from_queued()
            return
        await clean_download(self.dir)
        async with task_dict_lock:
            if self.mid in task_dict:
                del task_dict[self.mid]
            count = len(task_dict)
        if count == 0:
            await self.clean()
        else:
            await update_status_message(self.message.chat.id)

        async with queue_dict_lock:
            if self.mid in non_queued_up:
                non_queued_up.remove(self.mid)

        await start_from_queued()

    async def on_download_error(self, error, button=None):
        # Log full stack for operators
        LOGGER.exception("Download error (mid=%s, tag=%s): %s", self.mid, self.tag, error)

        async with task_dict_lock:
            if self.mid in task_dict:
                del task_dict[self.mid]
            count = len(task_dict)

        await self.remove_from_same_dir()

        # 1) Generic user message (no internal details)
        generic_msg = (
            "Error occurred leeching your link.\n\n"
            "I've reported it to the programmer to solve the issue as soon as possible.\n\n"
            "Please try again later."
        )
        try:
            await send_message(self.message, generic_msg)
        except Exception:
            pass

        # 2) Detailed error to forum topic
        try:
            err_text = escape(str(error))
            if ERROR_FORUM_CHAT_ID:
                await TgClient.bot.send_message(
                    ERROR_FORUM_CHAT_ID,
                    f"❌ Download error for user {self.user_id} (tag: {self.tag}):\n{err_text}",
                    message_thread_id=ERROR_THREAD_ID,
                    disable_notification=True,
                )
        except Exception as e:
            LOGGER.warning("Failed to report download error to group: %s", e)

        # Post-error housekeeping
        if count == 0:
            await self.clean()
        else:
            await update_status_message(self.message.chat.id)

        if (
            self.is_super_chat
            and Config.INCOMPLETE_TASK_NOTIFIER
            and Config.DATABASE_URL
        ):
            try:
                await database.rm_complete_task(self.message.link)
            except Exception:
                pass

        # Unblock queues safely for both dict/non-dict entries
        def _signal_and_del(dct):
            if self.mid in dct:
                entry = dct[self.mid]
                try:
                    (entry["event"] if isinstance(entry, dict) else entry).set()
                except Exception:
                    pass
                try:
                    del dct[self.mid]
                except Exception:
                    pass

        async with queue_dict_lock:
            _signal_and_del(queued_dl)
            _signal_and_del(queued_up)
            if self.mid in non_queued_dl:
                non_queued_dl.remove(self.mid)
            if self.mid in non_queued_up:
                non_queued_up.remove(self.mid)

        await start_from_queued()

        # Usage & cleanup
        try:
            await add_usage(self.user_id, int(getattr(self, "size", 0)) // (1024 * 1024))
        except Exception:
            pass
        await sleep(3)
        try:
            await clean_download(self.dir)
        except Exception:
            pass
        try:
            if getattr(self, "up_dir", None):
                await clean_download(self.up_dir)
        except Exception:
            pass
        try:
            if self.thumb and await aiopath.exists(self.thumb):
                await remove(self.thumb)
        except Exception:
            pass

    async def on_upload_error(self, error):
        # Log full stack for operators
        LOGGER.exception("Upload error (mid=%s, tag=%s): %s", self.mid, self.tag, error)

        async with task_dict_lock:
            if self.mid in task_dict:
                del task_dict[self.mid]
            count = len(task_dict)

        # 1) Generic user message (no internal details)
        generic_msg = (
            "an error occurred leeching your link.\n"
            "I've reported it to the programmer to solve the issue as soon as possible.\n"
            "Please try again later."
        )
        try:
            await send_message(self.message, generic_msg)
        except Exception:
            pass

        # 2) Detailed error to forum topic
        try:
            err_text = escape(str(error))
            if ERROR_FORUM_CHAT_ID:
                await TgClient.bot.send_message(
                    ERROR_FORUM_CHAT_ID,
                    f"❌ Upload error for user {self.user_id} (tag: {self.tag}):\n{err_text}",
                    message_thread_id=ERROR_THREAD_ID,
                    disable_notification=True,
                )
        except Exception as e:
            LOGGER.warning("Failed to report upload error to group: %s", e)

        # Post-error housekeeping
        if count == 0:
            await self.clean()
        else:
            await update_status_message(self.message.chat.id)

        if (
            self.is_super_chat
            and Config.INCOMPLETE_TASK_NOTIFIER
            and Config.DATABASE_URL
        ):
            try:
                await database.rm_complete_task(self.message.link)
            except Exception:
                pass

        # Unblock queues safely for both dict/non-dict entries
        def _signal_and_del(dct):
            if self.mid in dct:
                entry = dct[self.mid]
                try:
                    (entry["event"] if isinstance(entry, dict) else entry).set()
                except Exception:
                    pass
                try:
                    del dct[self.mid]
                except Exception:
                    pass

        async with queue_dict_lock:
            _signal_and_del(queued_dl)
            _signal_and_del(queued_up)
            if self.mid in non_queued_dl:
                non_queued_dl.remove(self.mid)
            if self.mid in non_queued_up:
                non_queued_up.remove(self.mid)

        await start_from_queued()

        # Usage & cleanup
        try:
            await add_usage(self.user_id, int(getattr(self, "size", 0)) // (1024 * 1024))
        except Exception:
            pass
        await sleep(3)
        try:
            await clean_download(self.dir)
        except Exception:
            pass
        try:
            if getattr(self, "up_dir", None):
                await clean_download(self.up_dir)
        except Exception:
            pass
        try:
            if self.thumb and await aiopath.exists(self.thumb):
                await remove(self.thumb)
        except Exception:
            pass
