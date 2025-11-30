import asyncio, os, json, shlex, tempfile

async def _run(*cmd):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(map(shlex.quote, cmd))}\n{err.decode(errors='ignore')}"
        )
    return out

async def probe_media(path: str) -> dict:
    out = await _run(
        "ffprobe","-v","error",
        "-show_entries","format=duration:stream=codec_type,codec_name,width,height",
        "-of","json", path
    )
    return json.loads(out or "{}")

async def ensure_music_audio(input_path: str, prefer: str = "mp3") -> tuple[str, int | None]:
    """
    Ensure input is a Telegram-music-friendly audio file.
    Returns (output_path, duration_seconds).
    prefer: "mp3" (libmp3lame) or "m4a" (aac)
    """
    ip = input_path
    ext = os.path.splitext(ip)[1].lower()
    # already music-friendly?
    if ext in (".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav"):
        meta = await probe_media(ip)
        dur = None
        try:
            dur = int(float(meta.get("format", {}).get("duration", 0)))
        except Exception:
            pass
        return ip, dur

    base, _ = os.path.splitext(ip)
    if prefer == "m4a":
        op = base + ".m4a"
        # aac in m4a
        await _run("ffmpeg","-y","-i", ip, "-vn", "-c:a","aac","-b:a","192k", op)
    else:
        op = base + ".mp3"
        # mp3
        await _run("ffmpeg","-y","-i", ip, "-vn", "-c:a","libmp3lame","-q:a","2", op)

    meta = await probe_media(op)
    dur = None
    try:
        dur = int(float(meta.get("format", {}).get("duration", 0)))
    except Exception:
        pass
    return op, dur

async def ensure_voice_ogg(input_path: str) -> tuple[str, int | None]:
    """
    Convert to OGG/Opus suitable for send_voice (voice messages).
    """
    base, _ = os.path.splitext(input_path)
    op = base + ".ogg"
    await _run(
        "ffmpeg","-y","-i", input_path,
        "-vn","-c:a","libopus","-b:a","64k",
        "-application","voip", op
    )
    meta = await probe_media(op)
    dur = None
    try:
        dur = int(float(meta.get("format", {}).get("duration", 0)))
    except Exception:
        pass
    return op, dur
