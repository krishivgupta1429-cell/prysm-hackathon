"""Built during the Meta Global AI Developer Hackathon (new work). Existing models, new integration:
Qwen2.5-VL-7B-Instruct and Q-Align (q-future/one-align) existed before the hackathon and are used unmodified;
only this deployment code is new.

Modal app "prysm-hackathon-engagement": the "Predicted Viewer Behaviour" model and a visual-quality signal.

    modal deploy app/studio/engagement_modal_app.py          # from backend/
    modal run app/studio/engagement_modal_app.py::download_weights    # once: weights into the Volume

Two GPU classes (separate images, because Q-Align's remote code needs transformers 4.36.1 and Qwen2.5-VL
needs >= 4.49):

  QwenZeroShot  Qwen2.5-VL-7B-Instruct (stock, NOT fine-tuned) asked for {"nawp", "ecr"}. This is a port of the
                old RunPod worker's logic WITHOUT its silent fallback (the old worker answered nawp=40, ecr=55 on
                any error). Any failure here raises, and the caller turns it into available=false.
                The real LMM-EVQA weights are only on Baidu Yun (docs/evqa-feasibility.md), so there is
                deliberately no evqa_predict function.
  QAlign        q-future/one-align. Video quality on the frame set as a clip, aesthetics per frame (mean).
                Native scale 1 to 5, higher is better. Non-commercial licence (S-Lab).

Weights live in a Modal Volume, containers scale to zero after a few minutes, and ENGAGEMENT_MIN_CONTAINERS
(read when you deploy) keeps containers warm for a demo. This file must stay self-contained (it runs in the
container without the rest of the repo).
"""
import os
import time

import modal

APP_NAME = "prysm-hackathon-engagement"
VOLUME_NAME = "prysm-hackathon-engagement-cache"
CACHE_DIR = "/cache"
QWEN_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"
QALIGN_MODEL = "q-future/one-align"
QWEN_REVISION = os.environ.get("ENGAGEMENT_QWEN_REVISION", "main")
QALIGN_REVISION = os.environ.get("ENGAGEMENT_QALIGN_REVISION", "main")

MIN_CONTAINERS = int(os.environ.get("ENGAGEMENT_MIN_CONTAINERS", "0"))
SCALEDOWN_S = int(os.environ.get("ENGAGEMENT_SCALEDOWN_S", "240"))
MAX_FRAMES = 8

app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

qwen_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch==2.5.1", "torchvision==0.20.1")
    .pip_install("transformers==4.49.0", "accelerate==1.3.0", "qwen-vl-utils==0.0.10", "huggingface_hub==0.27.1",
                 "pillow==11.1.0", "numpy<2")
    .env({"HF_HOME": f"{CACHE_DIR}/hf", "TOKENIZERS_PARALLELISM": "false", "HF_HUB_DISABLE_TELEMETRY": "1"})
)

# Q-Align (mPLUG-Owl2 remote code) was written for transformers 4.31 and works with 4.36.1 (its README).
qalign_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch==2.1.2", "torchvision==0.16.2")
    .pip_install("transformers==4.36.1", "tokenizers==0.15.2", "accelerate==0.25.0", "huggingface_hub==0.20.3",
                 "sentencepiece==0.1.99", "protobuf==4.25.3", "einops==0.7.0", "pillow==10.2.0", "numpy<2",
                 "timm==0.9.12", "icecream", "shortuuid")
    .env({"HF_HOME": f"{CACHE_DIR}/hf", "TOKENIZERS_PARALLELISM": "false", "HF_HUB_DISABLE_TELEMETRY": "1"})
)

# GPU preference lists (first available wins). Qwen2.5-VL-7B in bf16 needs a 24 GB card; Q-Align in fp16 fits a 16 GB T4.
QWEN_GPUS = ["L4", "A10G"]
QALIGN_GPUS = ["L4", "T4", "A10G"]

_COMMON = dict(volumes={CACHE_DIR: volume}, scaledown_window=SCALEDOWN_S, min_containers=MIN_CONTAINERS)

