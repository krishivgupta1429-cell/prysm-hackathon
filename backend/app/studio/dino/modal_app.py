"""Built during the Meta Global AI Developer Hackathon (new work). Existing model, new integration:
facebook/dinov2-base (Apache 2.0) existed before the hackathon and is used unmodified; only this deployment code is new.

Modal app "prysm-hackathon-dino": a batched DINOv2 frame embedder.

    modal secret create prysm-hackathon-dino-token DINO_TOKEN=<random string>      # once
    modal deploy app/studio/dino/modal_app.py                                       # from backend/

Two pieces:
  Dino  (GPU class, L4 then A10G)  CLS embedding per frame, L2-normalised, 768-d. Weights are baked into the image at
        build time (no Volume). Containers scale to zero after DINO_SCALEDOWN_S; DINO_MIN_CONTAINERS (read at deploy
        time) keeps one warm for a demo.
  api   (CPU web app)  GET /health (no GPU wake-up, no auth) and POST /embed (bearer token). It forwards to Dino.

POST /embed  {"frames": ["<base64 jpeg>", ...]}  (up to 64 frames, about 224 px on the short side)
  -> {"available": true, "dim": 768, "embeddings": [[...], ...], "model", "gpu", "cold", "run_seconds",
      "boot_seconds", "gpu_seconds"}
  Any failure is an HTTP error with {"available": false, "error": "<plain reason>"}: there is NO fallback and no
  invented vector. This file must stay self-contained (it runs in the container without the rest of the repo).
"""
import hmac
import os
import time

import modal

APP_NAME = "prysm-hackathon-dino"
SECRET_NAME = "prysm-hackathon-dino-token"
MODEL_ID = "facebook/dinov2-base"
MODEL_DIR = "/models"
MAX_FRAMES = 64
MAX_FRAME_BYTES = 1_500_000
BATCH = 32
SHORT_SIDE = 224
MAX_LONG_SIDE = 392  # 28 patches of 14 px
MIN_CONTAINERS = int(os.environ.get("DINO_MIN_CONTAINERS", "0"))
SCALEDOWN_S = int(os.environ.get("DINO_SCALEDOWN_S", "120"))
GPUS = ["L4", "A10G"]

app = modal.App(APP_NAME)


def download_model():
    from huggingface_hub import snapshot_download
    snapshot_download(MODEL_ID, local_dir=f"{MODEL_DIR}/dinov2-base",
                      allow_patterns=["*.json", "*.safetensors", "*.txt"])


gpu_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch==2.5.1", "transformers==4.49.0", "pillow==11.1.0", "numpy<2", "huggingface_hub==0.27.1")
    .env({"HF_HUB_DISABLE_TELEMETRY": "1", "TOKENIZERS_PARALLELISM": "false"})
    .run_function(download_model)
)
api_image = modal.Image.debian_slim(python_version="3.11").pip_install("fastapi[standard]==0.115.6")


def plain_errors(fn):
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            raise RuntimeError(f"{type(e).__name__}: {str(e)[:300]}") from None
    return wrapper


def target_size(w: int, h: int) -> tuple[int, int]:
    """(W, H) multiples of 14, short side about 224, long side at most 392, aspect ratio kept."""
    s = SHORT_SIDE / min(w, h)
    tw, th = w * s, h * s
    if max(tw, th) > MAX_LONG_SIDE:
        k = MAX_LONG_SIDE / max(tw, th)
        tw, th = tw * k, th * k
    snap = lambda v: max(14, int(round(v / 14.0)) * 14)
    return snap(tw), snap(th)


