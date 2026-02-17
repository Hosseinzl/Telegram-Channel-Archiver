FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install Python dependencies first (better layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Directory and volume for persistent Telethon session files
RUN mkdir -p /data
VOLUME ["/data"]

# Default session path inside the container (can be overridden)
ENV SESSION_PATH=/data

# Run the Telegram channel archiver
CMD ["python", "scraper.py"]
