# premium_shop.py
from typing import Optional, Union, Dict, Tuple
import logging
from datetime import datetime, timezone
import aiohttp
import asyncio

from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from ..helper.telegram_helper.message_utils import send_message, edit_message
from ..db.sqlite_db import set_premium_days, get_user, reward_referrer_on_purchase

LOGGER = logging.getLogger(__name__)

# -------------------- Plans & USD Pricing --------------------
BUTTON_PLANS: Dict[str, int] = {
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "12m": 365,
}
PLAN_PRICES_USD: Dict[str, float] = {
    "1m":  2.00,
    "3m":  5.00,
    "6m":  8.00,
    "12m": 15.00,
}

# -------------------- Lazy Config Helpers --------------------
def _get_cfg(name: str, default: str = "") -> str:
    try:
        from ..core.config_manager import Config  # lazy import, avoid cycles
        return str(getattr(Config, name, default) or default)
    except Exception:
        return default

def _bot_username() -> str:
    return _get_cfg("BOT_USERNAME", "")

def _menu_text() -> str:
    return (
        "💎 Premium\n\n"
        "— Daily usage limit: 60 GB\n"
        "— Video duration: up to 10 hours\n"
        "— Downloading requests in parallel: 10\n"
        "— Increased priority in processing queue\n"
        "— Disabled NSFW filter\n\n"
        "👉 Choose a plan below:"
    )

# -------------------- Crypto Bot (pay.crypt.bot) --------------------
CRYPTO_PAY_TOKEN = _get_cfg("CRYPTO_PAY_TOKEN", "166461:AAnJqbkNIdfbzaXO4swVoAYIjRepGJMAv5N")
CRYPTO_API       = "https://pay.crypt.bot/api"

CRYPTO_INVOICE_DEFAULTS = {
    "currency_type": "fiat",                         # show fiat price
    "fiat": "USD",                                   # USD
    "accepted_assets": "USDT,TON,BTC,ETH,LTC,BNB,TRX,USDC",
    "allow_comments": True,
    "allow_anonymous": True,
    # "expires_in": 900,  # optional, seconds
}

async def _crypto_create_invoice(*, user_id: int, plan: str, days: int, amount_usd: float) -> Tuple[bool, Optional[str], str]:
    """
    Create a Crypto Bot invoice for one plan.
    Returns (ok, pay_url_or_none, error_message_if_any).
    """
    if not CRYPTO_PAY_TOKEN:
        return False, None, "Crypto payments are temporarily unavailable."

    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    url = f"{CRYPTO_API}/createInvoice"

    paid_btn_url = f"https://t.me/{_bot_username()}" if _bot_username() else "https://t.me/leechflixbot"

    payload = {
        "currency_type": CRYPTO_INVOICE_DEFAULTS["currency_type"],
        "fiat":          CRYPTO_INVOICE_DEFAULTS["fiat"],
        "accepted_assets": CRYPTO_INVOICE_DEFAULTS["accepted_assets"],
        "amount": f"{amount_usd:.2f}",
        "description": f"Premium {plan} ({days} days) for user {user_id}",
        "hidden_message": "Thanks! You will be upgraded automatically in a few minutes.",
        "paid_btn_name": "openBot",
        "paid_btn_url": paid_btn_url,
        "payload": f"uid={user_id};plan={plan};days={days}",
        # "expires_in": CRYPTO_INVOICE_DEFAULTS.get("expires_in"),
    }

    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.post(url, data=payload) as resp:
                raw = await resp.text()
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    LOGGER.error("createInvoice non-JSON: %s", raw[:400])
                    return False, None, "Payment server returned invalid response."

        if not data or not data.get("ok"):
            err = (data or {}).get("error", "unknown_error")
            return False, None, f"Failed to create invoice: {err}"

        pay_url = (data.get("result") or {}).get("pay_url")
        if not pay_url:
            return False, None, "Payment link is unavailable."
        return True, pay_url, ""
    except Exception as e:
        LOGGER.exception("createInvoice failed: %s", e)
        return False, None, f"Failed to create invoice: {e}"

# -------------------- Menus with Invoice Links --------------------
async def _build_invoice_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """
    Creates 4 invoices (1m/3m/6m/12m) and returns an inline keyboard with direct URL buttons.
    If any plan fails to create, it is skipped. Includes a Refresh button.
    """
    rows = []
    tasks = []
    # Launch in parallel for snappier UX
    for code, days in BUTTON_PLANS.items():
        amount = PLAN_PRICES_USD.get(code)
        if amount is None:
            continue
        tasks.append(_crypto_create_invoice(user_id=user_id, plan=code, days=days, amount_usd=amount))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Map back results to codes in same order:
    ordered_codes = list(BUTTON_PLANS.keys())
    i = 0
    for code in ordered_codes:
        if code not in PLAN_PRICES_USD:
            continue
        res = results[i]
        i += 1

        label = {
            "1m":  "1 month – $2",
            "3m":  "3 months – $5 (15% Off)",
            "6m":  "6 months – $8 (30% Off)",
            "12m": "12 months – $15 (50% Off)",
        }[code]

        if isinstance(res, Exception):
            LOGGER.warning("Invoice error for %s: %s", code, res)
            # Skip this button if creation failed
            continue

        ok, url, err = res
        if ok and url:
            rows.append([InlineKeyboardButton(label, url=url)])
        else:
            LOGGER.warning("Invoice create failed for %s: %s", code, err)

    # Always provide a refresh (to regenerate fresh invoices if ones expired)
    #rows.append([InlineKeyboardButton("🔁 Refresh payment links", callback_data="premium_refresh")])
    return InlineKeyboardMarkup(rows)

