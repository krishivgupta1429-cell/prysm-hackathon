# RunPod Endpoint: Qwen2.5-VL-32B-Instruct-AWQ

Deploy this once. Takes ~5 min in the RunPod web UI. The comparison script reads
the endpoint ID from `QWEN32B_ENDPOINT` in the `.env` file.

---

## GPU Selection

| GPU | VRAM | AWQ 32B fit? | RunPod serverless $/hr | Verdict |
|-----|------|-------------|------------------------|---------|
| RTX 4090 | 24 GB | NO (too small) | ~$0.74 | ✗ |
| A40 | 48 GB | YES (weights=19GB, KV headroom=29GB) | ~$0.79 | ✓ Use this |
| A100 SXM 40GB | 40 GB | YES (tight) | ~$2.09 | Overkill |
| A100 SXM 80GB | 80 GB | YES (plenty) | ~$3.49 | Overkill |

**Use: A40 48GB.** AWQ INT4 weights are ~19 GB. With max_model_len=8192 and
max_num_seqs=4, KV cache needs ~4 GB — 29 GB of VRAM headroom is comfortable.

---

## Steps

1. Go to https://www.runpod.io/console/serverless
2. Click **New Endpoint**
3. Select template: **vLLM** (RunPod's official template — "RunPod vLLM Worker")
4. Configure:
   - **Name:** `qwen32b-scorer`
   - **GPU:** A40 48GB (select "NVIDIA A40")
   - **Min workers:** 0 (scale to zero when idle)
   - **Max workers:** 3
   - **Active workers:** 0

5. Under **Container / Environment Variables**, set:

| Variable | Value |
|----------|-------|
| `MODEL_NAME` | `Qwen/Qwen2.5-VL-32B-Instruct-AWQ` |
| `MAX_MODEL_LEN` | `8192` |
| `GPU_MEMORY_UTILIZATION` | `0.92` |
| `QUANTIZATION` | `awq` |
| `MAX_NUM_SEQS` | `4` |
| `DTYPE` | `auto` |

6. Click **Deploy**
7. Wait for the endpoint to appear in the Serverless dashboard (usually 1-2 min)
8. Copy the **Endpoint ID** (looks like `abc1def2ghi3jkl4`)

---

## After Deployment

Add to `scripts/niche_research/.env`:

```
QWEN32B_ENDPOINT=<your-endpoint-id>
QWEN32B_MODEL=Qwen/Qwen2.5-VL-32B-Instruct-AWQ
```

The first job will cold-start the worker (30-90s to download 19GB weights from
HuggingFace). Subsequent jobs on warm workers are fast (~15-30s per reel).

To verify the endpoint works:
```bash
curl -s "https://api.runpod.ai/v2/<endpoint-id>/health" \
  -H "Authorization: Bearer $LLM_API_KEY"
```

---

## Run the comparison

```bash
cd ~/Desktop/"Prysm(frontend + backend)"/"prysm-backend 3"
source venv/bin/activate

# Sonnet half (can run now, before endpoint is up)
python -m scripts.niche_research.model_compare --model sonnet

# Qwen32B half (after endpoint is deployed and QWEN32B_ENDPOINT is set in .env)
source scripts/niche_research/.env && \
python -m scripts.niche_research.model_compare --model qwen

# Build final report (both halves complete)
python -m scripts.niche_research.model_compare --report-only
```

Results land in `scripts/niche_research/results/model_compare/`.
Each reel produces `<code>_sonnet.json` and `<code>_qwen32b.json`.

---

## Cost estimate

| Item | Estimate |
|------|----------|
| Sonnet 4.6: 20 reels × ~1,800 in + ~1,800 out tokens | ~$0.03 |
| A40 cold-start (first worker) | ~15-25s |
| A40 warm calls: 20 reels × ~20s | ~400s |
| A40 total billed: ~425s × $0.000590/s | ~$0.25 |
| **Total** | **~$0.28** |
