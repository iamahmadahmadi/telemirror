from pyrogram.types import Message

from .. import (
    non_queued_dl,
    non_queued_up,
    queued_dl,
    queued_up,
    queue_dict_lock,
    task_dict,
    task_dict_lock,
)
from ..helper.telegram_helper.message_utils import send_message


def _format_task_row(task_id, task):
    name = getattr(task, "name", getattr(task, "title", "Task"))
    progress = getattr(task, "progress", None)
    pct = "-"
    if isinstance(progress, (int, float)):
        pct = f"{progress * 100:.0f}%"
    elif isinstance(progress, str):
        pct = progress
    state = getattr(task, "status", getattr(task, "state", "running"))
    return f"• {name} (id:{task_id}) — {state} {pct}"


def _queue_rows_for_user(queue_map: dict, user_id: int, label: str):
    rows = []
    for pos, (mid, meta) in enumerate(queue_map.items(), start=1):
        uid = meta.get("user_id") if isinstance(meta, dict) else None
        if uid == user_id:
            rows.append(f"{label} position {pos} — task {mid}")
    return rows


async def queue_cmd(client, message: Message):
    uid = message.from_user.id
    running_rows = []
    pending_rows = []

    async with task_dict_lock:
        for mid, task in task_dict.items():
            task_uid = getattr(task, "user_id", getattr(task, "uid", None))
            if task_uid != uid:
                continue
            row = _format_task_row(mid, task)
            if mid in non_queued_dl or mid in non_queued_up:
                running_rows.append(row)
            else:
                pending_rows.append(f"⏳ Starting — {row}")

    async with queue_dict_lock:
        queued_rows = _queue_rows_for_user(queued_dl, uid, "Download queue")
        queued_rows += _queue_rows_for_user(queued_up, uid, "Upload queue")
        total_waiting = len(queued_dl) + len(queued_up)

    if not (running_rows or pending_rows or queued_rows):
        await send_message(message, "🧺 No active or queued tasks for you right now.")
        return

    lines = ["🧺 Your Queue"]
    if running_rows:
        lines.append("\n🚀 Running:")
        lines.extend(running_rows)
    if pending_rows:
        lines.append("\n⏳ Warming up:")
        lines.extend(pending_rows)
    if queued_rows:
        lines.append("\n🪣 Waiting in queue:")
        lines.extend(queued_rows)
    lines.append(f"\nTotal waiting globally: {total_waiting}")

    await send_message(message, "\n".join(lines[:120]))
