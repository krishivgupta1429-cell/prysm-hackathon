FROM python:3.11-slim

# System dependencies (FFmpeg + yt-dlp)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    && pip install --no-cache-dir yt-dlp \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# Run Alembic migrations on startup, then start server
# Railway sets $PORT dynamically
CMD sh -c "python -m alembic upgrade head 2>/dev/null; uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 4 --timeout-keep-alive 620 --h11-max-incomplete-event-size 524288"