# ---------------------------------------------------------------- the Qwen prompt (port of the RunPod handler)

PROMPT_HEAD = (
    "You are an expert short-form video engagement analyst.\n"
    "Predict two metrics:\n"
    "- NAWP (Normalized Average Watch Percentage, 0-100): avg % of video viewers watch\n"
    "- ECR (Engagement Continuation Rate, 0-100): % who watch past 5 seconds\n"
    "Respond ONLY with valid JSON, no markdown.\n\n"
)


def build_content(frames: list, niche: str, duration: float) -> list:
    """Same sections and wording as the RunPod handler. `frames` = [{"timestamp", "image": PIL.Image}]."""
    hook = [f for f in frames if f["timestamp"] <= 3.0]
    mid = [f for f in frames if 3.0 < f["timestamp"] <= duration * 0.7]
    end = [f for f in frames if f["timestamp"] > duration * 0.7]
    content = [{"type": "text", "text": PROMPT_HEAD}]

    def section(label, fs):
        if not fs:
            return
        content.append({"type": "text", "text": label})
        for f in fs:
            content.append({"type": "image", "image": f["image"]})
            content.append({"type": "text", "text": f"[t={f['timestamp']:.1f}s]"})

    section(f"HOOK (first 3s, {len(hook)} frames):", hook)
    section(f"MID ({len(mid)} frames):", mid)
    section(f"END ({len(end)} frames):", end)
    content.append({"type": "text", "text": (
        f"\nThis is a {niche} video, {duration:.0f}s long. "
        f"Based on the frames, predict viewer retention.\n"
        f'Respond ONLY with: {{"nawp": <0-100>, "ecr": <0-100>}}')})
    return content


def parse_response(text: str) -> tuple:
    """(nawp, ecr) from the model's reply, both in (0, 100]. Raises ValueError otherwise: NO invented numbers."""
    import json
    import re
    clean = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    m = re.search(r"\{[^}]+\}", clean)
    if not m:
        raise ValueError(f"no JSON in the model reply: {text[:120]!r}")
    data = json.loads(m.group())
    try:
        nawp, ecr = float(data["nawp"]), float(data["ecr"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"reply lacks numeric nawp/ecr: {text[:120]!r}")
    if not (0 < nawp <= 100 and 0 < ecr <= 100):
        raise ValueError(f"nawp/ecr outside (0, 100]: {nawp}, {ecr}")
    return nawp, ecr


def _decode_frames(frames_in: list) -> list:
    import io
    from PIL import Image
    out = []
    for f in frames_in[:MAX_FRAMES]:
        out.append({"timestamp": float(f["timestamp"]), "image": Image.open(io.BytesIO(f["jpg"])).convert("RGB")})
    if not out:
        raise ValueError("no frames")
    return out


# ---------------------------------------------------------------- Qwen2.5-VL zero-shot

@app.cls(image=qwen_image, gpu=QWEN_GPUS, timeout=600, **_COMMON)
class QwenZeroShot:
    @modal.enter()
    def boot(self):
        import torch
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration
        t0 = time.time()
        volume.reload()
        self.model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            QWEN_MODEL, revision=QWEN_REVISION, torch_dtype=torch.bfloat16, device_map="cuda")
        # cap the pixels per frame so 8 frames fit in 24 GB with room to spare
        self.processor = AutoProcessor.from_pretrained(
            QWEN_MODEL, revision=QWEN_REVISION, min_pixels=128 * 28 * 28, max_pixels=512 * 28 * 28)
        self.gpu = torch.cuda.get_device_name(0)
        self.boot_seconds = time.time() - t0
        self.born = time.time()
        self.calls = 0

    @modal.method()
    def predict(self, frames: list, niche: str = "general", duration: float = 30.0) -> dict:
        """frames = [{"timestamp": float, "jpg": bytes}] (up to 8). Returns raw numbers and timing, or raises."""
        import torch
        from qwen_vl_utils import process_vision_info
        t0 = time.time()
        cold = self.calls == 0
        self.calls += 1
        fr = _decode_frames(frames)
        messages = [{"role": "user", "content": build_content(fr, niche, float(duration))}]
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        images, videos = process_vision_info(messages)
        inputs = self.processor(text=[text], images=images or None, videos=videos or None,
                                return_tensors="pt", padding=True).to(self.model.device)
        with torch.no_grad():
            out = self.model.generate(**inputs, max_new_tokens=64, do_sample=False, temperature=None, top_p=None,
                                      top_k=None)
        reply = self.processor.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()
        nawp, ecr = parse_response(reply)  # raises: the caller reports available=false
        run = time.time() - t0
        return {"nawp": nawp, "ecr": ecr, "raw": reply, "model": QWEN_MODEL, "frames": len(fr),
                "gpu": self.gpu, "cold": cold, "run_seconds": round(run, 3),
                "boot_seconds": round(self.boot_seconds, 3) if cold else 0.0,
                "gpu_seconds": round(run + (self.boot_seconds if cold else 0.0), 3)}


