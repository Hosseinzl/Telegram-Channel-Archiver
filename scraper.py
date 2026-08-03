import asyncio
import logging
from datetime import datetime
import os
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
    """Build the document to save in MongoDB with foolproof source tracking."""
    
    # 1. تشخیص منبع (Source Chat & Source Message ID)
    # چک می‌کنیم آیا پیام از جای دیگری فوروارد شده یا خیر
    if message.fwd_from and message.fwd_from.from_id and hasattr(message.fwd_from.from_id, 'channel_id'):
        # سناریو الف: پیام فورواردی است (مثلاً از ورزش 3 به کانال تست)
        # اطلاعات منبع اصلی را برمی‌داریم تا با چیزی که بات می‌بیند ست شود
        raw_peer_id = message.fwd_from.from_id.channel_id
        source_msg_id = message.fwd_from.channel_post
    else:
        # سناریو ب: پیام مستقیم در کانال تست پست شده یا فوروارد مخفی است
        # اطلاعات خود کانال تست را برمی‌داریم
        raw_peer_id = getattr(message.peer_id, "channel_id", 0)
        source_msg_id = message.id

    # استانداردسازی آیدی کانال برای مطابقت با Bot API
    # آیدی‌های کانال در تلگرام همیشه با -100 شروع می‌شوند
    if raw_peer_id:
        str_peer_id = str(raw_peer_id)
        if not str_peer_id.startswith("-100"):
            formatted_source_chat_id = int(f"-100{raw_peer_id}")
        else:
            formatted_source_chat_id = int(raw_peer_id)
    else:
        formatted_source_chat_id = None

    # 2. تعیین محتوا و آیدی‌های داخلی
    internal_id = source_message_ids[0] if source_message_ids else message.id
    text = text_combined if source_message_ids else (message.text or message.message or "")

    # 3. ساخت داکیومنت نهایی
    meta = {
        "channel": channel,
        "source_chat_id": formatted_source_chat_id, # شناسنامه کانال (ورزش 3 یا تست)
        "source_msg_id": source_msg_id,            # شناسنامه پیام (290740 یا 32)
        "internal_source_id": internal_id,        # آیدی در کانال واسط شما
        "source_message_id": internal_id,         # برای سازگاری با کدهای قدیمی
        "source_date": message.date.isoformat() if message.date else None,
        "fetch_date": datetime.utcnow().isoformat(),
        "forwarded_message_id": forwarded_message_id,
        "forwarded_chat_id": str(TARGET_GROUP),
        "media": media_info,
        "text_length": len(text),
        "text_preview": text if text else None,
        "has_reply": message.reply_to is not None,
        "has_edit_date": message.edit_date is not None,
        "views": getattr(message, "views", None),
        "forwards": getattr(message, "forwards", None),
    }

    # چاپ برای دیباگ در کنسول تلثون
    print(f"DEBUG SCRAPER: Source {formatted_source_chat_id} | Msg {source_msg_id}")
    
    if message.grouped_id:
        meta["grouped_id"] = message.grouped_id
    if source_message_ids is not None:
        meta["source_message_ids"] = source_message_ids
        meta["album_count"] = album_count
    
    return meta

async def _fetch_album_messages(
    client: TelegramClient, 
    channel: str, 
    grouped_id: int, 
    known_message_id: int
) -> list:
    """
    Fetch all messages in an album. 
    Telegram albums can have up to 10 items.
    """
    logger.debug(
        "Fetching album window %s grouped_id=%s around_message=%s",
        channel,
        grouped_id,
        known_message_id,
    )
    
    # گرفتن پیام‌های اطراف برای اطمینان از جمع‌آوری کل آلبوم
    # معمولاً آلبوم‌ها پشت سر هم هستند، پس بازه ۵۰ تایی کاملاً امن است
    messages = await client.get_messages(
        channel, 
        min_id=known_message_id - 40, 
        max_id=known_message_id + 10
    )
    
    # فیلتر کردن پیام‌هایی که متعلق به این آلبوم هستند
    album = [
        m for m in messages 
        if m and getattr(m, "grouped_id", None) == grouped_id
    ]
    
    # مرتب‌سازی بر اساس آیدی برای حفظ ترتیب درست نمایش در فوروارد
    return sorted(album, key=lambda m: m.id)

