import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from channel-archiver dir or project root
load_dotenv()
load_dotenv(".env.example")

# Telegram API (get from https://my.telegram.org)
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")

# Session name for Telethon (persists login)
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "channel_archiver_session")

# Target private group/channel to forward messages to (use @username or negative ID)
TARGET_GROUP = int(os.getenv("TARGET_GROUP_ID"))

# List of public channels to scrape (comma-separated @usernames or IDs)
CHANNELS_RAW = os.getenv("TELEGRAM_CHANNELS", "")
CHANNELS = [c.strip() for c in CHANNELS_RAW.split(",") if c.strip()]

# MongoDB
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "faraz_telegram_bridge")
MONGODB_COLLECTION = os.getenv("MONGODB_COLLECTION", "messages")
