# Faraz Telegram Bridge

A Telethon-based agent that fetches all messages from public Telegram channels, forwards them to a private group, and stores metadata in MongoDB.

## Project Structure

```
faraz-telegram-bridge/
├── docker-compose.yml      # Run channel-archiver + MongoDB
├── .env.example
├── README.md
└── channel-archiver/       # Archives Telegram channel posts to MongoDB
    ├── Dockerfile
    ├── config.py
    ├── database.py
    ├── scraper.py
    ├── main.py
    └── requirements.txt
```

## Features

- **Scrapes** all messages from a list of public channels
- **Forwards** every message/post to a private group
- **Saves metadata** to MongoDB for each message:
  - Channel name
  - Fetch date
  - Post type: photo count, video count, voice count, audio count, document count, sticker count
  - Poll, web page indicators
  - Source and forwarded message IDs
  - Text preview, views, forwards, replies count
  - Album/grouped ID when applicable

## Setup

1. **Get Telegram API credentials**
   - Go to https://my.telegram.org
   - Log in and create an application
   - Copy `API ID` and `API Hash`

2. **Configure**
   - Copy `.env.example` to `.env`
   - Fill in your values:
     - `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`
     - `TARGET_GROUP_ID` — your private group @username or ID
     - `TELEGRAM_CHANNELS` — comma-separated list of public channels to scrape

## Usage with Docker Compose

```bash
# From project root
docker compose up -d

# View logs (first run will prompt for phone login - use -it for interactive)
docker compose run --rm -it channel-archiver
```

**First run:** The container needs interactive input for Telegram login (phone number + code). Run once with:

```bash
docker compose run --rm -it channel-archiver
```

After logging in, the session is stored in a Docker volume. Subsequent `docker compose up -d` runs will reuse it.

## Local Development (without Docker)

```bash
cd channel-archiver
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env` in the project root. Then:

```bash
python main.py
```

## MongoDB Document Structure

Each saved message looks like:

```json
{
  "channel": "@channel_username",
  "source_message_id": 12345,
  "source_date": "2025-02-15T10:00:00",
  "fetch_date": "2025-02-15T12:30:00",
  "forwarded_message_id": 42,
  "forwarded_chat_id": "@your_private_group",
  "media": {
    "photo_count": 1,
    "video_count": 0,
    "voice_count": 0,
    "audio_count": 0,
    "document_count": 0,
    "sticker_count": 0,
    "poll": false,
    "web_page": false
  },
  "text_length": 150,
  "text_preview": "First 500 chars...",
  "has_reply": false,
  "has_edit_date": false,
  "views": 1000,
  "forwards": 5,
  "replies_count": 3,
  "grouped_id": 123456789
}
```
