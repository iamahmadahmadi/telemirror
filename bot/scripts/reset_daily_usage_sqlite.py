import asyncio
import logging

from bot.db.sqlite_db import reset_daily_usage_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

async def main():
    try:
        updated = await reset_daily_usage_all()
        logging.info(f"✅ Daily usage reset complete. {updated} user(s) updated.")
    except Exception as e:
        logging.error(f"❌ Failed to reset daily usage: {e}")

if __name__ == "__main__":
    asyncio.run(main())
