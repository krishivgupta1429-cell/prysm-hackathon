# Niche research — empirical parameter weights

A standalone tool that scores Instagram reels through a **local LLM** (Ollama or vLLM)
across the 10 Prysm parameters, then aggregates results per niche to derive
**data-driven weight tables** — replacing the opinion-based defaults in production.

This does not run as part of the live API. It's an offline research pipeline you
run on a machine that has Ollama or vLLM installed.

---

## What it does

For each `(url, niche)` row in your input CSV:

1. **Fetch metadata** — Apify for Instagram, yt-dlp for everything else
2. **Download the video** to a local cache (`WORK_DIR`)
3. **Extract** frames (FFmpeg, hook + body) and audio (FFmpeg → mp3)
4. **Transcribe** the audio (Groq Whisper, local whisper.cpp, or skip)
5. **Pass 1 — vision** — local vision LLM observes frames, outputs structured description
6. **Pass 2 — scoring** — local LLM scores all 10 parameters using vision summary + transcript + post metadata
7. **Save** the result as one entry in `results/per_video_<niche>.json`

Once all videos are processed, the **aggregator** produces:

- `results/aggregate_<niche>.json` — per-parameter mean / median / stddev / correlation with view count
- `results/niche_weights.md` — cross-niche markdown comparison table with **empirical weights** (blend of mean score and view-count correlation)

The empirical weights are what you'd plug back into the live backend's prompt.

---

## Setup

### 1. Install Ollama (recommended) or vLLM

**Ollama** — single binary install:
```bash
brew install ollama        # macOS
ollama serve &
ollama pull qwen2.5vl:7b   # ~5GB; best vision quality at this size
```

**vLLM** — for GPU machines, OpenAI-compatible serving:
```bash
pip install vllm
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-VL-7B-Instruct --port 8000
```

### 2. Install Python deps

```bash
cd "prysm-backend 3"
pip install -r scripts/niche_research/requirements.txt
```

(The other backend deps from `requirements.txt` are NOT needed — this tool
runs standalone.)

### 3. Configure

```bash
cp scripts/niche_research/.env.example scripts/niche_research/.env
$EDITOR scripts/niche_research/.env
```

Required:
- `APIFY_API_TOKEN` — needed for Instagram URLs (yt-dlp fails on most reels)
- `GROQ_API_KEY` if `TRANSCRIBE_BACKEND=groq` (default; fastest)

Defaults work for everything else. Switch backends with `LLM_BACKEND=vllm` and
update `LLM_BASE_URL` / `LLM_MODEL` accordingly.

---

## Run

```bash
cd "prysm-backend 3"

# Dry-run — show what would be processed
python -m scripts.niche_research.analyse --input scripts/niche_research/sample_input.csv --dry-run

# Real run
python -m scripts.niche_research.analyse --input scripts/niche_research/sample_input.csv

# Limit to specific niches
python -m scripts.niche_research.analyse --input my_videos.csv --niche dance --niche comedy

# Re-aggregate without re-scoring (after you tweak the aggregation logic)
python -m scripts.niche_research.analyse --aggregate-only

# Force re-analysis even of already-done URLs
python -m scripts.niche_research.analyse --input my_videos.csv --no-resume
```

---

## Input format

`input.csv` (header required):

```csv
url,niche,view_count
https://www.instagram.com/reel/Cxxxxx/,dance,1200000
https://www.instagram.com/reel/Cyyyyy/,fitness,84000
https://www.youtube.com/shorts/Czzzzz,comedy,500
```

- `url` — any URL yt-dlp can handle, plus Instagram (which goes through Apify).
- `niche` — free-form lowercase tag. Use any name; it's just a grouping key.
- `view_count` — optional. If empty, the tool reads it from Apify/yt-dlp. We
  recommend leaving it empty so the live metadata is used.

---

## Recommended sample sizes

For meaningful niche weights:

| Sample size | What you get |
|---|---|
| 5 videos / niche | Sanity check that the pipeline works for that niche. Numbers are unstable. |
| 20 videos / niche | Stable means. Correlations still noisy. Weights are usable as a tuning prior. |
| 50+ videos / niche | Correlations become trustworthy. Weights are publishable. |
| 100+ videos / niche | You can credibly say "in this niche, parameter X drives virality more than Y." |

Mix performance levels — include both viral (>500K views) and underperforming (<10K) videos in each niche so the correlation analysis has signal to work with. **A sample of only viral videos cannot reveal what separates viral from non-viral.**

---

## Output

After a run:

```
scripts/niche_research/results/
├── per_video_dance.json        # raw scores per video
├── per_video_fitness.json
├── aggregate_dance.json        # per-niche statistics
├── aggregate_fitness.json
└── niche_weights.md            # human-readable cross-niche comparison
```

Open `niche_weights.md` for the headline output — that's the table you use to
update the live backend's per-niche weight schema.

---

## Cost

Per video, with default settings:

| Step | Cost |
|---|---|
| Apify scrape (Instagram) | ~$0.0008 |
| Video download | bandwidth only |
| FFmpeg frames + audio | local compute |
| Groq Whisper transcript | ~$0.0001 |
| Ollama vision pass | $0 (local) |
| Ollama scoring pass | $0 (local) |
| **Total** | **~$0.001 per video** |

So 1,000 videos costs ~$1 of cloud spend, plus your local GPU/CPU time. The
local LLM passes are free in dollars but cost wall-clock time — typical ~30–90s
per video on consumer hardware, ~10–20s on a decent GPU.

---

## How the empirical weights are derived

For each parameter in each niche, we compute:

- `mean` — average score across all videos in that niche
- `corr_views` — Pearson correlation between this parameter's score and `log10(viewCount + 1)`

We then blend them:

```
raw_score(param) = 0.7 * (mean / 100) + 0.3 * max(0, corr_views)
weight(param)    = raw_score(param) / sum(raw_scores) * 100
```

Why the blend:
- **Mean** captures *what good videos in this niche look like overall*. Trustworthy even with small samples.
- **Correlation** captures *which parameter actually predicts virality* in this niche. Sensitive — needs ≥30 videos to be reliable.
- 70/30 weighting biases toward stability when data is sparse, but lets correlation pull the weights toward what really matters as samples grow.

You can change the blend in [aggregator.py:87](aggregator.py) if you want to favour correlation more aggressively once you have enough data.
