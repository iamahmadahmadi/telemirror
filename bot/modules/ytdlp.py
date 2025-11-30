from httpx import AsyncClient
from asyncio import wait_for, Event
from functools import partial
from pyrogram.filters import regex, user
from pyrogram.handlers import CallbackQueryHandler
from time import time
from yt_dlp import YoutubeDL
import re  # <-- needed for height parsing

from .. import LOGGER, bot_loop, task_dict_lock, DOWNLOAD_DIR
from ..core.config_manager import Config
from ..helper.ext_utils.bot_utils import (
    new_task,
    sync_to_async,
    arg_parser,
    COMMAND_USAGE,
)
from ..helper.ext_utils.links_utils import is_url
from ..helper.ext_utils.status_utils import get_readable_file_size, get_readable_time
from ..helper.listeners.task_listener import TaskListener
from ..helper.mirror_leech_utils.download_utils.yt_dlp_download import YoutubeDLHelper
from ..helper.telegram_helper.button_build import ButtonMaker
from ..helper.telegram_helper.message_utils import (
    send_message,
    edit_message,
    delete_message,
)
from ..db.sqlite_db import get_file_by_source_quality, increment_download
from ..db.sqlite_db import get_remaining_today_mb
from ..helper.telegram_helper.message_utils import build_over_quota_kb  # cleaned duplicate import


@new_task
async def select_format(_, query, obj):
    data = query.data.split()
    message = query.message
    await query.answer()

    choice = data[1]
    if choice in ("1080p", "720p", "360p", "Audio"):
        cached = obj.cached_rows.get(choice)
        if cached:
            try:
                await message.reply_cached_media(
                    cached["file_id"],
                    caption=f"Saved by @Leechflixbot\n\n{cached['name']}",
                )
            except Exception:
                await message.reply_document(
                    cached["file_id"],
                    caption=f"Saved by @Leechflixbot\n\n{cached['name']}",
                )
            await increment_download(cached["file_id"])
            obj.qual = None
            obj.event.set()
        else:
            obj.listener.yt_quality = choice
            obj.qual = obj.formats[choice]
            obj.event.set()
    elif choice == "cancel":
        await edit_message(message, "Task has been cancelled.")
        obj.qual = None
        obj.listener.is_cancelled = True
        obj.event.set()


