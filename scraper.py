"""Telethon agent: fetches messages from channels, forwards to group, saves to MongoDB."""

import asyncio
import logging
from datetime import datetime
from typing import Any

from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    DocumentAttributeVideo,
    DocumentAttributeAudio,
    DocumentAttributeSticker,
)

from config import API_ID, API_HASH, SESSION_NAME, TARGET_GROUP, CHANNELS
from database import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def analyze_message_media(message) -> dict[str, Any]:
    """Extract media type counts and metadata from a Telegram message."""
    counts = {
        "photo_count": 0,
        "video_count": 0,
        "voice_count": 0,
        "audio_count": 0,
        "document_count": 0,
        "sticker_count": 0,
        "poll": False,
        "web_page": False,
    }

    if not message.media:
        return counts

    if isinstance(message.media, MessageMediaPhoto):
        counts["photo_count"] = 1
    elif isinstance(message.media, MessageMediaDocument):
        doc = message.media.document
        if not doc or not doc.attributes:
            counts["document_count"] = 1
            return counts

        is_video = False
        is_voice = False
        is_audio = False
        is_sticker = False

        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeSticker):
                is_sticker = True
                break
            if isinstance(attr, DocumentAttributeVideo):
                is_video = True
                break
            if isinstance(attr, DocumentAttributeAudio):
                if attr.voice:
                    is_voice = True
                else:
                    is_audio = True
                break

        if not is_sticker and getattr(doc, "mime_type", ""):
            if doc.mime_type.startswith("video/"):
                is_video = True
            elif "sticker" in doc.mime_type:
                is_sticker = True

        if is_sticker:
            counts["sticker_count"] = 1
        elif is_voice:
            counts["voice_count"] = 1
        elif is_audio:
            counts["audio_count"] = 1
        elif is_video:
            counts["video_count"] = 1
        else:
            counts["document_count"] = 1

    if hasattr(message.media, "webpage") and message.media.webpage:
        counts["web_page"] = True

    if message.poll:
        counts["poll"] = True

    return counts


