
from re import findall
from pyrogram.filters import command
from ..helper.ext_utils.links_utils import is_url
from ..helper.ext_utils.bot_utils import get_content_type, new_task
from .mirror_leech import leech as _leech
from .ytdlp import ytdl_leech as _ytdl_leech

# Quick heuristics for "direct file" links typically handled by /leech
_DIRECT_EXTS = (
    ".zip",".rar",".7z",".tar",".gz",".bz2",".xz",".zst",
    ".mp4",".mkv",".avi",".mov",".flv",".wmv",".m4v",
    ".mp3",".flac",".wav",".aac",".ogg",".m4a",
    ".apk",".ipa",".exe",".dmg",".iso",".img",".bin",
    ".pdf",".epub",".mobi",".doc",".docx",".xls",".xlsx",".ppt",".pptx"
)

def _looks_like_direct(url: str) -> bool:
    low = url.lower()
    for ext in _DIRECT_EXTS:
        if low.endswith(ext):
            return True
    return False

async def _route_is_direct(url: str) -> bool:
    # Fast path by extension
    if _looks_like_direct(url):
        return True
    # Fallback to HEAD content-type probe (best-effort)
    ctype = await get_content_type(url)
    if not ctype:
        return False
    # Treat common file-ish types as direct
    return any(ctype.startswith(prefix) for prefix in (
        "application/", "video/", "audio/", "image/"
    ))

def _extract_urls(text: str):
    # We accept anything that passes is_url()
    parts = text.split()
    return [p for p in parts if is_url(p)]

@new_task
async def auto_leech(client, message):
    # ignore messages that already start with a command
    if message.text and message.text.strip().startswith('/'):
        return
    if not message.text:
        return
    urls = _extract_urls(message.text)
    if not urls:
        return
    # Process each URL separately
    for url in urls:
        # Clone message with only that URL so downstream parsers receive a clean input
        msg = message
        msg.text = url
        try:
            if await _route_is_direct(url):
                await _leech(client, msg)       # behaves like /l
            else:
                await _ytdl_leech(client, msg)  # behaves like /yl
        except Exception as e:
            # best-effort: report as a reply, but don't stop the loop
            try:
                await message.reply(f"Failed to queue <code>{url}</code>: {e}")
            except:
                pass