class YtSelection:
    def __init__(self, listener):
        self.listener = listener
        self._is_m4a = False
        self._reply_to = None
        self._time = time()
        self._timeout = 120
        self._is_playlist = False
        self._main_buttons = None
        self.event = Event()
        self.formats = {}
        self.qual = None
        self.cached_rows = {}

    async def _event_handler(self):
        pfunc = partial(select_format, obj=self)
        handler = self.listener.client.add_handler(
            CallbackQueryHandler(
                pfunc, filters=regex("^ytq") & user(self.listener.user_id)
            ),
            group=-1,
        )
        try:
            await wait_for(self.event.wait(), timeout=self._timeout)
        except:
            await edit_message(self._reply_to, "Timed Out. Task has been cancelled!")
            self.qual = None
            self.listener.is_cancelled = True
            self.event.set()
        finally:
            self.listener.client.remove_handler(*handler)

    async def get_quality(self, result):
        buttons = ButtonMaker()
        qual_map = {
            "1080p": "bv*[height<=?1080][ext=mp4]+ba[ext=m4a]/b[height<=?1080]",
            "720p":  "bv*[height<=?720][ext=mp4]+ba[ext=m4a]/b[height<=?720]",
            "360p":  "bv*[height<=?360][ext=mp4]+ba[ext=m4a]/b[height<=?360]",
            # "Audio": "ba/b",
        }
        self.formats = qual_map
        for name in ("1080p", "720p", "360p"):  # , "Audio"):
            row = await get_file_by_source_quality(self.listener.link, name)
            btn_name = f"{name}{' ⚡️' if row else ''}"
            buttons.data_button(btn_name, f"ytq {name}")
            if row:
                self.cached_rows[name] = row
        buttons.data_button("Cancel", "ytq cancel", "footer")
        self._main_buttons = buttons.build_menu(2)
        msg = "Choose Quality:"
        self._reply_to = await send_message(
            self.listener.message, msg, self._main_buttons
        )
        await self._event_handler()
        if not self.listener.is_cancelled:
            await delete_message(self._reply_to)
        return self.qual

    async def back_to_main(self):
        if self._is_playlist:
            msg = (
                f"Choose Playlist Videos Quality:\nTimeout: "
                f"{get_readable_time(self._timeout - (time() - self._time))}"
            )
        else:
            msg = (
                f"Choose Video Quality:\nTimeout: "
                f"{get_readable_time(self._timeout - (time() - self._time))}"
            )
        await edit_message(self._reply_to, msg, self._main_buttons)

    async def qual_subbuttons(self, b_name):
        buttons = ButtonMaker()
        tbr_dict = self.formats[b_name]
        for tbr, d_data in tbr_dict.items():
            button_name = f"{tbr}K ({get_readable_file_size(d_data[0])})"
            buttons.data_button(button_name, f"ytq sub {b_name} {tbr}")
        buttons.data_button("Back", "ytq back", "footer")
        buttons.data_button("Cancel", "ytq cancel", "footer")
        subbuttons = buttons.build_menu(2)
        msg = (
            f"Choose Bit rate for <b>{b_name}</b>:\nTimeout: "
            f"{get_readable_time(self._timeout - (time() - self._time))}"
        )
        await edit_message(self._reply_to, msg, subbuttons)

    async def mp3_subbuttons(self):
        i = "s" if self._is_playlist else ""
        buttons = ButtonMaker()
        audio_qualities = [64, 128, 320]
        for q in audio_qualities:
            audio_format = f"ba/b-mp3-{q}"
            buttons.data_button(f"{q}K-mp3", f"ytq {audio_format}")
        buttons.data_button("Back", "ytq back")
        buttons.data_button("Cancel", "ytq cancel")
        subbuttons = buttons.build_menu(3)
        msg = f"Choose mp3 Audio{i} Bitrate:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        await edit_message(self._reply_to, msg, subbuttons)

    async def audio_format(self):
        i = "s" if self._is_playlist else ""
        buttons = ButtonMaker()
        for frmt in ["aac", "alac", "flac", "m4a", "opus", "vorbis", "wav"]:
            audio_format = f"ba/b-{frmt}-"
            buttons.data_button(frmt, f"ytq aq {audio_format}")
        buttons.data_button("Back", "ytq back", "footer")
        buttons.data_button("Cancel", "ytq cancel", "footer")
        subbuttons = buttons.build_menu(3)
        msg = f"Choose Audio{i} Format:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        await edit_message(self._reply_to, msg, subbuttons)

    async def audio_quality(self, format):
        i = "s" if self._is_playlist else ""
        buttons = ButtonMaker()
        for qual in range(11):
            audio_format = f"{format}{qual}"
            buttons.data_button(qual, f"ytq {audio_format}")
        buttons.data_button("Back", "ytq aq back")
        buttons.data_button("Cancel", "ytq aq cancel")
        subbuttons = buttons.build_menu(5)
        msg = (
            f"Choose Audio{i} Qaulity:\n0 is best and 10 is worst\nTimeout: "
            f"{get_readable_time(self._timeout - (time() - self._time))}"
        )
        await edit_message(self._reply_to, msg, subbuttons)


def extract_info(link, options):
    with YoutubeDL(options) as ydl:
        result = ydl.extract_info(link, download=False)
        if result is None:
            raise ValueError("Info result is None")
        return result


async def _mdisk(link, name):
    key = link.split("/")[-1]
    async with AsyncClient(verify=False) as client:
        resp = await client.get(
            f"https://diskuploader.entertainvideo.com/v1/file/cdnurl?param={key}"
        )
    if resp.status_code == 200:
        resp_json = resp.json()
        link = resp_json["source"]
        if not name:
            name = resp_json["filename"]
    return name, link