# ---------------------------------------------------------------- Q-Align (one-align)

@app.cls(image=qalign_image, gpu=QALIGN_GPUS, timeout=600, **_COMMON)
class QAlign:
    @modal.enter()
    def boot(self):
        import torch
        from transformers import AutoModelForCausalLM
        t0 = time.time()
        volume.reload()
        self.model = AutoModelForCausalLM.from_pretrained(
            QALIGN_MODEL, revision=QALIGN_REVISION, trust_remote_code=True, attn_implementation="eager",
            torch_dtype=torch.float16, device_map="cuda")
        self.gpu = torch.cuda.get_device_name(0)
        self.boot_seconds = time.time() - t0
        self.calls = 0

    @modal.method()
    def score(self, frames: list) -> dict:
        """frames = [{"timestamp", "jpg"}]. Video quality (frames as a clip) and mean per-frame aesthetics, 1 to 5."""
        import io
        import torch
        from PIL import Image
        t0 = time.time()
        cold = self.calls == 0
        self.calls += 1
        imgs = [Image.open(io.BytesIO(f["jpg"])).convert("RGB") for f in frames[:MAX_FRAMES]]
        if not imgs:
            raise ValueError("no frames")
        with torch.no_grad():
            quality = float(self.model.score([imgs], task_="quality", input_="video").flatten()[0])
            aes = self.model.score(imgs, task_="aesthetics", input_="image").flatten().float().cpu().tolist()
        run = time.time() - t0
        return {"quality": round(quality, 4), "aesthetic": round(sum(aes) / len(aes), 4),
                "aesthetic_per_frame": [round(a, 4) for a in aes], "scale": [1.0, 5.0], "model": QALIGN_MODEL,
                "frames": len(imgs), "gpu": self.gpu, "cold": cold, "run_seconds": round(run, 3),
                "boot_seconds": round(self.boot_seconds, 3) if cold else 0.0,
                "gpu_seconds": round(run + (self.boot_seconds if cold else 0.0), 3)}


# ---------------------------------------------------------------- one-time weight download (CPU only)

@app.function(image=qwen_image, volumes={CACHE_DIR: volume}, timeout=3600)
def download_weights(which: str = "both") -> dict:
    from huggingface_hub import snapshot_download
    got = {}
    if which in ("both", "qwen"):
        snapshot_download(QWEN_MODEL, revision=QWEN_REVISION, allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model"])
        got["qwen"] = QWEN_MODEL
    if which in ("both", "qalign"):
        snapshot_download(QALIGN_MODEL, revision=QALIGN_REVISION)
        got["qalign"] = QALIGN_MODEL
    volume.commit()
    return got


@app.function(image=qwen_image, timeout=120)
def ping() -> dict:
    return {"ok": True, "min_containers": MIN_CONTAINERS, "scaledown_s": SCALEDOWN_S}
