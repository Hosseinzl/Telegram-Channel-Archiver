"""Async MongoDB client wrapping Motor (AsyncIOMotorClient)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import structlog
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = structlog.get_logger(__name__)

from config import MONGODB_URI, MONGODB_DB, MONGODB_COLLECTION

class Database:
    """Async MongoDB client.

    Usage::

        await db.connect()
        # ... use db ...
        await db.close()
    """

    def __init__(self) -> None:
        self._client: AsyncIOMotorClient | None = None
        self._db: AsyncIOMotorDatabase | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the Motor connection pool and verify connectivity."""

        logger.info("Connecting to MongoDB", uri=MONGODB_URI, db=MONGODB_DB)
        self._client = AsyncIOMotorClient(MONGODB_URI)
        self._db = self._client[MONGODB_DB]
        # Fail fast if the server is unreachable
        await self._client.admin.command("ping")
        logger.info("MongoDB connection established")
        await self._ensure_indexes()

    async def close(self) -> None:
        """Close the Motor connection pool."""
        if self._client is not None:
            self._client.close()
            self._client = None
            self._db = None
            logger.info("MongoDB connection closed")

    async def _ensure_indexes(self) -> None:
        """Create indexes required for efficient queries."""
        await self.fetched_messages.create_index(
            [("source_chat_id", 1), ("source_msg_id", 1)], unique=True
        )
        await self.news_items.create_index(
            [("source_chat_id", 1), ("source_msg_id", 1)]
        )
        await self.news_items.create_index([("state", 1)])
        await self.admin_users.create_index([("telegram_id", 1)], unique=True)
        await self.source_channels.create_index([("channel_id", 1)], unique=True)
        await self.destination_channels.create_index([("channel_id", 1)], unique=True)
        await self.ai_prompts.create_index([("operation", 1)], unique=True)
        logger.debug("MongoDB indexes ensured")

    # ── Collection properties ─────────────────────────────────────────────────

    def _require_db(self) -> AsyncIOMotorDatabase:
        if self._db is None:
            raise RuntimeError("Database.connect() has not been called yet")
        return self._db

    @property
    def fetched_messages(self):
        return self._require_db()["fetched_messages"]

    @property
    def news_items(self):
        return self._require_db()["news_items"]

    @property
    def admin_users(self):
        return self._require_db()["admin_users"]

    @property
    def source_channels(self):
        return self._require_db()["source_channels"]

    @property
    def destination_channels(self):
        return self._require_db()["destination_channels"]

    @property
    def ai_prompts(self):
        return self._require_db()["ai_prompts"]

    @property
    def messages(self):
        return self._require_db()[MONGODB_COLLECTION]

    # ── Health ────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """Return True if MongoDB is reachable, False otherwise."""
        try:
            if self._client is None:
                return False
            await self._client.admin.command("ping")
            return True
        except Exception:
            logger.warning("MongoDB ping failed", exc_info=True)
            return False

    # ── Legacy archive operations ───────────────────────────────────────────

    async def save_message(self, data: dict[str, Any]) -> str:
        data["_created_at"] = datetime.now(timezone.utc).replace(microsecond=0)

        result = await self.messages.update_one(
            {
                "source_chat_id": data["source_chat_id"],
                "source_msg_id": data["source_msg_id"],
            },
            {
                "$setOnInsert": data
            },
            upsert=True,
        )

        if result.upserted_id:
            return str(result.upserted_id)

        existing = await self.messages.find_one(
            {
                "source_chat_id": data["source_chat_id"],
                "source_msg_id": data["source_msg_id"],
            },
            {"_id": 1},
        )

        return str(existing["_id"]) if existing else ""

    async def message_exists(self, channel_id: str, message_id: int) -> bool:
        """Check if a channel message has already been processed."""

        doc = await self.messages.find_one(
            {
                "$and": [
                    {
                        "$or": [
                            {"channel_id": channel_id},
                            {"channel": channel_id},
                        ]
                    },
                    {
                        "$or": [
                            {"telegram_message_id": message_id},
                            {"telegram_message_ids": message_id},
                            {"source_message_id": message_id},
                            {"source_message_ids": message_id},
                        ]
                    },
                ]
            }
        )
        return doc is not None


    async def grouped_exists(self, channel_id: str, grouped_id: int) -> bool:
        """Check if an album has already been processed."""

        doc = await self.messages.find_one(
            {
                "$and": [
                    {
                        "$or": [
                            {"channel_id": channel_id},
                            {"channel": channel_id},
                        ]
                    },
                    {"grouped_id": grouped_id},
                ]
            }
        )
        return doc is not None

    async def get_last_source_message_id(self, channel_id: str) -> int | None:
        """Return the highest stored source_message_id for a channel."""

        doc = await self.messages.find_one(
            {
                "$and": [
                    {
                        "$or": [
                            {"channel_id": channel_id},
                            {"channel": channel_id},
                        ]
                    },
                    {"source_message_id": {"$exists": True}},
                ]
            },
            sort=[("source_message_id", -1)],
            projection={"source_message_id": 1, "_id": 0},
        )
        if not doc:
            return None
        return int(doc["source_message_id"])


    async def get_last_telegram_message_id(self, channel_id: str) -> int | None:
        """Return the highest stored telegram_message_id for a channel."""

        doc = await self.messages.find_one(
            {
                "$and": [
                    {
                        "$or": [
                            {"channel_id": channel_id},
                            {"channel": channel_id},
                        ]
                    },
                    {"telegram_message_id": {"$exists": True}},
                ]
            },
            sort=[("telegram_message_id", -1)],
            projection={"telegram_message_id": 1, "_id": 0},
        )
        if not doc:
            return None
        return int(doc["telegram_message_id"])
    
    # ── FetchedMessage operations ─────────────────────────────────────────────

    async def update_forwarded_message_by_source(
        self,
        source_chat_id: int,
        source_msg_id: int,
        file_ids: dict | None = None,
        bot_text: str | None = None,
        last_bot_msg_id: int | None = None,
        state: str | None = None,
    ) -> dict | None:
        """Upsert a FetchedMessage document identified by source coordinates.

        Creates the document if it does not exist; updates only the provided fields.
        Returns the updated document.
        """
        now = datetime.now(timezone.utc)
        set_fields: dict[str, Any] = {"updated_at": now}

        if file_ids is not None:
            set_fields["file_ids"] = file_ids
        if bot_text is not None:
            set_fields["bot_text"] = bot_text
        if last_bot_msg_id is not None:
            set_fields["last_bot_msg_id"] = last_bot_msg_id
        if state is not None:
            set_fields["state"] = state

        result = await self.fetched_messages.find_one_and_update(
            {"source_chat_id": source_chat_id, "source_msg_id": source_msg_id},
            {
                "$set": set_fields,
                "$setOnInsert": {
                    "source_chat_id": source_chat_id,
                    "source_msg_id": source_msg_id,
                    "fetched_at": now,
                },
            },
            upsert=True,
            return_document=True,
        )
        return result

    # ── NewsItem operations ───────────────────────────────────────────────────

    async def get_or_create_news_item(
        self, source_chat_id: int, source_msg_id: int
    ) -> dict:
        """Return an existing NewsItem or create a new one for the given source coordinates."""
        now = datetime.now(timezone.utc)
        doc = await self.news_items.find_one_and_update(
            {"source_chat_id": source_chat_id, "source_msg_id": source_msg_id},
            {
                "$setOnInsert": {
                    "source_chat_id": source_chat_id,
                    "source_msg_id": source_msg_id,
                    "state": "new",
                    "media": [],
                    "publications": [],
                    "created_at": now,
                    "updated_at": now,
                }
            },
            upsert=True,
            return_document=True,
        )
        return doc  # type: ignore[return-value]


    async def update_news_item(
        self,
        news_id: str,
        update_data: dict,
    ) -> dict | None:
        """Partially update a NewsItem by its _id."""

        try:
            update_data = {
                **update_data,
                "updated_at": datetime.now(timezone.utc),
            }

            object_id = ObjectId(news_id)

            result = await self.news_items.update_one(
                {"_id": object_id},
                {"$set": update_data},
            )

            logger.info(
                "db.update_news_item",
                news_id=news_id,
                matched_count=result.matched_count,
                modified_count=result.modified_count,
                update_fields=list(update_data.keys()),
            )

            if result.matched_count == 0:
                logger.warning(
                    "db.update_news_item_not_found",
                    news_id=news_id,
                )
                return None

            # Return the updated document.
            return await self.news_items.find_one(
                {"_id": object_id}
            )

        except Exception:
            logger.exception(
                "db.update_news_item_error",
                news_id=news_id,
            )
            return None


    async def get_news_item(self, news_id: str) -> dict | None:
        """Fetch a single NewsItem by its ``_id``."""
        return await self.news_items.find_one({"_id": ObjectId(news_id)})


    async def unset_news_fields(
        self,
        news_id: str,
        fields: list[str],
    ) -> dict | None:
        """Remove fields from a NewsItem."""

        try:
            result = await self.news_items.find_one_and_update(
                {"_id": ObjectId(news_id)},
                {
                    "$unset": {field: "" for field in fields},
                    "$set": {
                        "updated_at": datetime.now(timezone.utc),
                    },
                },
                return_document=True,
            )

            return result

        except Exception:
            logger.exception(
                "db.unset_news_fields_error",
                news_id=news_id,
                fields=fields,
            )
            return None

    # ── AdminUser operations ──────────────────────────────────────────────────

    async def get_admin_user(self, telegram_id: int) -> dict | None:
        """Fetch an AdminUser document by Telegram user ID."""
        return await self.admin_users.find_one({"telegram_id": telegram_id})

    async def add_admin_user(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        added_by: int,
        role: str = "admin",
    ) -> dict:
        """Insert or reactivate an AdminUser. Returns the upserted document."""
        now = datetime.now(timezone.utc)
        doc = await self.admin_users.find_one_and_update(
            {"telegram_id": telegram_id},
            {
                "$set": {
                    "username": username,
                    "role": role,
                    "added_by": added_by,
                    "is_active": True,
                },
                "$setOnInsert": {
                    "telegram_id": telegram_id,
                    "added_at": now,
                },
            },
            upsert=True,
            return_document=True,
        )
        return doc  # type: ignore[return-value]

    async def remove_admin_user(self, telegram_id: int) -> bool:
        """Soft-delete an AdminUser by setting ``is_active=False``.

        Returns True if a document was matched.
        """
        result = await self.admin_users.update_one(
            {"telegram_id": telegram_id}, {"$set": {"is_active": False}}
        )
        return result.matched_count > 0

    async def update_admin_identity(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> bool:
        """Update cached Telegram identity fields when they change."""

        result = await self.admin_users.update_one(
            {"telegram_id": telegram_id},
            {
                "$set": {
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                }
            },
        )

        return result.modified_count > 0

    async def list_admin_users(self, active_only: bool = True) -> list[dict]:
        """Return all admin users, optionally filtering to active ones only."""
        query: dict[str, Any] = {}
        if active_only:
            query["is_active"] = True
        cursor = self.admin_users.find(query)
        return await cursor.to_list(length=None)

    # ── AIPrompt operations ───────────────────────────────────────────────────

    async def get_ai_prompt(self, operation: str) -> str | None:
        """Return the prompt text for the given operation, or None if not set."""
        doc = await self.ai_prompts.find_one({"operation": operation})
        return doc["prompt_text"] if doc else None

    async def set_ai_prompt(
        self, operation: str, prompt_text: str, updated_by: int
    ) -> None:
        """Upsert the AIPrompt document for the given operation."""
        now = datetime.now(timezone.utc)
        await self.ai_prompts.update_one(
            {"operation": operation},
            {
                "$set": {
                    "prompt_text": prompt_text,
                    "updated_by": updated_by,
                    "updated_at": now,
                }
            },
            upsert=True,
        )

    # ── SourceChannel operations ──────────────────────────────────────────────

    async def list_source_channels(
        self,
        active_only: bool = True,
        validation_status: str | None = None,
    ) -> list[dict]:
        """Return all source channels."""
        query: dict[str, Any] = {"is_active": True} if active_only else {}
        if validation_status is not None:
            query["validation_status"] = validation_status
        return await self.source_channels.find(query).to_list(length=None)

    async def add_source_channel(
        self,
        channel_id: str,
        channel_username: str | None,
        title: str | None,
        validation_status: str,
        added_by: int,
    ) -> dict:
        """Add or reactivate a source channel."""

        now = datetime.now(timezone.utc)

        doc = await self.source_channels.find_one_and_update(
            {"channel_id": channel_id},
            {
                "$set": {
                    "channel_username": channel_username,
                    "title": title,
                    "validation_status": validation_status,
                    "is_active": True,
                },
                "$setOnInsert": {
                    "channel_id": channel_id,
                    "added_by": added_by,
                    "added_at": now,
                },
            },
            upsert=True,
        )

        return doc  # type: ignore[return-value]

    async def update_source_channel_status(
        self,
        channel_id: str,
        *,
        channel_username: str | None,
        title: str | None,
        validation_status: str | None = None,
        is_active: bool | None = None,
    ) -> bool:
        """Refresh source channel metadata."""

        update_fields: dict[str, Any] = {
            "channel_username": channel_username,
            "title": title,
            "validation_status": validation_status,
            "is_active": is_active,
        }
        update_fields = {k: v for k, v in update_fields.items() if v is not None}
        if not update_fields:
            return False

        result = await self.source_channels.update_one(
            {"channel_id": channel_id},
            {"$set": update_fields},
        )
        return result.modified_count > 0

    async def remove_source_channel(self, channel_id: str) -> bool:
        """Soft-delete a SourceChannel. Returns True if matched."""
        result = await self.source_channels.update_one(
            {"channel_id": channel_id}, {"$set": {"is_active": False}}
        )
        return result.matched_count > 0

    # ── DestinationChannel operations ─────────────────────────────────────────

    async def list_destination_channels(
        self,
        active_only: bool = True,
    ) -> list[dict]:
        """Return destination channels."""
        query: dict[str, Any] = {"is_active": True} if active_only else {}

        return await self.destination_channels.find(query).to_list(length=None)

    async def get_destination_channel(
        self,
        channel_id: str,
    ) -> dict | None:
        """Return a destination channel by Telegram channel ID."""

        return await self.destination_channels.find_one(
            {"channel_id": channel_id}
        )

    async def add_destination_channel(
        self,
        channel_id: str,
        channel_username: str | None,
        title: str | None,
        bot_status: str,
        is_bot_member: bool,
        is_bot_admin: bool,
        can_post_messages: bool,
        validation_status: str,
        added_by: int,
    ) -> bool:
        """Add or reactivate a destination channel."""

        now = datetime.now(timezone.utc)

        history_entry = {
            "action": "added",
            "performed_by": added_by,
            "performed_at": now,
        }

        result = await self.destination_channels.update_one(
            {"channel_id": channel_id},
            {
                "$set": {
                    "channel_username": channel_username,
                    "title": title,
                    "bot_status": bot_status,
                    "is_bot_member": is_bot_member,
                    "is_bot_admin": is_bot_admin,
                    "can_post_messages": can_post_messages,
                    "validation_status": validation_status,
                    "is_active": True,
                },
                "$setOnInsert": {
                    "channel_id": channel_id,
                },
                "$push": {
                    "history": history_entry,
                },
            },
            upsert=True,
        )

        return result.acknowledged

    async def remove_destination_channel(
        self,
        channel_id: str,
        removed_by: int,
    ) -> bool:
        """Soft-delete a destination channel and record the action."""

        now = datetime.now(timezone.utc)

        result = await self.destination_channels.update_one(
            {
                "channel_id": channel_id,
                "is_active": True,
            },
            {
                "$set": {
                    "is_active": False,
                },
                "$push": {
                    "history": {
                        "action": "removed",
                        "performed_by": removed_by,
                        "performed_at": now,
                    },
                },
            },
        )

        return result.modified_count > 0

    async def update_destination_channel_status(
        self,
        channel_id: str,
        *,
        channel_username: str | None,
        title: str | None,
        bot_status: str,
        is_bot_member: bool,
        is_bot_admin: bool,
        can_post_messages: bool,
        validation_status: str,
    ) -> bool:
        """Update the current Telegram state of a destination channel."""

        update_fields = {
            "channel_username": channel_username,
            "title": title,
            "bot_status": bot_status,
            "is_bot_member": is_bot_member,
            "is_bot_admin": is_bot_admin,
            "can_post_messages": can_post_messages,
            "validation_status": validation_status,
            "last_checked_at": datetime.now(timezone.utc),
        }

        update_fields = {
            k: v
            for k, v in update_fields.items()
            if v is not None
        }

        result = await self.destination_channels.update_one(
            {"channel_id": channel_id},
            {"$set": update_fields},
        )

        return result.modified_count > 0

    async def update_destination_channel_identity(
        self,
        channel_id: str,
        *,
        channel_username: str | None,
        title: str | None,
    ) -> bool:
        """Update only the destination channel identity fields."""

        update_fields = {
            "channel_username": channel_username,
            "title": title,
        }

        result = await self.destination_channels.update_one(
            {"channel_id": channel_id},
            {"$set": update_fields},
        )

        return result.modified_count > 0

    # ── Aggregate / stats ─────────────────────────────────────────────────────

    async def get_stats(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        """Return management-oriented NewsHub statistics."""

        # ── Optional date filter ──────────────────────────────────────────

        date_filter: dict[str, Any] = {}

        if start_date and end_date:
            date_filter = {
                "created_at": {
                    "$gte": start_date,
                    "$lte": end_date,
                }
            }

        # ── Basic news statistics ─────────────────────────────────────────

        total_messages = await self.news_items.count_documents(
            date_filter
        )

        approved_messages = await self.news_items.count_documents({
            **date_filter,
            "state": {
                "$in": [
                    "approved",
                    "publishing",
                    "published",
                ],
            },
        })

        unpublished_messages = await self.news_items.count_documents({
            **date_filter,
            "$or": [
                {"publications": {"$exists": False}},
                {"publications": {"$size": 0}},
                {
                    "publications": {
                        "$not": {
                            "$elemMatch": {
                                "success": True,
                            }
                        }
                    }
                },
            ],
        })

        # ── Source statistics ─────────────────────────────────────────────

        source_pipeline = []

        if date_filter:
            source_pipeline.append({
                "$match": date_filter,
            })

        source_pipeline.extend([
            {
                "$group": {
                    "_id": "$source_chat_id",
                    "message_count": {
                        "$sum": 1,
                    },
                }
            },
            {
                "$sort": {
                    "message_count": -1,
                }
            },
        ])

        source_stats = await self.news_items.aggregate(
            source_pipeline
        ).to_list(length=None)

        source_channels = await self.source_channels.find(
            {"is_active": True}
        ).to_list(length=None)

        source_by_id = {
            str(ch["channel_id"]): ch
            for ch in source_channels
        }

        sources = []

        for item in source_stats:
            channel_id = str(item["_id"])
            channel = source_by_id.get(channel_id)

            sources.append({
                "channel_id": channel_id,
                "username": (
                    channel.get("channel_username")
                    if channel
                    else None
                ),
                "title": (
                    channel.get("title")
                    if channel
                    else None
                ),
                "message_count": item["message_count"],
            })

        existing_source_ids = {
            item["channel_id"]
            for item in sources
        }

        for channel in source_channels:
            channel_id = str(channel["channel_id"])

            if channel_id not in existing_source_ids:
                sources.append({
                    "channel_id": channel_id,
                    "username": channel.get("channel_username"),
                    "title": channel.get("title"),
                    "message_count": 0,
                })

        sources.sort(
            key=lambda item: item["message_count"],
            reverse=True,
        )

        # ── Destination statistics ────────────────────────────────────────

        destination_channels = await self.destination_channels.find(
            {"is_active": True}
        ).to_list(length=None)

        destination_pipeline = []

        if date_filter:
            destination_pipeline.append({
                "$match": date_filter,
            })

        destination_pipeline.extend([
            {
                "$unwind": "$publications",
            },
            {
                "$match": {
                    "publications.success": True,
                }
            },
            {
                "$group": {
                    "_id": "$publications.channel_id",
                    "message_count": {
                        "$sum": 1,
                    },
                }
            },
            {
                "$sort": {
                    "message_count": -1,
                }
            },
        ])

        destination_stats = await self.news_items.aggregate(
            destination_pipeline
        ).to_list(length=None)

        destination_by_id = {
            str(ch["channel_id"]): ch
            for ch in destination_channels
        }

        destinations = []

        for item in destination_stats:
            channel_id = str(item["_id"])
            channel = destination_by_id.get(channel_id)

            destinations.append({
                "channel_id": channel_id,
                "username": (
                    channel.get("channel_username")
                    if channel
                    else None
                ),
                "title": (
                    channel.get("title")
                    if channel
                    else None
                ),
                "message_count": item["message_count"],
            })

        existing_destination_ids = {
            item["channel_id"]
            for item in destinations
        }

        for channel in destination_channels:
            channel_id = str(channel["channel_id"])

            if channel_id not in existing_destination_ids:
                destinations.append({
                    "channel_id": channel_id,
                    "username": channel.get("channel_username"),
                    "title": channel.get("title"),
                    "message_count": 0,
                })

        destinations.sort(
            key=lambda item: item["message_count"],
            reverse=True,
        )

        # ── Admin statistics ───────────────────────────────────────────────

        admins = await self.list_admin_users(
            active_only=True
        )

        admin_ids = {
            int(admin["telegram_id"])
            for admin in admins
        }

        admin_pipeline = []

        if date_filter:
            admin_pipeline.append({
                "$match": date_filter,
            })

        admin_pipeline.extend([
            {
                "$match": {
                    "published_by": {
                        "$in": list(admin_ids),
                    }
                }
            },
            {
                "$group": {
                    "_id": "$published_by",
                    "message_count": {
                        "$sum": 1,
                    },
                }
            },
            {
                "$sort": {
                    "message_count": -1,
                }
            },
        ])

        admin_stats = await self.news_items.aggregate(
            admin_pipeline
        ).to_list(length=None)

        admin_stats_by_id = {
            int(item["_id"]): item["message_count"]
            for item in admin_stats
        }

        admin_publication_stats = []

        for admin in admins:
            telegram_id = int(admin["telegram_id"])

            admin_publication_stats.append({
                "telegram_id": telegram_id,
                "username": admin.get("username"),
                "role": admin.get("role"),
                "message_count": admin_stats_by_id.get(
                    telegram_id,
                    0,
                ),
            })

        admin_publication_stats.sort(
            key=lambda item: item["message_count"],
            reverse=True,
        )

        return {
            "total_messages": total_messages,
            "approved_messages": approved_messages,
            "unpublished_messages": unpublished_messages,
            "sources": sources,
            "destinations": destinations,
            "admins": admin_publication_stats,
        }


# Module-level singleton – import this everywhere.
db = Database()
