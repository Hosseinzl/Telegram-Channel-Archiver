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

    async def update_forwarded_message_by_source(self, source_chat_id, source_msg_id, **kwargs):
        """
        Finds a record by source_chat_id and source_msg_id and updates it.
        """
        # اطمینان از اینکه هر دو عدد هستند (نه استرینگ)
        query = {
            "source_chat_id": int(source_chat_id),
            "source_msg_id": int(source_msg_id)
        }
        
        # چاپ کوئری برای دیباگ (اختیاری - بعد از تست پاک کنید)
        # logger.debug(f"Searching DB with query: {query}")
        
        result = await self.db.messages.update_one(
            query,
            {"$set": kwargs}
        )
        
        return result.modified_count > 0
    async def update_forwarded_message(
        self,
        forwarded_message_id: int,
        text: str,
        file_ids: dict[str, Any],
        state: str,
    ) -> bool:
        """
        Update a message document by forwarded_message_id with text, file_ids, and state.
        
        For albums, also checks if the message_id is in forwarded_message_ids array.
        For albums, merges file IDs instead of replacing them.
        
        Returns True if a document was updated, False if not found.
        """
        # Try exact match first
        doc = await self.collection.find_one({"forwarded_message_id": forwarded_message_id})
        
        if not doc:
            # For albums: check if this message_id is in the forwarded_message_ids array
            doc = await self.collection.find_one({"forwarded_message_ids": forwarded_message_id})
        
        if not doc:
            return False
        
        # For albums: merge file IDs instead of replacing
        existing_file_ids = doc.get("file_ids", {})
        merged_file_ids = existing_file_ids.copy() if existing_file_ids else {}
        
        # Merge file IDs: append lists, replace singles
        for key, value in file_ids.items():
            if value:  # Only merge non-empty values
                if key == "photo_file_ids" and isinstance(value, list):
                    # Merge photo lists (avoid duplicates)
                    existing_photos = merged_file_ids.get("photo_file_ids", [])
                    merged_file_ids["photo_file_ids"] = list(set(existing_photos + value))
                elif isinstance(value, (str, int)) and value:
                    # Replace single file IDs (video, audio, etc.)
                    merged_file_ids[key] = value
                elif isinstance(value, list) and value:
                    # For other lists, merge
                    existing = merged_file_ids.get(key, [])
                    merged_file_ids[key] = list(set(existing + value))
        
        # Use provided text if it's longer (for albums, later messages might have captions)
        final_text = text if len(text) > len(doc.get("text", "")) else doc.get("text", text)
        
        update_data = {
            "text": final_text,
            "text_length": len(final_text),
            "file_ids": merged_file_ids,
            "state": state,
            "bot_processed_at": datetime.now().replace(microsecond=0, tzinfo=None),
        }
        
        result = await self.collection.update_one(
            {"_id": doc["_id"]},
            {"$set": update_data}
        )
        
        return result.modified_count > 0

    async def close(self):
        self.client.close()


# Singleton instance
db = Database()
