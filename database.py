"""MongoDB operations for storing message metadata."""

from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

from config import MONGODB_URI, MONGODB_DB, MONGODB_COLLECTION


class Database:
    def __init__(self):
        self.client = AsyncIOMotorClient(MONGODB_URI)
        self.db = self.client[MONGODB_DB]
        self.collection = self.db[MONGODB_COLLECTION]

    async def save_message(self, data: dict[str, Any]) -> str:
        """Insert message metadata and return inserted id."""
        data["_created_at"] = datetime.now().replace(microsecond=0, tzinfo=None)
        result = await self.collection.insert_one(data)
        return str(result.inserted_id)

    async def message_exists(self, channel: str, message_id: int) -> bool:
        """Check if we already processed this message (avoid duplicates)."""
        doc = await self.collection.find_one({
            "channel": channel,
            "$or": [
                {"source_message_id": message_id},
                {"source_message_ids": message_id},
            ]
        })
        return doc is not None

    async def grouped_exists(self, channel: str, grouped_id: int) -> bool:
        """Check if we already processed this album (by grouped_id)."""
        doc = await self.collection.find_one({
            "channel": channel,
            "grouped_id": grouped_id,
        })
        return doc is not None

    async def get_last_source_message_id(self, channel: str) -> int | None:
        """
        Return the highest source_message_id we have stored for this channel.

        Used to only fetch messages that are newer than what is already in DB.
        """
        doc = await self.collection.find_one(
            {
                "channel": channel,
                "source_message_id": {"$exists": True},
            },
            sort=[("source_message_id", -1)],
            projection={"source_message_id": 1, "_id": 0},
        )
        if not doc:
            return None
        return int(doc["source_message_id"])

    async def close(self):
        self.client.close()


# Singleton instance
db = Database()
