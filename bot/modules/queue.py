from pyrogram.types import Message
from .. import task_dict_lock, task_dict  # your existing global task registry
from ..helper.telegram_helper.message_utils import send_message

def _format_task_row(t):
    # Customize based on your task structure
    name = getattr(t, "name", "Task")
    prog = getattr(t, "progress", None)
    pct = f"{int(prog*100)}%" if isinstance(prog, (int, float)) else "-"
    return f"• {name} — {pct}"

async def queue_cmd(client, message: Message):
    uid = message.from_user.id
    rows = []

    async with task_dict_lock:
        # Adapt to your actual structures; fall back gracefully
        # Here we assume task_dict maps task_id -> task_obj with user_id
        for _, task in task_dict.items():
            if getattr(task, "user_id", None) == uid:
                rows.append(_format_task_row(task))

    if not rows:
        await send_message(message, "🧺 Queue is empty.")
        return

    text = "🧺 Your Queue:\n" + "\n".join(rows[:50])  # cap display
    await send_message(message, text)
