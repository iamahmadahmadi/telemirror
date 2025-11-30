from pyrogram import Client, enums
from asyncio import Lock
import os
from aiofiles.os import makedirs

from .. import LOGGER
from .config_manager import Config


# Where Pyrogram will store its *.session SQLite files.
# Override with:  export PYROGRAM_WORKDIR=/some/writable/path
SESS_DIR = os.getenv("PYROGRAM_WORKDIR", "/opt/telemirror/sessions")


class TgClient:
    _lock = Lock()
    bot = None
    user = None
    NAME = ""
    ID = 0
    IS_PREMIUM_USER = True
    MAX_SPLIT_SIZE = 4194304000

    @classmethod
    async def start_bot(cls):
        LOGGER.info("Creating client from BOT_TOKEN")
        await makedirs(SESS_DIR, exist_ok=True)  # ensure directory exists
        cls.ID = Config.BOT_TOKEN.split(":", 1)[0]
        cls.bot = Client(
            cls.ID,
            Config.TELEGRAM_API,
            Config.TELEGRAM_HASH,
            proxy=Config.TG_PROXY,
            bot_token=Config.BOT_TOKEN,
            workdir=SESS_DIR,  # <-- important
            parse_mode=enums.ParseMode.HTML,
            max_concurrent_transmissions=10,
            workers=64,
        )
        await cls.bot.start()
        cls.NAME = cls.bot.me.username

    @classmethod
    async def start_user(cls):
        if Config.USER_SESSION_STRING:
            LOGGER.info("Creating client from USER_SESSION_STRING")
            try:
                await makedirs(SESS_DIR, exist_ok=True)  # ensure directory exists
                cls.user = Client(
                    "user",
                    Config.TELEGRAM_API,
                    Config.TELEGRAM_HASH,
                    proxy=Config.TG_PROXY,
                    session_string=Config.USER_SESSION_STRING,
                    workdir=SESS_DIR,  # <-- important
                    parse_mode=enums.ParseMode.HTML,
                    sleep_threshold=0,
                    max_concurrent_transmissions=10,
                    workers=64,
                )
                await cls.user.start()
                cls.IS_PREMIUM_USER = bool(getattr(cls.user.me, "is_premium", False))
                if cls.IS_PREMIUM_USER:
                    cls.MAX_SPLIT_SIZE = 4194304000
            except Exception as e:
                LOGGER.error(f"Failed to start client from USER_SESSION_STRING. {e}")
                cls.IS_PREMIUM_USER = False
                cls.user = None

    @classmethod
    async def stop(cls):
        async with cls._lock:
            if cls.bot:
                await cls.bot.stop()
            if cls.user:
                await cls.user.stop()
            LOGGER.info("Client(s) stopped")

    @classmethod
    async def reload(cls):
        async with cls._lock:
            await cls.bot.restart()
            if cls.user:
                await cls.user.restart()
            LOGGER.info("Client(s) restarted")
