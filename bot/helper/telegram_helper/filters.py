from pyrogram.filters import create
from pyrogram.types import Message, CallbackQuery
from ... import user_data, auth_chats, sudo_users
from ...core.config_manager import Config


def _extract_ids(update):
    """
    Normalize ids for Message and CallbackQuery.
    Returns (uid, chat_id, thread_id).
    """
    # user
    user = getattr(update, "from_user", None) or getattr(update, "sender_chat", None)
    if user is None and isinstance(update, CallbackQuery) and update.message:
        user = update.message.from_user or update.message.sender_chat
    uid = getattr(user, "id", None)

    # chat
    chat = getattr(update, "chat", None)
    if chat is None and isinstance(update, CallbackQuery) and update.message:
        chat = update.message.chat
    chat_id = getattr(chat, "id", None)

    # topic/thread (forums)
    msg = update.message if isinstance(update, CallbackQuery) else update
    thread_id = getattr(msg, "message_thread_id", None) if msg is not None else None

    return uid, chat_id, thread_id


class CustomFilters:
    async def owner_filter(self, _, update):
        uid, _, _ = _extract_ids(update)
        return uid == Config.OWNER_ID

    owner = create(owner_filter)

    async def authorized_user(self, _, update):
        uid, chat_id, thread_id = _extract_ids(update)

        return bool(
            uid == Config.OWNER_ID
            or (
                uid in user_data
                and (
                    user_data[uid].get("AUTH", False)
                    or user_data[uid].get("SUDO", False)
                )
            )
            or (
                chat_id in user_data
                and user_data[chat_id].get("AUTH", False)
                and (
                    thread_id is None
                    or thread_id in user_data[chat_id].get("thread_ids", [])
                )
            )
            or uid in sudo_users
            or uid in auth_chats
            or (
                chat_id in auth_chats
                and (
                    (auth_chats[chat_id] and thread_id and thread_id in auth_chats[chat_id])
                    or (chat_id in auth_chats and not auth_chats[chat_id])
                )
            )
        )

    authorized = create(authorized_user)

    async def sudo_user(self, _, update):
        uid, _, _ = _extract_ids(update)
        return bool(
            uid == Config.OWNER_ID
            or (uid in user_data and user_data[uid].get("SUDO"))
            or uid in sudo_users
        )

    sudo = create(sudo_user)