def merge_media_counts(counts_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge media counts from multiple messages (e.g. album) by summing."""
    merged = {
        "photo_count": 0,
        "video_count": 0,
        "voice_count": 0,
        "audio_count": 0,
        "document_count": 0,
        "sticker_count": 0,
        "poll": False,
        "web_page": False,
    }
    for c in counts_list:
        merged["photo_count"] += c.get("photo_count", 0)
        merged["video_count"] += c.get("video_count", 0)
        merged["voice_count"] += c.get("voice_count", 0)
        merged["audio_count"] += c.get("audio_count", 0)
        merged["document_count"] += c.get("document_count", 0)
        merged["sticker_count"] += c.get("sticker_count", 0)
        merged["poll"] = merged["poll"] or c.get("poll", False)
        merged["web_page"] = merged["web_page"] or c.get("web_page", False)
    return merged


def build_message_metadata(
    channel: str,
    message,
    forwarded_message_id: int,
    media_info: dict[str, Any],
    *,
    source_message_ids: list[int] | None = None,
    text_combined: str = "",
    album_count: int = 0,
) -> dict[str, Any]:
    """Build the document to save in MongoDB."""
    if source_message_ids is not None:
        # Album: use first message for dates, combine text from all
        source_id = source_message_ids[0]
        source_date = message.date.isoformat() if message.date else None
        text = text_combined
    else:
        source_id = message.id
        source_date = message.date.isoformat() if message.date else None
        text = message.text or message.message or ""

    meta = {
        "channel": channel,
        "source_message_id": source_id,
        "source_date": source_date,
        "fetch_date": datetime.utcnow().isoformat(),
        "forwarded_message_id": forwarded_message_id,
        "forwarded_chat_id": str(TARGET_GROUP),
        "media": media_info,
        "text_length": len(text),
        "text_preview": text[:500] if text else None,
        "has_reply": message.reply_to is not None,
        "has_edit_date": message.edit_date is not None,
        "views": getattr(message, "views", None),
        "forwards": getattr(message, "forwards", None),
        "replies_count": getattr(message.replies, "replies", None) if message.replies else None,
    }
    if message.grouped_id:
        meta["grouped_id"] = message.grouped_id
    if source_message_ids is not None:
        meta["source_message_ids"] = source_message_ids
        meta["album_count"] = album_count
    return meta


async def _process_album(
    client: TelegramClient,
    channel: str,
    target_entity,
    album_messages: list,
) -> None:
    """Process an album: forward as a group, save one doc with aggregated media counts."""
    # Sort by id ascending (chronological) for correct forward order
    album_messages.sort(key=lambda m: m.id)
    ids = [m.id for m in album_messages]
    logger.debug("Album detected %s grouped_ids=%s", channel, ids)

    media_counts = [analyze_message_media(m) for m in album_messages]
    media_info = merge_media_counts(media_counts)

    text_parts = []
    for m in album_messages:
        t = m.text or m.message or ""
        if t:
            text_parts.append(t)
    text_combined = "\n\n".join(text_parts)

    try:
        logger.info("Forwarding album %s count=%d first_id=%s last_id=%s", channel, len(ids), ids[0], ids[-1])
        forwarded = await client.forward_messages(target_entity, ids, channel)
        if isinstance(forwarded, list):
            forwarded_ids = [f.id for f in forwarded if f] if forwarded else []
            forwarded_id = forwarded_ids[0] if forwarded_ids else 0
        else:
            forwarded_id = forwarded.id if forwarded else 0
            forwarded_ids = [forwarded_id] if forwarded_id else []
    except Exception as e:
        logger.error("Failed to forward album %s ids %s: %s", channel, ids, e)
        return

    meta = build_message_metadata(
        channel,
        album_messages[0],
        forwarded_id,
        media_info,
        source_message_ids=ids,
        text_combined=text_combined,
        album_count=len(album_messages),
    )
    # Store all forwarded message IDs for albums (bot needs to match any of them)
    if len(forwarded_ids) > 1:
        meta["forwarded_message_ids"] = forwarded_ids
    logger.debug(
        "Saving album metadata %s forwarded_id=%s media=%s text_len=%d",
        channel,
        forwarded_id,
        media_info,
        len(text_combined or ""),
    )
    await db.save_message(meta)
    logger.info("Processed album %s [%s] -> %s", channel, ",".join(map(str, ids)), forwarded_id)


async def _fetch_album_messages(
    client: TelegramClient, channel: str, grouped_id: int, known_message_id: int
) -> list:
    """Fetch all messages in an album (they can be interleaved with others)."""
    # Albums are max 10 items; fetch a window to catch them all
    logger.debug(
        "Fetching album window %s grouped_id=%s around_message=%s",
        channel,
        grouped_id,
        known_message_id,
    )
    messages = await client.get_messages(
        channel, min_id=known_message_id - 50, max_id=known_message_id + 1
    )
    album = [m for m in messages if m and getattr(m, "grouped_id", None) == grouped_id]
    logger.debug("Album window fetched %s grouped_id=%s found=%d", channel, grouped_id, len(album))
    return sorted(album, key=lambda m: m.id)


import os


async def process_channel(client: TelegramClient, channel: str, target_entity):
    """Fetch all messages from a channel, forward each to target, save to DB safely."""
    logger.info("Processing channel: %s", channel)

    try:
        last_id = await db.get_last_source_message_id(channel)
        logger.debug("DB watermark %s last_source_message_id=%s", channel, last_id)

        processed = 0
        skipped = 0

        # Determine backlog for first run
        if last_id is None:
            backlog_limit = int(os.getenv("FIRST_RUN_BACKLOG_LIMIT", "5"))
            logger.info("First run for %s; processing last %d messages", channel, backlog_limit)
            messages = await client.get_messages(channel, limit=backlog_limit)
            messages = [m for m in messages if m]
            messages.sort(key=lambda m: m.id)
        else:
            messages = []
            async for m in client.iter_messages(channel, min_id=last_id):
                if m:
                    messages.append(m)
            messages.sort(key=lambda m: m.id)

        for message in messages:
            if not message:
                continue

            # --- Handle albums ---
            if message.grouped_id:
                if await db.grouped_exists(channel, message.grouped_id):
                    skipped += 1
                    continue
                if await db.message_exists(channel, message.id):
                    skipped += 1
                    continue  # Part of already-processed album

                album_messages = await _fetch_album_messages(client, channel, message.grouped_id, message.id)
                if album_messages:
                    # 1️⃣ Save metadata for all album messages first
                    for m in album_messages:
                        media_info = analyze_message_media(m)
                        meta = build_message_metadata(channel, m, forwarded_id=0, media_info=media_info)
                        await db.save_message(meta)

                    # 2️⃣ Forward album
                    try:
                        forwarded_list = await client.forward_messages(target_entity, [m.id for m in album_messages], channel)
                        # 3️⃣ Update forwarded_id for each message
                        for orig_msg, fwd_msg in zip(album_messages, forwarded_list):
                            fwd_id = fwd_msg.id if fwd_msg else 0
                            await db.update_forwarded_id(channel, orig_msg.id, fwd_id)
                    except Exception as e:
                        logger.error("Failed to forward album %s: %s", message.grouped_id, e)

                    processed += 1
                    await asyncio.sleep(1)
                continue

            # --- Single message ---
            if await db.message_exists(channel, message.id):
                skipped += 1
                continue

            media_info = analyze_message_media(message)
            # 1️⃣ Save metadata first
            meta = build_message_metadata(channel, message, forwarded_id=0, media_info=media_info)
            await db.save_message(meta)

            # 2️⃣ Forward
            try:
                forwarded = await client.forward_messages(target_entity, message.id, channel)
                if isinstance(forwarded, list):
                    forwarded_id = forwarded[0].id if forwarded else 0
                else:
                    forwarded_id = forwarded.id if forwarded else 0
            except Exception as e:
                logger.error("Failed to forward %s/%s: %s", channel, message.id, e)
                forwarded_id = 0

            # 3️⃣ Update forwarded_id
            await db.update_forwarded_id(channel, message.id, forwarded_id)

            processed += 1
            await asyncio.sleep(1)

        logger.info("Channel done %s processed=%d skipped=%d", channel, processed, skipped)

    except Exception as e:
        logger.exception("Error processing channel %s: %s", channel, e)

async def run():
    """Main agent loop."""
    if not API_ID or not API_HASH:
        raise ValueError("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")
    if not TARGET_GROUP:
        raise ValueError("Set TARGET_GROUP_ID in .env")
    if not CHANNELS:
        raise ValueError("Set TELEGRAM_CHANNELS (comma-separated) in .env")

    # Session file: use SESSION_PATH for Docker volume persistence
    import os
    session_path = os.getenv("SESSION_PATH", "")
    session_file = f"{session_path.rstrip('/')}/{SESSION_NAME}" if session_path else SESSION_NAME

    client = TelegramClient(session_file, API_ID, API_HASH)

    # How often to poll channels for new messages (in seconds).
    poll_interval = int(os.getenv("POLL_INTERVAL", "10"))
    logger.info("Starting scraper channels=%d target_group=%s poll_interval=%ss", len(CHANNELS), TARGET_GROUP, poll_interval)

    async with client:
        me = await client.get_me()
        logger.info("Logged in as %s (%s)", getattr(me, "username", None), getattr(me, "id", None))
        target_entity = await client.get_input_entity(TARGET_GROUP)
        logger.info("Resolved target entity: %s", TARGET_GROUP)

        try:
            while True:
                logger.debug("Poll cycle start")
                for channel in CHANNELS:
                    await process_channel(client, channel, target_entity)
                logger.debug("Poll cycle done; sleeping %ss", poll_interval)
                await asyncio.sleep(poll_interval)
        finally:
            logger.info("Shutting down; closing DB connection")
            await db.close()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