@app.cls(image=gpu_image, gpu=GPUS, timeout=300, scaledown_window=SCALEDOWN_S, min_containers=MIN_CONTAINERS)
class Dino:
    @modal.enter()
    def boot(self):
        import torch
        from transformers import AutoModel
        t0 = time.time()
        self.torch = torch
        self.model = AutoModel.from_pretrained(f"{MODEL_DIR}/dinov2-base", torch_dtype=torch.float16).to("cuda").eval()
        self.gpu = torch.cuda.get_device_name(0)
        self.mean = torch.tensor([0.485, 0.456, 0.406], device="cuda").view(1, 3, 1, 1)
        self.std = torch.tensor([0.229, 0.224, 0.225], device="cuda").view(1, 3, 1, 1)
        self.boot_seconds = time.time() - t0
        self.calls = 0

    @modal.method()
    @plain_errors
    def embed(self, jpegs: list) -> dict:
        """jpegs = list of JPEG bytes. All frames are resized to the geometry of the first one (batching needs one size).
        Returns the 768-d CLS embedding of each frame, L2-normalised, with timing. Raises on any failure."""
        import io
        import numpy as np
        from PIL import Image
        torch = self.torch
        t0 = time.time()
        cold = self.calls == 0
        self.calls += 1
        if not jpegs:
            raise ValueError("no frames")
        imgs = [Image.open(io.BytesIO(b)).convert("RGB") for b in jpegs]
        W, H = target_size(*imgs[0].size)
        arr = np.stack([np.asarray(im.resize((W, H), Image.BICUBIC), dtype=np.uint8) for im in imgs])
        out = []
        with torch.no_grad():
            for i in range(0, len(arr), BATCH):
                x = torch.from_numpy(arr[i:i + BATCH]).to("cuda").permute(0, 3, 1, 2).float() / 255.0
                x = ((x - self.mean) / self.std).half()
                cls = self.model(pixel_values=x).last_hidden_state[:, 0].float()
                cls = torch.nn.functional.normalize(cls, dim=1)
                out.append(cls.cpu().numpy())
        emb = np.concatenate(out)
        if not np.isfinite(emb).all():
            raise ValueError("non-finite values in the embedding")
        run = time.time() - t0
        return {"embeddings": np.round(emb, 6).tolist(), "dim": int(emb.shape[1]), "model": MODEL_ID, "frames": len(imgs),
                "input_size": [W, H], "gpu": self.gpu, "cold": cold, "run_seconds": round(run, 3),
                "boot_seconds": round(self.boot_seconds, 3) if cold else 0.0,
                "gpu_seconds": round(run + (self.boot_seconds if cold else 0.0), 3)}


@app.function(image=api_image, secrets=[modal.Secret.from_name(SECRET_NAME)], scaledown_window=60, timeout=600)
@modal.asgi_app()
def api():
    import base64
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    web = FastAPI(title="prysm-hackathon-dino")

    def fail(status: int, msg: str):
        return JSONResponse({"available": False, "error": msg}, status_code=status)

    @web.get("/health")
    def health():
        # Does not wake the GPU: it says the service is up and what it will run.
        return {"ok": True, "model": MODEL_ID, "dim": 768, "gpus": GPUS, "max_frames": MAX_FRAMES,
                "min_containers": MIN_CONTAINERS, "scaledown_s": SCALEDOWN_S, "token_configured": bool(os.environ.get("DINO_TOKEN"))}

    @web.post("/embed")
    async def embed(request: Request):
        expected = os.environ.get("DINO_TOKEN") or ""
        if not expected:
            return fail(503, "the service has no token configured")  # fail closed
        got = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(got.encode(), expected.encode()):
            return fail(401, "bad or missing token")
        try:
            body = await request.json()
            frames = body["frames"]
            if not isinstance(frames, list) or not frames:
                return fail(400, "frames must be a non-empty list of base64 JPEGs")
            if len(frames) > MAX_FRAMES:
                return fail(413, f"at most {MAX_FRAMES} frames per request")
            jpegs = [base64.b64decode(f, validate=True) for f in frames]
            if any(len(j) > MAX_FRAME_BYTES or j[:2] != b"\xff\xd8" for j in jpegs):
                return fail(400, "each frame must be a JPEG of at most 1.5 MB")
        except Exception as e:
            return fail(400, f"bad request: {type(e).__name__}")
        try:
            res = await Dino().embed.remote.aio(jpegs)
        except Exception as e:
            return fail(502, f"embedding failed: {str(e)[:300]}")
        return {"available": True, **res}

    return web