async def _process_album(
    client: TelegramClient,
    channel: str,
    target_entity,
    album_messages: list,
) -> None:
    """Process an album correctly without overwriting original source info."""
    album_messages.sort(key=lambda m: m.id)
    ids = [m.id for m in album_messages]
    
    media_counts = [analyze_message_media(m) for m in album_messages]
    media_info = merge_media_counts(media_counts)

    text_parts = []
    for m in album_messages:
        t = m.text or m.message or ""
        if t:
            text_parts.append(t)
    text_combined = "\n\n".join(text_parts)

    try:
        forwarded = await client.forward_messages(target_entity, ids, channel)
        if isinstance(forwarded, list):
            forwarded_ids = [f.id for f in forwarded if f] if forwarded else []
            forwarded_id = forwarded_ids[0] if forwarded_ids else 0
        else:
            forwarded_id = forwarded.id if forwarded else 0
            forwarded_ids = [forwarded_id] if forwarded_id else []
    except Exception as e:
        logger.error("Failed to forward album %s: %s", channel, e)
        return

    # --- اصلاح اصلی اینجاست ---
    # فقط تابع را صدا می‌زنیم. این تابع خودش هوشمند است و 
    # اگر آلبوم فورواردی باشد، آیدی منبع اصلی (ورزش 3) را برمی‌دارد.
    meta = build_message_metadata(
        channel,
        album_messages[0],
        forwarded_id,
        media_info,
        source_message_ids=ids,
        text_combined=text_combined,
        album_count=len(album_messages),
    )
    
    # دیگر دستی source_chat_id یا source_msg_id را ست نکنید!
    # فقط اگر لیست آیدی‌ها را برای بات لازم دارید اضافه کنید
    if len(forwarded_ids) > 1:
        meta["forwarded_message_ids"] = forwarded_ids

    await db.save_message(meta)
    logger.info("Processed album %s -> %s (Source: %s)", channel, forwarded_id, meta.get("source_msg_id"))

async def handle_single_message(message, channel: str, client: TelegramClient, target_entity):
    # ۱. بررسی تکراری بودن (آلبوم یا پیام تکی)
    if message.grouped_id:
        if await db.grouped_exists(channel, message.grouped_id) or await db.message_exists(channel, message.id):
            return False
        album = await _fetch_album_messages(client, channel, message.grouped_id, message.id)
        if album:
            await _process_album(client, channel, target_entity, album)
            return True
        return False

    if await db.message_exists(channel, message.id):
        return False

    # ۲. آنالیز رسانه
    media_info = analyze_message_media(message)

        # ۳. فوروارد پیام به مقصد
    try:
        forwarded = await client.forward_messages(target_entity, message.id, channel)
        # استخراج آیدی پیام فوروارد شده در مقصد
        f_id = forwarded[0].id if isinstance(forwarded, list) else (forwarded.id if forwarded else 0)
    except Exception as e:
        logger.error("Failed to forward %s/%s: %s", channel, message.id, e)
        return False

    # ۴. ساخت و ذخیره متادیتا (اصلاح شده)
    # نکته: دیگر source_chat_id و source_msg_id را دستی ست نمی‌کنیم
    # تا تابع زیر بتواند منبع اصلی (مثلا ورزش ۳) را به درستی شناسایی کند.
    meta = build_message_metadata(channel, message, f_id, media_info)
            
    await db.save_message(meta)
    logger.info("Processed %s/%s -> %s (Source: %s)", 
                    channel, message.id, f_id, meta.get('source_msg_id'))
    return True

async def process_channel(client: TelegramClient, channel: str, target_entity):
    """Fetch all messages from a channel, forward each to target, save to DB."""
    logger.info("Processing channel: %s", channel)

    try:
        last_id = await db.get_last_source_message_id(channel)
        
        # تعیین لیست پیام‌ها برای اجرای اول (First Run)
        if last_id is None:
            backlog_limit = int(os.getenv("FIRST_RUN_BACKLOG_LIMIT", "5"))
            messages = await client.get_messages(channel, limit=backlog_limit)
            messages = [m for m in messages if m]
            messages.sort(key=lambda m: m.id)
        else:
            messages = [] 

        # ۵. اجرای حلقه پردازش
        if last_id is None:
            for m in messages:
                if await handle_single_message(m, channel, client, target_entity):
                    await asyncio.sleep(1)
        else:
            async for m in client.iter_messages(channel, min_id=last_id):
                if not m: continue
                if await handle_single_message(m):
                    await asyncio.sleep(1)

        logger.info("Channel done %s", channel)
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
