from aioaria2 import Aria2WebsocketClient
from aioqbt.client import create_client
from aiohttp import ClientSession, ClientError
from asyncio import gather, TimeoutError
from pathlib import Path
from inspect import iscoroutinefunction
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from .. import LOGGER, aria2_options
from .config_manager import Config


def wrap_with_retry(obj, max_retries: int = 3):
    """
    Add a simple retry policy to all coroutine methods of an object.
    Retries on common transient network errors.
    """
    for attr_name in dir(obj):
        if attr_name.startswith("_"):
            continue
        attr = getattr(obj, attr_name)
        if iscoroutinefunction(attr):
            retry_policy = retry(
                stop=stop_after_attempt(max_retries),
                wait=wait_exponential(multiplier=1, min=1, max=5),
                retry=retry_if_exception_type((ClientError, TimeoutError, RuntimeError)),
            )
            setattr(obj, attr_name, retry_policy(attr))
    return obj


class TorrentManager:
    aria2 = None
    qbittorrent = None

    @classmethod
    async def initiate(cls):
        # 1) Aria2 (required)
        cls.aria2 = await Aria2WebsocketClient.new("http://localhost:6800/jsonrpc")
        LOGGER.info("Aria2 connected at http://localhost:6800/jsonrpc")

        # 2) qBittorrent (optional – controlled by config)
        if not getattr(Config, "QBITTORRENT_ENABLED", True):
            cls.qbittorrent = None
            LOGGER.info("qBittorrent disabled by config; continuing without it.")
            return

        qbit_base = "http://localhost:8090/api/v2/"
        try:
            # quick probe with short-lived session
            async with ClientSession() as s:
                url = qbit_base.rstrip("/") + "/app/webapiVersion"
                async with s.get(url, timeout=3) as resp:
                    if resp.status != 200:
                        raise ClientError(f"Probe HTTP {resp.status}")

            # probe succeeded → create client
            qb = await create_client(qbit_base)
            cls.qbittorrent = wrap_with_retry(qb)
            LOGGER.info("qBittorrent connected at %s", qbit_base)
        except Exception as e:
            cls.qbittorrent = None
            # info-level so it doesn't look like a failure when optional
            LOGGER.info(
                "qBittorrent not available at %s; continuing without it: %s",
                qbit_base, e
            )

    @classmethod
    async def close_all(cls):
        tasks = []
        if cls.aria2:
            tasks.append(cls.aria2.close())
        if cls.qbittorrent:
            try:
                tasks.append(cls.qbittorrent.close())
            except Exception:
                pass
        if tasks:
            await gather(*tasks)

    @classmethod
    async def aria2_remove(cls, download):
        if download.get("status", "") in ["active", "paused", "waiting"]:
            await cls.aria2.forceRemove(download.get("gid", ""))
        else:
            try:
                await cls.aria2.removeDownloadResult(download.get("gid", ""))
            except Exception:
                pass

    @classmethod
    async def remove_all(cls):
        await cls.pause_all()
        if cls.qbittorrent:
            try:
                await cls.qbittorrent.torrents.delete("all", False)
            except Exception:
                pass
        try:
            await cls.aria2.purgeDownloadResult()
        except Exception:
            pass

        downloads = []
        results = await gather(cls.aria2.tellActive(), cls.aria2.tellWaiting(0, 1000))
        for res in results:
            downloads.extend(res)
        tasks = [cls.aria2.forceRemove(d.get("gid")) for d in downloads]
        try:
            await gather(*tasks)
        except Exception:
            pass

    @classmethod
    async def overall_speed(cls):
        download_speed = upload_speed = 0
        if cls.qbittorrent:
            try:
                s1 = await cls.qbittorrent.transfer.info()
                download_speed += s1.dl_info_speed
                upload_speed += s1.up_info_speed
            except Exception:
                pass
        try:
            s2 = await cls.aria2.getGlobalStat()
            download_speed += int(s2.get("downloadSpeed", "0"))
            upload_speed += int(s2.get("uploadSpeed", "0"))
        except Exception:
            pass
        return download_speed, upload_speed

    @classmethod
    async def pause_all(cls):
        tasks = [cls.aria2.forcePauseAll()]
        if cls.qbittorrent:
            tasks.append(cls.qbittorrent.torrents.stop("all"))
        await gather(*tasks)

    @classmethod
    async def change_aria2_option(cls, key, value):
        downloads = []
        results = await gather(cls.aria2.tellActive(), cls.aria2.tellWaiting(0, 1000))
        for res in results:
            downloads.extend(res)

        tasks = []
        for download in downloads:
            if download.get("status", "") != "complete":
                tasks.append(cls.aria2.changeOption(download.get("gid"), {key: value}))
        if tasks:
            try:
                await gather(*tasks)
            except Exception as e:
                LOGGER.error(e)

        # Persist to global; ignore special per-task keys
        if key not in ["checksum", "index-out", "out", "pause", "select-file"]:
            try:
                await cls.aria2.changeGlobalOption({key: value})
                aria2_options[key] = value
            except Exception as e:
                LOGGER.error("Failed to change Aria2 global option %s: %s", key, e)


def aria2_name(download_info):
    if "bittorrent" in download_info and download_info["bittorrent"].get("info"):
        return download_info["bittorrent"]["info"]["name"]
    elif download_info.get("files"):
        if download_info["files"][0]["path"].startswith("[METADATA]"):
            return download_info["files"][0]["path"]
        file_path = download_info["files"][0]["path"]
        dir_path = download_info["dir"]
        if file_path.startswith(dir_path):
            return Path(file_path[len(dir_path) + 1 :]).parts[0]
        else:
            return ""
    else:
        return ""


def is_metadata(download_info):
    return any(
        f["path"].startswith("[METADATA]") for f in download_info.get("files", [])
    )
