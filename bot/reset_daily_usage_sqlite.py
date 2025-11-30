import asyncio
from bot.db.sqlite_db import reset_daily_usage_all

if __name__ == "__main__":
    asyncio.run(reset_daily_usage_all())