# -------------------- Public entry points --------------------
async def _send_invoice_menu(target: Union[Message, CallbackQuery], *, replace: bool = False) -> None:
    """
    Build four invoices for the current user and render the Premium menu with direct pay URLs.
    """
    if isinstance(target, CallbackQuery):
        uid = target.from_user.id
    else:
        uid = target.from_user.id if getattr(target, "from_user", None) else target.chat.id

    kb = await _build_invoice_keyboard(uid)
    text = _menu_text()

    if isinstance(target, CallbackQuery):
        if replace:
            await edit_message(target.message, text, kb)
        else:
            await send_message(target.message, text, kb)
        try:
            await target.answer()
        except Exception:
            pass
        return

    await send_message(target, text, kb)

def _normalize_buy_arg(arg: str) -> Optional[str]:
    arg = (arg or "").strip().lower()
    if not arg:
        return None
    if arg in BUTTON_PLANS:
        return arg
    # accept legacy names, map them if you want:
    legacy = {
        "1mo": "1m", "3mo": "3m", "6mo": "6m", "12mo": "12m",
        "1month": "1m", "3months": "3m", "6months": "6m", "12months": "12m",
    }
    return legacy.get(arg)

async def _send_single_invoice(message: Message, code: str) -> None:
    """
    For /buy 1m (etc.) — create ONE invoice & show that single link.
    """
    uid = message.from_user.id if getattr(message, "from_user", None) else message.chat.id
    days = BUTTON_PLANS.get(code)
    amount = PLAN_PRICES_USD.get(code)
    if days is None or amount is None:
        await send_message(message, "Unknown plan.")
        return

    await send_message(message, f"⏳ Creating invoice for {days} days (${amount:.2f})…")
    ok, url, err = await _crypto_create_invoice(user_id=uid, plan=code, days=days, amount_usd=amount)
    if not ok or not url:
        await send_message(message, f"{err or 'Could not create invoice.'}")
        return

    label = {
        "1m":  "1 month – $2",
        "3m":  "3 months – $5 (15% Off)",
        "6m":  "6 months – $8 (30% Off)",
        "12m": "12 months – $15 (50% Off)",
    }[code]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(label, url=url)],
                               [InlineKeyboardButton("⬅️ Back", callback_data="premium_open")]])
    await send_message(
        message,
        (
            "💎 Premium\n\n"
            "👉 Your payment link is ready:"
        ),
        kb
    )

# Main entry (bind your MessageHandler/CallbackQueryHandler to this)
async def buy_premium(client, update: Union[Message, CallbackQuery]):
    """
    - /premium or /buy (no args) → generate 4 invoices & show URL buttons
    - /buy <1m|3m|6m|12m>       → generate a single invoice & show URL button
    - Callback:
        premium_open            → regenerate 4 invoices & edit message
        premium_refresh         → regenerate 4 invoices & edit message
    """
    if isinstance(update, CallbackQuery):
        data = (update.data or "").strip().lower()
        if data in {"premium_open", "premium_refresh"}:
            await _send_invoice_menu(update, replace=True)
            try: await update.answer()
            except Exception: pass
            return

        # Unknown callback → simply reopen the menu
        await _send_invoice_menu(update, replace=True)
        try: await update.answer()
        except Exception: pass
        return

    # Message path
    msg = update
    parts = (msg.text or "").split(maxsplit=1)
    cmd = parts[0].lstrip("/").lower() if parts else ""

    # /premium or /buy without arg → full invoice menu
    if cmd in {"premium", "buypremium", "buy"} and len(parts) == 1:
        await _send_invoice_menu(msg)
        return

    # /buy <plan>
    if cmd in {"buy", "buypremium"} and len(parts) == 2:
        code = _normalize_buy_arg(parts[1])
        if code:
            await _send_single_invoice(msg, code)
            return
        # Unknown arg → fall back to full menu
        await _send_invoice_menu(msg)
        return

    # Fallback → show premium menu
    await _send_invoice_menu(msg)

# Optional: admin helper unchanged, left available if you still use it
async def set_premium_cmd(client, message: Message):
    """
    Sudo/admin helper:
      /setpremium 31
    Grants the caller N days and fires referral reward if applicable.
    (Not used by the Crypto Bot flow; webhook will upgrade users later.)
    """
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await send_message(message, "Usage: /setpremium <days>")
        return

    days = int(parts[1])
    uid = message.from_user.id
    await set_premium_days(uid, days)
    await reward_referrer_on_purchase(uid, days)
    user = await get_user(uid)
    await send_message(message, f"✅ Premium set for {user['user_id']} — {days} days.")
