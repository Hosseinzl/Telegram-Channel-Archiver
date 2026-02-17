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
import asyncio

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """اصلاح شده: پردازش هوشمند پیام‌های تکی و آلبوم‌ها"""
    message = update.effective_message
    
    if not message or message.chat_id != TARGET_GROUP:
        return

    # ۱. استخراج اطلاعات فوروارد (پل ارتباطی ما با تلثون)
    if not message.forward_origin or not isinstance(message.forward_origin, MessageOriginChannel):
        return

    source_chat_id = message.forward_origin.chat.id
    source_msg_id = message.forward_origin.message_id
    bot_message_id = message.message_id

    # ۲. استخراج محتوا (متن و فایل آیدی)
    text = message.text or message.caption or ""
    print(f"this is file text:  {text}")
    file_ids = extract_file_ids(message)
    print(f"file ids:{file_ids}")
    # ۳. فیلتر کردن پیام‌های فاقد محتوا
    has_file = any(v for v in file_ids.values() if v)
    if not text and not has_file:
        return

    # ۴. وقفه هوشمند (Non-blocking)
    # اگر آلبوم باشد، پیام‌ها خیلی سریع می‌آیند؛ کمی صبر می‌کنیم تا تلثون کارش تمام شود
    wait_time = 1.5 if message.media_group_id else 0.8
    await asyncio.sleep(wait_time)

    logger.info(f"🔍 Searching DB for Source: {source_chat_id} | Msg: {source_msg_id}")
    
    try:
        # ۵. تلاش برای آپدیت رکورد در مونگو
        # نکته: متد update_forwarded_message_by_source باید از $push برای فایل آیدی استفاده کند
        updated = await db.update_forwarded_message_by_source(
            source_chat_id=source_chat_id,
            source_msg_id=source_msg_id,
            file_ids=file_ids,         # به صورت لیست در دیتابیس Push می‌شود
            bot_text=text,             # کپشن دریافتی توسط بات
            last_bot_msg_id=bot_message_id,
            state="completed"
        )
        
        if updated:
            logger.info(f"✅ DB Updated: Source {source_msg_id} -> Bot ID {bot_message_id}")
        else:
            # اگر بار اول پیدا نشد، یک شانس مجدد با تاخیر بیشتر (مخصوص سرورهای کند)
            await asyncio.sleep(2)
            retry_updated = await db.update_forwarded_message_by_source(
                source_chat_id=source_chat_id,
                source_msg_id=source_msg_id,
                file_ids=file_ids,
                bot_text=text,
                last_bot_msg_id=bot_message_id,
                state="completed"
            )
            if retry_updated:
                logger.info(f"♻️ DB Updated on Retry: {source_msg_id}")
            else:
                logger.warning(f"❌ DB Mapping failed for source {source_chat_id}/{source_msg_id}")

    except Exception as e:
        logger.error(f"⚠️ Error updating DB for bot message {bot_message_id}: {str(e)}")

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
