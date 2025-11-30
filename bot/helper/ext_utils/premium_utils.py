from .quota_manager import get_usage, get_limits

_NSWF_DOMAINS = (
    "porn", "xvideos", "xhamster", "redtube", "xnxx",
    "onlyfans", "adult", "rule34", "erome", "jav", "e621",
)

async def check_user_quota(user_id: int) -> bool:
    used = await get_usage(user_id)
    _, limit = await get_limits(user_id)
    return float(used) < float(limit)

async def has_nsfw_access(user_id: int) -> bool:
    prem, _ = await get_limits(user_id)
    return bool(prem)

async def is_nsfw(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return any(k in u for k in _NSWF_DOMAINS)
