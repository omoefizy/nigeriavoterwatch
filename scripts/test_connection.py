"""Quick MongoDB connectivity check — run from the backend/ directory."""
import asyncio
import sys
import os

# Allow running from project root or backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import motor.motor_asyncio
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

MONGO_URI = os.environ["MONGODB_URI"]
DB_NAME   = os.environ.get("MONGODB_DB_NAME", "nigeriavoterwatch")


async def main():
    print(f"Connecting to Atlas … (db: {DB_NAME})")
    client = motor.motor_asyncio.AsyncIOMotorClient(
        MONGO_URI,
        serverSelectionTimeoutMS=8000,
    )
    try:
        info = await client.server_info()
        print(f"  version : {info.get('version', '?')}")
        db = client[DB_NAME]
        collections = await db.list_collection_names()
        print(f"  database: {DB_NAME}")
        print(f"  collections ({len(collections)}): {collections or '(none yet)'}")
        print("\nMongoDB connection OK")
    except Exception as exc:
        print(f"\nCONNECTION FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


asyncio.run(main())