class YtDlp(TaskListener):
    def __init__(
        self,
        client,
        message,
        _=None,
        is_leech=False,
        __=None,
        ___=None,
        same_dir=None,
        bulk=None,
        multi_tag=None,
        options="",
    ):
        if same_dir is None:
            same_dir = {}
        if bulk is None:
            bulk = []
        self.message = message
        self.client = client
        self.multi_tag = multi_tag
        self.options = options
        self.same_dir = same_dir
        self.bulk = bulk
        super().__init__()
        self.is_ytdlp = True
        self.is_leech = is_leech

    async def new_event(self):
        text = self.message.text.split("\n")
        input_list = text[0].split(" ")
        qual = ""

        args = {
            "-doc": False,
            "-med": False,
            "-s": False,
            "-b": False,
            "-z": False,
            "-sv": False,
            "-ss": False,
            "-f": False,
            "-fd": False,
            "-fu": False,
            "-hl": False,
            "-bt": False,
            "-ut": False,
            "-i": 0,
            "-sp": 0,
            "link": "",
            "-m": "",
            "-opt": {},
            "-n": "",
            "-up": "",
            "-rcf": "",
            "-t": "",
            "-ca": "",
            "-cv": "",
            "-ns": "",
            "-tl": "",
            "-ff": set(),
        }

        arg_parser(input_list[1:], args)
        # --- normalized block after arg_parser ---
        try:
            self.multi = int(args["-i"])
        except:
            self.multi = 0

        try:
            opt = eval(args["-opt"]) if args["-opt"] else {}
        except Exception as e:
            LOGGER.error(e)
            opt = {}

        self.ffmpeg_cmds = args["-ff"]
        self.select = args["-s"]
        self.name = args["-n"]
        self.up_dest = args["-up"]
        self.rc_flags = args["-rcf"]
        self.link = args["link"]

        # --- robust URL fallback for direct-call invocations ---
        if not self.link:
            # 1) Try pyrogram's parsed tokens (may not exist on direct call)
            try:
                _toks = getattr(self.message, "command", None) or []
            except Exception:
                _toks = []
            if (
                isinstance(_toks, (list, tuple))
                and len(_toks) >= 2
                and isinstance(_toks[1], str)
            ):
                _cand = _toks[1].strip()
                if _cand.startswith("http://") or _cand.startswith("https://"):
                    self.link = _cand
                    LOGGER.info(
                        "[YtDlp.new_event] Fallback set link from message.command: %s",
                        self.link,
                    )
        # 2) Plain scan of message.text for the first URL
        if not self.link:
            try:
                for _part in (self.message.text or "").split():
                    if _part.startswith("http://") or _part.startswith("https://"):
                        self.link = _part.strip()
                        LOGGER.info(
                            "[YtDlp.new_event] Fallback set link by scanning text: %s",
                            self.link,
                        )
                        break
            except Exception as _e:
                LOGGER.warning("[YtDlp.new_event] text scan failed: %s", _e)
        LOGGER.info("[YtDlp.new_event] final link after fallbacks: %r", self.link)
        # --- /fallback ---
        self.compress = args["-z"]
        self.thumb = args["-t"]
        self.split_size = args["-sp"]
        self.sample_video = args["-sv"]
        self.screen_shots = args["-ss"]
        self.force_run = args["-f"]
        self.force_download = args["-fd"]
        self.force_upload = args["-fu"]
        self.convert_audio = args["-ca"]
        self.convert_video = args["-cv"]
        self.name_sub = args["-ns"]
        self.hybrid_leech = args["-hl"]
        self.thumbnail_layout = args["-tl"]
        self.as_doc = args["-doc"]
        self.as_med = args["-med"]
        self.folder_name = f"/{args['-m']}".rstrip("/") if len(args["-m"]) > 0 else ""
        self.bot_trans = args["-bt"]
        self.user_trans = args["-ut"]

        # --- Fallback: if arg_parser missed the URL, take it from the command tokens ---
        try:
            from ..helper.ext_utils.links_utils import is_url as _is_url_fallback
        except Exception:
            def _is_url_fallback(_):
                return False

        # input_list = text[0].split(" ") was built earlier; use token 1 when present
        try:
            _tokens = text[0].split(" ")
        except Exception:
            _tokens = []

        if not self.link and len(_tokens) >= 2 and _is_url_fallback(_tokens[1]):
            self.link = _tokens[1]
            LOGGER.info(
                "[YtDlp.new_event] Fallback set link from tokens: %s", self.link
            )
        # -------------------------------------------------------------------------------
        # --- /normalized block ---
        is_bulk = args["-b"]

        bulk_start = 0
        bulk_end = 0
        reply_to = None

        if not isinstance(is_bulk, bool):
            dargs = is_bulk.split(":")
            bulk_start = dargs[0] or None
            if len(dargs) == 2:
                bulk_end = dargs[1] or None
            is_bulk = True

        if not is_bulk:
            if self.multi > 0:
                if self.folder_name:
                    async with task_dict_lock:
                        if self.folder_name in self.same_dir:
                            self.same_dir[self.folder_name]["tasks"].add(self.mid)
                            for fd_name in self.same_dir:
                                if fd_name != self.folder_name:
                                    self.same_dir[fd_name]["total"] -= 1
                        elif self.same_dir:
                            self.same_dir[self.folder_name] = {
                                "total": self.multi,
                                "tasks": {self.mid},
                            }
                            for fd_name in self.same_dir:
                                if fd_name != self.folder_name:
                                    self.same_dir[fd_name]["total"] -= 1
                        else:
                            self.same_dir = {
                                self.folder_name: {
                                    "total": self.multi,
                                    "tasks": {self.mid},
                                }
                            }
                elif self.same_dir:
                    async with task_dict_lock:
                        for fd_name in self.same_dir:
                            self.same_dir[fd_name]["total"] -= 1
        else:
            await self.init_bulk(input_list, bulk_start, bulk_end, YtDlp)
            return

        if len(self.bulk) != 0:
            del self.bulk[0]

        path = f"{DOWNLOAD_DIR}{self.mid}{self.folder_name}"

        await self.get_tag(text)

        opt = opt or self.user_dict.get("YT_DLP_OPTIONS") or Config.YT_DLP_OPTIONS

        if not self.link and (reply_to := self.message.reply_to_message):
            self.link = reply_to.text.split("\n", 1)[0].strip()

        LOGGER.info("[YtDlp.new_event] post-parse link=%r", self.link)
        if not is_url(self.link):
            await send_message(
                self.message, COMMAND_USAGE["yt"][0], COMMAND_USAGE["yt"][1]
            )
            await self.remove_from_same_dir()
            return

        if "mdisk.me" in self.link:
            self.name, self.link = await _mdisk(self.link, self.name)

        try:
            await self.before_start()
        except Exception as e:
            await send_message(self.message, e)
            await self.remove_from_same_dir()
            return

        # --- yt-dlp options ---
        options = {"usenetrc": True, "cookiefile": "cookies.txt"}
        if opt:
            for key, value in opt.items():
                if key in ["postprocessors", "download_ranges"]:
                    continue
                if key == "format" and not self.select:
                    if value.startswith("ba/b-"):
                        qual = value
                        continue
                    else:
                        qual = value
                options[key] = value
        options["playlist_items"] = "0"
        options["no_color"] = True  # avoid ANSI codes in error strings

        try:
            result = await sync_to_async(extract_info, self.link, options)
        except Exception as e:
            msg = str(e).replace("<", " ").replace(">", " ")
            await send_message(self.message, f"{self.tag} {msg}")
            await self.remove_from_same_dir()
            return
        finally:
            await self.run_multi(input_list, YtDlp)

        # site/extractor key for per-site tweaks (e.g., Instagram)
        extractor_key = (result.get("extractor_key") or result.get("extractor") or "").lower()

        if not qual:
            qual = await YtSelection(self).get_quality(result)
            if qual is None:
                await self.remove_from_same_dir()
                return

        # ---- Instagram-friendly format fallback (avoid bv+ba mux on IG) ----
        if "instagram" in extractor_key and isinstance(qual, str):
            m = re.search(r'height<=\?(\d+)', qual)
            h = m.group(1) if m else None
            qual = f"best[height<=?{h}]/best" if h else "best"
        # -------------------------------------------------------------------

        # ---- AUDIO MODE INJECTION (make audio → .mp3 for Telegram music UI) ----
        try:
            _q = str(qual or "")
            is_audio_choice = _q.startswith("ba")
        except Exception:
            is_audio_choice = False

        try:
            if not isinstance(opt, dict):
                opt = {} if opt is None else dict(opt)
        except Exception:
            opt = {}

        if is_audio_choice:
            try:
                pps = opt.get("postprocessors")
                if not isinstance(pps, list):
                    pps = []
                if not any(
                    isinstance(pp, dict) and pp.get("key") == "FFmpegExtractAudio"
                    for pp in pps
                ):
                    pps.append(
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192",
                        }
                    )
                opt["postprocessors"] = pps
                opt.setdefault("audio_format", "mp3")
            except Exception as _e:
                LOGGER.warning("Failed to inject audio postprocessor: %s", _e)
        # -----------------------------------------------------------------------

        LOGGER.info(
            "Downloading with YT-DLP: %s | audio_mode=%s", self.link, is_audio_choice
        )
        playlist = "entries" in result
        ydl = YoutubeDLHelper(self)

        # ---- Retry once with a safer "best" fallback if chosen format isn't available ----
        try:
            await ydl.add_download(path, qual, playlist, opt)
        except Exception as e:
            emsg = str(e)
            if (
                "Requested format is not available" in emsg
                or "requested format not available" in emsg.lower()
                or "format is not available" in emsg.lower()
            ):
                # derive height from current qual and retry with best
                m = re.search(r'height<=\?(\d+)', str(qual) or "")
                h = m.group(1) if m else None
                fallback_qual = f"best[height<=?{h}]/best" if h else "best"
                LOGGER.warning("yt-dlp format not available; retrying with %s", fallback_qual)
                await ydl.add_download(path, fallback_qual, playlist, opt)
            else:
                raise
        # -------------------------------------------------------------------------------

