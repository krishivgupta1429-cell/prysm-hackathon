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

# Hackathon Meta layer (AI_PROVIDER=meta): Modal client and signal helpers (ffmpeg/ffprobe come from apt above)
COPY requirements-studio.txt .
RUN pip install --no-cache-dir -r requirements-studio.txt

# App code
COPY . .

# Start: bring the database to the current schema (hackathon: app/studio/db_bootstrap.py creates the vector extension, builds a fresh
# database or runs `alembic upgrade head`, and stops the container if that fails), then start the server.
# Railway sets $PORT dynamically; WEB_CONCURRENCY = number of server processes (each loads the analysis libraries: keep it small on a small plan).
ENV PYTHONUNBUFFERED=1
CMD sh -c "python -m app.studio.db_bootstrap && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout-keep-alive 620 --h11-max-incomplete-event-size 524288"
