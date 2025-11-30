from . import LOGGER, bot_loop
from .core.mltb_client import TgClient
from .core.config_manager import Config
import logging
import sys
import signal
import asyncio
import os

# --- Enhanced logging configuration: file + stdout ---
LOG_FILE = os.environ.get("BOT_LOG_FILE", "log.txt")

_logging_handlers = [
    logging.StreamHandler(sys.stdout),
]
try:
    _logging_handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
except Exception:
    # If file handler fails (e.g., read-only FS), continue with stdout only.
    pass

logging.basicConfig(
    level=os.environ.get("BOT_LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=_logging_handlers,
)

# --- Asyncio loop exception handler to surface hidden crashes ---
def _loop_exception_handler(loop, context):
    msg = context.get("message", "")
    exc = context.get("exception")
    logging.error("UNCAUGHT ASYNCIO ERROR: %s", msg or "<no message>")
    if exc:
        logging.exception(exc)

bot_loop.set_exception_handler(_loop_exception_handler)

# --- Signal logging so external kills are visible in logs ---
def _log_signal(sig_name):
    def handler(*_):
        logging.warning("Received %s; shutting down soon...", sig_name)
    return handler

for _sig in ("SIGTERM", "SIGINT"):
    if hasattr(signal, _sig):
        try:
            # Prefer asyncio-friendly signal handlers when available
            bot_loop.add_signal_handler(getattr(signal, _sig), _log_signal(_sig))
        except (NotImplementedError, RuntimeError):
            # Fallback (e.g., on Windows or if loop isn't the main thread)
            signal.signal(getattr(signal, _sig), lambda *_: logging.warning("Received %s; shutting down soon...", _sig))

# --- Heartbeat to prove liveness and track memory ---
try:
    import psutil
except Exception:
    psutil = None

async def _heartbeat():
    proc = None
    if psutil:
        try:
            proc = psutil.Process()
        except Exception:
            proc = None
    while True:
        try:
            if proc:
                mem_mb = int(proc.memory_info().rss / (1024 * 1024))
                logging.info("HEARTBEAT | RSS=%sMB | PID=%s", mem_mb, os.getpid())
            else:
                logging.info("HEARTBEAT | PID=%s", os.getpid())
        except Exception as e:
            logging.exception(e)
        await asyncio.sleep(60)

# Kick off heartbeat immediately
bot_loop.create_task(_heartbeat())

Config.load()


async def main():
    from asyncio import gather
    from .core.startup import (
        load_settings,
        load_configurations,
        save_settings,
        update_aria2_options,
        update_nzb_options,
        update_qb_options,
        update_variables,
    )

    await load_settings()

    await gather(TgClient.start_bot(), TgClient.start_user())
    await gather(load_configurations(), update_variables())

    from .core.torrent_manager import TorrentManager

    await TorrentManager.initiate()
    await gather(
        update_qb_options(),
        update_aria2_options(),
        update_nzb_options(),
    )
    from .helper.ext_utils.files_utils import clean_all
    from .core.jdownloader_booter import jdownloader
    from .helper.ext_utils.telegraph_helper import telegraph
    from .helper.mirror_leech_utils.rclone_utils.serve import rclone_serve_booter
    from .modules import (
        initiate_search_tools,
        get_packages_version,
        restart_notification,
    )

    await gather(
        save_settings(),
        jdownloader.boot(),
        clean_all(),
        initiate_search_tools(),
        get_packages_version(),
        restart_notification(),
        telegraph.create_account(),
        rclone_serve_booter(),
    )


bot_loop.run_until_complete(main())

from .helper.ext_utils.bot_utils import create_help_buttons
from .helper.listeners.aria2_listener import add_aria2_callbacks
from .core.handlers import add_handlers

add_aria2_callbacks()
create_help_buttons()
add_handlers()

LOGGER.info("Bot Started!")
bot_loop.run_forever()
