"""Telegram Bot API listener: extracts text and file IDs from forwarded messages."""

import asyncio
import logging
import time
from typing import Any

from telegram import Update, MessageOriginChannel
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from config import TARGET_GROUP
from database import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def extract_file_ids(message: Any) -> dict[str, Any]:
    """Extract all file IDs from a Telegram message."""
    file_ids = {
        "photo_file_ids": [],
        "video_file_id": None,
        "video_note_file_id": None,
        "voice_file_id": None,
        "audio_file_id": None,
        "document_file_id": None,
        "sticker_file_id": None,
        "animation_file_id": None,
    }

    if message.photo:
        # Photos can have multiple sizes; get the largest (last one)
        file_ids["photo_file_ids"] = [photo.file_id for photo in message.photo]
    
    if message.video:
        file_ids["video_file_id"] = message.video.file_id
    
    if message.video_note:
        file_ids["video_note_file_id"] = message.video_note.file_id
    
    if message.voice:
        file_ids["voice_file_id"] = message.voice.file_id
    
    if message.audio:
        file_ids["audio_file_id"] = message.audio.file_id
    
    if message.document:
        file_ids["document_file_id"] = message.document.file_id
        # Check if it's a sticker or animation
        if message.document.mime_type:
            if "sticker" in message.document.mime_type:
                file_ids["sticker_file_id"] = message.document.file_id
            elif "gif" in message.document.mime_type or message.document.file_name and message.document.file_name.endswith(".gif"):
                file_ids["animation_file_id"] = message.document.file_id
    
    if message.sticker:
        file_ids["sticker_file_id"] = message.sticker.file_id
    
    if message.animation:
        file_ids["animation_file_id"] = message.animation.file_id

    return file_ids

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process incoming message: extract text/file IDs and update DB."""
    message = update.message
    
    if not message:
        return
    
    # محدود کردن به گروه هدف
    chat_id = message.chat_id
    if chat_id != TARGET_GROUP:
        logger.debug("Ignoring message from chat %s", chat_id)
        return

    # استخراج اطلاعات فوروارد (نقطه اتصال با تلثون)
    if not message.forward_origin or not isinstance(message.forward_origin, MessageOriginChannel):
        logger.debug("Message %s is not a forwarded channel message, skipping", message.message_id)
        return

    source_chat_id = message.forward_origin.chat.id
    source_msg_id = message.forward_origin.message_id
    bot_message_id = message.message_id

    logger.info("Processing message %s (Source: %s, Msg: %s)", bot_message_id, source_chat_id, source_msg_id)
    
    # استخراج محتوا
    text = message.text or message.caption or ""
    file_ids = extract_file_ids(message)
    
    has_content = bool(text) or any(
        file_ids.get(k) for k in file_ids.keys()
        if isinstance(file_ids.get(k), (str, list)) and file_ids.get(k)
    )
    
    if not has_content:
        return
    
    # وقفه کوتاه برای اطمینان از اینکه تلثون قبلاً رکورد را ساخته است
    time.sleep(1)
    
    try:
        # جستجو و آپدیت بر اساس اطلاعات منبع (نه آیدی فوروارد)
        updated = await db.update_forwarded_message_by_source(
            source_chat_id=source_chat_id,
            source_msg_id=source_msg_id,
            text=text,
            file_ids=file_ids,
            bot_message_id=bot_message_id, # ذخیره آیدی جدید بات برای مراجعات بعدی
            state="completed"
        )
        
        if updated:
            logger.info(
                "Successfully matched and updated source %s/%s with Bot ID %s",
                source_chat_id, source_msg_id, bot_message_id
            )
        else:
            logger.warning(
                "DB Mapping failed for source %s/%s (No matching record found)",
                source_chat_id, source_msg_id
            )
    except Exception as e:
        logger.exception("Error in DB update for message %s: %s", bot_message_id, e)

async def main():
    """Start the bot."""
    import os
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not bot_token:
        raise ValueError("Set TELEGRAM_BOT_TOKEN in .env")
    
    logger.info("Starting Telegram bot for group %s", TARGET_GROUP)
    
    # Create application
    application = Application.builder().token(bot_token).build()
    
    # Add handler for all messages in groups
    message_handler = MessageHandler(
        filters.Chat(chat_id=TARGET_GROUP) & filters.ALL,
        process_message
    )
    application.add_handler(message_handler)
    
    # Start the bot
    logger.info("Bot started, listening for messages...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=["message"])
    
    # Keep running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutting down bot...")
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
