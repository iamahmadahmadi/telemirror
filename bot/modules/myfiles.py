from pyrogram.types import CallbackQuery
from ..helper.ext_utils.bot_utils import new_task
from ..db.sqlite_db import get_files_by_user_paged, get_files_count_by_user
from ..helper.telegram_helper.message_utils import send_message, edit_message

PAGE_SIZE_DEFAULT = 10

# --- replace _page_text function with this version ---
# --- replace _page_text function with this version ---
def _page_text(rows, page: int, page_size: int, total: int) -> str:
    if not rows:
        return "No files found."
    start = (page - 1) * page_size + 1
    lines = []
    for idx, row in enumerate(rows, start=start):
        qual = f" ({row['quality']})" if row.get("quality") else ""
        # Short ID = stable prefix of file_id (20 chars)
        fid = row["file_id"]
        short = fid[:20]
        lines.append(f"{idx}. {row['name']}{qual} — /get_{short}")
    footer = f"\nPage {page} • {total} files"
    return "Your files:\n" + "\n".join(lines) + footer


def _nav_kb(page: int, page_size: int, total: int):
    from ..helper.telegram_helper.button_build import ButtonMaker
    bm = ButtonMaker()
    max_page = max(1, (total + page_size - 1) // page_size)
    added = 0
    if page > 1:
        bm.data_button("◀️ Prev", f"myfiles:p={page-1}")
        added += 1
    if page < max_page:
        bm.data_button("Next ▶️", f"myfiles:p={page+1}")
        added += 1
    return bm.build_menu(2) if added else None

@new_task
async def my_files(_, message):
    uid = message.from_user.id
    # allow `/myfiles <page>`
    try:
        parts = (message.text or "").split()
        page = int(parts[1]) if len(parts) > 1 else 1
    except Exception:
        page = 1
    page = max(1, page)

    total = await get_files_count_by_user(uid)
    rows = await get_files_by_user_paged(uid, page=page, page_size=PAGE_SIZE_DEFAULT)
    text = _page_text(rows, page, PAGE_SIZE_DEFAULT, total)
    kb = _nav_kb(page, PAGE_SIZE_DEFAULT, total)
    await send_message(message, text, kb)

async def my_files_page(_, query: CallbackQuery):
    """Callback handler: myfiles:p=<n>"""
    await query.answer()
    uid = query.from_user.id
    try:
        data = query.data or "myfiles:p=1"
        page = int(data.split("=", 1)[-1]) if "=" in data else 1
    except Exception:
        page = 1
    page = max(1, page)

    total = await get_files_count_by_user(uid)
    rows = await get_files_by_user_paged(uid, page=page, page_size=PAGE_SIZE_DEFAULT)
    text = _page_text(rows, page, PAGE_SIZE_DEFAULT, total)
    kb = _nav_kb(page, PAGE_SIZE_DEFAULT, total)
    await edit_message(query.message, text, kb)
