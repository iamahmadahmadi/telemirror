import asyncio
from bot.db import sqlite_db

async def main():
    # Connect (this creates the DB and tables if missing)
    await sqlite_db.connect()

    # Just print a sanity check
    print("Database initialized ✅")

    # Check if we can fetch a user (it should auto-insert)
    user = await sqlite_db.get_user(12345)
    print("Sample user:", user)

if __name__ == "__main__":
    asyncio.run(main())
