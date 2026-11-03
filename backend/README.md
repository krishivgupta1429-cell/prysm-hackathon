# Prysm Backend

Production-grade creator intelligence platform — **Claude API only**.

## What Changed From Original

| Component | Original | Prysm |
|-----------|----------|-------|
| Vision (frame analysis) | GPT-4o Vision | **Claude Vision** |
| Transcription | OpenAI Whisper | **Claude audio** |
| Reasoning | Claude | Claude (unchanged) |
| Chat (ARIA) | Claude | Claude (unchanged) |
| API keys needed | ANTHROPIC + OPENAI | **ANTHROPIC only** |

## Architecture

```
Video Upload / URL
       ↓
Celery Task Queue (Redis)
       ↓
video_worker.py
  ├── yt-dlp: download if URL
  ├── FFmpeg: extract frames (1fps body, 2fps hook window)
  ├── FFmpeg: extract audio as MP3
  └── transcription_service.py → Claude reads audio → transcript + timestamps
       ↓
analysis_worker.py
  ├── vision_service.py → Claude Vision analyses frames → VideoFrameSummary
  ├── ai_service.py → Claude reasons over transcript + visual context
  │   ├── analyze_hook()
  │   ├── predict_virality()
  │   ├── analyze_content_breakdown()
  │   ├── analyze_retention()
  │   └── suggest_thumbnails()
  └── Store result in PostgreSQL
       ↓
FastAPI returns result to Next.js frontend
```

## Setup

```bash
# 1 — Install system deps
brew install ffmpeg postgresql@15 redis python@3.11

# 2 — Create virtualenv
python3.11 -m venv venv && source venv/bin/activate

# 3 — Install packages
pip install -r requirements.txt

# 4 — Configure environment
cp .env.example .env
# Edit .env — only ANTHROPIC_API_KEY is required

# 5 — Create database
createdb prysm

# 6 — Run migrations
alembic upgrade head

# 7 — Start Redis
brew services start redis

# 8 — Start FastAPI
uvicorn app.main:app --reload --port 8000

# 9 — Start Celery worker (separate terminal)
celery -A app.workers.celery_app worker --loglevel=info -Q video,analysis,intelligence

# 10 — Verify
curl http://localhost:8000/health
```

## Model Usage

| Task | Model | Provider | Why |
|------|-------|----------|-----|
| Frame analysis | claude-opus-4-6 | Anthropic | Best vision quality |
| Scoring/Reasoning | claude-sonnet-4-6 | Anthropic | Fast, accurate reasoning |
| Chat / ARIA | claude-sonnet-4-6 | Anthropic | Fast, cost-efficient |
| Content ideas | claude-sonnet-4-6 | Anthropic | Fast generation |
| Transcription | whisper-large-v3-turbo | Groq | Fast, multilingual |
| Audio listening | gemini-3.1-pro-preview | Google | Deep audio analysis |
| Trend search | gemini-2.0-flash | Google | Google Search grounding |
| Instagram scraping | Instagram Scraper | Apify | Real engagement data |

## Cost Per Analysis

| Step | Approx Cost |
|------|------------|
| Vision (8 frames) | ~$0.02 |
| Transcription (60s video) | ~$0.01 |
| Full analysis (8 parameters) | ~$0.03 |
| **Total per analysis** | **~$0.06** |

## API Endpoints

```
POST /media/upload          Upload video file
POST /media/link            Submit video URL
GET  /analyze/{video_id}    Get full analysis
GET  /analyze/{video_id}/status  Poll job status
POST /chat                  ARIA chat endpoint
GET  /creator/{handle}      Creator profile intelligence
POST /intelligence/ideas    Generate content ideas
POST /clips/generate        Auto clip generation
GET  /viral-patterns        Viral pattern database
```
