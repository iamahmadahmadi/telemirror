# bot/core/handlers.py

from pyrogram import filters
from pyrogram.filters import command, regex
from pyrogram.handlers import MessageHandler, CallbackQueryHandler, EditedMessageHandler

from ..modules.queue import queue_cmd
from ..modules import (
    auto_leech,
    set_premium, show_usage,
    authorize, unauthorize, add_sudo, remove_sudo,
    send_bot_settings, edit_bot_settings, cancel, cancel_all_buttons,
    cancel_all_update, cancel_multi, clone_node, aioexecute, execute,
    clear, select, confirm_selection, remove_from_queue, count_node,
    delete_file, gdrive_search, select_type, arg_usage, mirror,
    qb_mirror, jd_mirror, nzb_mirror, leech, qb_leech, jd_leech, nzb_leech,
    get_rss_menu, rss_listener, run_shell, start, log, restart_bot,
    confirm_restart, ping, bot_stats, task_status, status_pages,
    torrent_search, torrent_search_update, get_users_settings, send_user_settings,
    edit_user_settings, ytdl, ytdl_leech, hydra_search, my_files, get_cached,
    my_files_page,
)

# Premium / invite / help / about from services
from ..modules.services import (
    invite,
    premium_callback as premium_open_cb,
    help as help_cmd,
    about as about_cmd,
)

from ..helper.telegram_helper.bot_commands import BotCommands
from ..helper.telegram_helper.filters import CustomFilters
from ..helper.telegram_helper.message_utils import send_message  # for premium fallback
from .mltb_client import TgClient

# Try to import the real premium shop handler; if missing, build a fallback opener
try:
    from ..modules.premium_shop import buy_premium as buy_premium_cb  # message handler for /premium
    _HAS_PREMIUM_SHOP = True
except Exception:
    _HAS_PREMIUM_SHOP = False
    # Fallback: reuse services’ price keyboard builder
    from ..modules.services import _premium_plans_kb  # noqa: F401

    async def buy_premium_cb(client, message):
        """
        Fallback /premium handler when premium_shop is unavailable.
        Sends the same menu the /start premium deep-link would show.
        """
        uid = getattr(getattr(message, "from_user", None), "id", None) or message.chat.id
        text = (
            "💎 Premium\n\n"
            "— Daily usage limit: 60 GB\n"
            "— Video duration: up to 10 hours\n"
            "— Downloading requests in parallel: 10\n"
            "— Increased priority in processing queue\n"
            "— Disabled NSFW filter\n\n"
            "👉 Choose a plan below and complete your purchase:"
        )
        await send_message(message, text, _premium_plans_kb(uid))


