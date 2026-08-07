import os

from dotenv import load_dotenv

# Load .env from channel-archiver dir or project root
load_dotenv()
load_dotenv(".env.example")

# Telegram API (get from https://my.telegram.org)
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")

# Session name for Telethon (persists login)
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "channel_archiver_session")
SESSION_PATH = os.getenv("SESSION_PATH", "")

# Target private group/channel to forward messages to (use @username or negative ID)
TARGET_GROUP = int(os.getenv("TARGET_GROUP_ID"))

# MongoDB
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "faraz_telegram_bridge")
MONGODB_COLLECTION = os.getenv("MONGODB_COLLECTION", "messages")

# Scraper runtime settings
FETCH_PERMISSION_API = os.getenv("FETCH_PERMISSION_API", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))
CHANNEL_SYNC_INTERVAL = int(os.getenv("CHANNEL_SYNC_INTERVAL", "300"))
FIRST_RUN_BACKLOG_LIMIT = int(os.getenv("FIRST_RUN_BACKLOG_LIMIT", "5"))

# Supervisor id
SUPERVISOR_ID = int(os.getenv("SUPERVISOR_ID"))