"""" 
        # START UPLOADER FOR LEECH MODE
        from ..helper.mirror_leech_utils.telegram_uploader import TelegramUploader
        LOGGER.info("[ytdlp] download complete; starting uploader | path=%s | is_leech=%s", path, self.is_leech)
        if self.is_leech:
            await TelegramUploader().upload(self, path)
        else:
            LOGGER.info("[ytdlp] is_leech is False; not invoking uploader automatically.")
"""


async def ytdl(client, message):
    bot_loop.create_task(YtDlp(client, message).new_event())


async def ytdl_leech(client, message):
    uid = (message.from_user or message.sender_chat).id
    remaining = await get_remaining_today_mb(uid)
    if remaining <= 0:
        text = (
            "🚫 You’ve reached today’s free limit.\n\n"
            "Watch an ad to proceed **once** or upgrade for a higher daily cap."
        )
        # ✅ use buttons= (works even if legacy callers use reply_markup=)
        await send_message(message, text, buttons=build_over_quota_kb())
        return
    LOGGER.info(
        "[ytdlp.py::ytdl_leech] ENTER user=%s chat=%s text=%r command=%r",
        getattr(message.from_user, "id", None),
        getattr(message.chat, "id", None),
        message.text,
        getattr(message, "command", None),
    )
    # --- AUTO-INJECT: yl debug + link shim ---
    try:
        from ..helper.ext_utils.bot_utils import get_links
    except Exception:
        get_links = None

    try:
        from ..helper.telegram_helper.bot_commands import BotCommands
        from ..helper.ext_utils.links_utils import is_url as _is_url_check
    except Exception:
        BotCommands = None

        def _is_url_check(_):
            return False

    LOGGER.info(
        "[ytdlp.py::ytdl_leech] pre-parse text=%r command=%r",
        message.text,
        getattr(message, "command", None),
    )

    _links = []
    if get_links:
        try:
            _links = await get_links(message)
        except Exception as e:
            LOGGER.warning("[ytdlp.py::ytdl_leech] get_links failed: %s", e)

    if not _links and isinstance(getattr(message, "command", None), (list, tuple)) and len(message.command) >= 2:
        _url = message.command[1]
        if isinstance(_url, str) and _is_url_check(_url):
            # Normalize to the *shortest* /yl alias if BotCommands defines a list
            _alias = "yl"
            try:
                _bc = getattr(BotCommands, "YtdlLeechCommand", "yl")
                if isinstance(_bc, (list, tuple)) and _bc:
                    _alias = min((str(x) for x in _bc), key=len)
                else:
                    _alias = str(_bc)
            except Exception:
                pass
            message.text = f"/{_alias} {_url}"
            LOGGER.info("[ytdlp.py::ytdl_leech] shimmed message.text=%r", message.text)
            # Try parsing again for visibility
            if get_links:
                try:
                    _links = await get_links(message)
                except Exception:
                    pass

    LOGGER.info("[ytdlp.py::ytdl_leech] links=%r", _links)
    # --- /AUTO-INJECT ---

    bot_loop.create_task(YtDlp(client, message, is_leech=True).new_event())