def add_handlers():
    # 1) Auto-detect bare links first (runs before everything else)
    TgClient.bot.add_handler(
        MessageHandler(
            auto_leech,
            filters=(
                (filters.text | filters.caption)
                & filters.regex(r"(https?://|www\.)")
                & ~filters.regex(r"^/")  # ignore if it starts with a slash (commands)
            ),
        ),
        group=-10,
    )

    # 2) Standard commands
    TgClient.bot.add_handler(
        MessageHandler(
            queue_cmd,
            filters=command(["queue", "q"], case_sensitive=True),
        )
    )

    # Admin / owner controls
    TgClient.bot.add_handler(
        MessageHandler(
            add_sudo,
            filters=command(BotCommands.AddSudoCommand, case_sensitive=True)
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            remove_sudo,
            filters=command(BotCommands.RmSudoCommand, case_sensitive=True)
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            send_bot_settings,
            filters=command(BotCommands.BotSetCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(
            edit_bot_settings, filters=regex("^botset") & CustomFilters.sudo
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            cancel,
            filters=command(BotCommands.CancelTaskCommand, case_sensitive=True),
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            cancel_all_buttons,
            filters=command(BotCommands.CancelAllCommand, case_sensitive=True),
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(cancel_all_update, filters=regex("^canall"))
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(cancel_multi, filters=regex("^stopm"))
    )

    # Drive / maintenance
    TgClient.bot.add_handler(
        MessageHandler(
            clone_node,
            filters=command(BotCommands.CloneCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            aioexecute,
            filters=command(BotCommands.AExecCommand, case_sensitive=True)
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            execute,
            filters=command(BotCommands.ExecCommand, case_sensitive=True)
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            clear,
            filters=command(BotCommands.ClearLocalsCommand, case_sensitive=True)
            & CustomFilters.owner,
        )
    )

    # Selection flow
    TgClient.bot.add_handler(
        MessageHandler(
            select,
            filters=command(BotCommands.SelectCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(confirm_selection, filters=regex("^sel"))
    )
    TgClient.bot.add_handler(
        MessageHandler(
            remove_from_queue,
            filters=command(BotCommands.ForceStartCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )

    # Drive utilities
    TgClient.bot.add_handler(
        MessageHandler(
            count_node,
            filters=command(BotCommands.CountCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            delete_file,
            filters=command(BotCommands.DeleteCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            gdrive_search,
            filters=command(BotCommands.ListCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(select_type, filters=regex("^list_types"))
    )
    TgClient.bot.add_handler(CallbackQueryHandler(arg_usage, filters=regex("^help")))

    # Mirrors
    TgClient.bot.add_handler(
        MessageHandler(
            mirror,
            filters=command(BotCommands.MirrorCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            qb_mirror,
            filters=command(BotCommands.QbMirrorCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            jd_mirror,
            filters=command(BotCommands.JdMirrorCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            nzb_mirror,
            filters=command(BotCommands.NzbMirrorCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )

    # Leeches
    TgClient.bot.add_handler(
        MessageHandler(
            leech,
            filters=command(BotCommands.LeechCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            qb_leech,
            filters=command(BotCommands.QbLeechCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            jd_leech,
            filters=command(BotCommands.JdLeechCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            nzb_leech,
            filters=command(BotCommands.NzbLeechCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )

    # RSS
    TgClient.bot.add_handler(
        MessageHandler(
            get_rss_menu,
            filters=command(BotCommands.RssCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(CallbackQueryHandler(rss_listener, filters=regex("^rss")))

    # Shell
    TgClient.bot.add_handler(
        MessageHandler(
            run_shell,
            filters=command(BotCommands.ShellCommand, case_sensitive=True)
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        EditedMessageHandler(
            run_shell,
            filters=command(BotCommands.ShellCommand, case_sensitive=True)
            & CustomFilters.owner,
        )
    )

    # Core bot commands
    TgClient.bot.add_handler(
        MessageHandler(
            start, filters=command(BotCommands.StartCommand, case_sensitive=True)
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            log,
            filters=command(BotCommands.LogCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            restart_bot,
            filters=command(BotCommands.RestartCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(
            confirm_restart, filters=regex("^botrestart") & CustomFilters.sudo
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            ping,
            filters=command(BotCommands.PingCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )

    # ✅ /help (from services.help)
    TgClient.bot.add_handler(
        MessageHandler(
            help_cmd,
            filters=command(BotCommands.HelpCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )

    TgClient.bot.add_handler(
        MessageHandler(
            bot_stats,
            filters=command(BotCommands.StatsCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            task_status,
            filters=command(BotCommands.StatusCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(status_pages, filters=regex("^status"))
    )

    # Searches
    TgClient.bot.add_handler(
        MessageHandler(
            torrent_search,
            filters=command(BotCommands.SearchCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(torrent_search_update, filters=regex("^torser"))
    )

    # Users / settings
    TgClient.bot.add_handler(
        MessageHandler(
            get_users_settings,
            filters=command(BotCommands.UsersCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            send_user_settings,
            filters=command(BotCommands.UserSetCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(edit_user_settings, filters=regex("^userset"))
    )

    # YTDL
    TgClient.bot.add_handler(
        MessageHandler(
            ytdl,
            filters=command(BotCommands.YtdlCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            ytdl_leech,
            filters=command(BotCommands.YtdlLeechCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )

    # NZB / Hydra
    TgClient.bot.add_handler(
        MessageHandler(
            hydra_search,
            filters=command(BotCommands.NzbSearchCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )

    # 3) Premium / Usage / Invite
    TgClient.bot.add_handler(
        MessageHandler(
            buy_premium_cb,
            filters=command(BotCommands.BuyPremiumCommand, case_sensitive=True)
            # & CustomFilters.authorized,  # allow even if not authorized to see pricing
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            set_premium,
            filters=command(BotCommands.SetPremiumCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            show_usage,
            filters=command(BotCommands.UsageCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            invite,
            filters=command(BotCommands.InviteCommand, case_sensitive=True)
        )
    )

    # /about (from services.about)
    TgClient.bot.add_handler(
        MessageHandler(
            about_cmd,
            filters=command(BotCommands.AboutCommand, case_sensitive=True)
        )
    )

    # /myfiles + pagination
    TgClient.bot.add_handler(
        MessageHandler(
            my_files,
            filters=command(BotCommands.MyFilesCommand, case_sensitive=True)
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(my_files_page, filters=regex(r"^myfiles:p=\d+$"))
    )

    # Get cached file by slug (/get_XXXXX)
    TgClient.bot.add_handler(
        MessageHandler(
            get_cached,
            filters=filters.regex(r"^/get_[A-Za-z0-9_-]+")
        )
    )

    # Inline “Upgrade to Premium” button (re-open premium menu)
    TgClient.bot.add_handler(
        CallbackQueryHandler(
            premium_open_cb,
            filters=regex(r"^premium_open$") & CustomFilters.authorized
        )
    )
