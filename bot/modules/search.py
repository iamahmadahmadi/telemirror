# bot/modules/search.py
import logging
from pyrogram.types import Message

from ..core.torrent_manager import TorrentManager
from ..helper.telegram_helper.message_utils import send_message, edit_message

LOGGER = logging.getLogger(__name__)

# Global flag so handlers can check quickly
QB_SEARCH_ENABLED = False


async def initiate_search_tools():
    """
    Initialize qBittorrent Search plugins, if qB is connected.
    Safe to call at startup even if qB is absent.
    """
    global QB_SEARCH_ENABLED

    qb = TorrentManager.qbittorrent
    if qb is None:
        LOGGER.info("qBittorrent not connected; skipping search plugin initialization.")
        QB_SEARCH_ENABLED = False
        return

    try:
        # Ensure search engine is usable (this will raise if API is missing)
        plugins = await qb.search.plugins()
        LOGGER.info("qB search available with %d plugins.", len(plugins))
        QB_SEARCH_ENABLED = True
    except Exception as e:
        LOGGER.warning("initiate_search_tools: failed to init qB search: %s", e)
        QB_SEARCH_ENABLED = False


async def torrent_search(_, message: Message):
    """
    Handler for /search (or whatever command you wired).
    Gracefully degrades when qB is not available.
    """
    if TorrentManager.qbittorrent is None or not QB_SEARCH_ENABLED:
        await send_message(
            message,
            "🔎 Torrent search is unavailable (qBittorrent not connected).",
        )
        return

    query = (message.text or "").split(maxsplit=1)
    if len(query) < 2 or not query[1].strip():
        await send_message(message, "Usage: /search <query>")
        return

    q = query[1].strip()
    reply = await send_message(message, f"Searching for: <code>{q}</code> …")
    try:
        res = await TorrentManager.qbittorrent.search.start(pattern=q, plugins="all", categories="all")
        # qB returns an id; poll results once (or implement pagination/polling as needed)
        rid = res.id
        results = await TorrentManager.qbittorrent.search.results(id=rid, limit=50, offset=0)
        await TorrentManager.qbittorrent.search.stop(id=rid)

        total = results.total or 0
        if total == 0:
            await edit_message(reply, "No results found.")
            return

        # Very simple formatting; customize as you like
        lines = []
        for it in results.results[:20]:
            title = it.fileName or it.fileUrl or "—"
            size = it.fileSize or 0
            seeds = it.nbSeeders if it.nbSeeders is not None else "?"
            link = it.fileUrl or it.fileName
            lines.append(f"• {title}\n  Seeds: {seeds} | Size: {size} | <code>{link}</code>")

        text = "Top results:\n\n" + "\n\n".join(lines)
        await edit_message(reply, text)
    except Exception as e:
        LOGGER.exception("torrent_search failed")
        await edit_message(reply, f"Search failed: <code>{e}</code>")


async def torrent_search_update(_, message: Message):
    """
    Keep a stub here; if you have an inline update flow, guard it too.
    """
    if TorrentManager.qbittorrent is None or not QB_SEARCH_ENABLED:
        await send_message(
            message,
            "🔎 Torrent search is unavailable (qBittorrent not connected).",
        )
        return
    await send_message(message, "Not implemented in this build.")
