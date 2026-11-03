"""Built during the Meta Global AI Developer Hackathon (new work).

Face boxes for keeping the subject in frame during zoom punches and keeping text off the face. Uses the YuNet face detector
(MIT licence, 230 kB ONNX file bundled in models/) through OpenCV; if that cannot load, OpenCV's Haar cascade. When nothing is found
the callers fall back to the centre of the frame. Coordinates are normalised 0-1 in the SOURCE frame.
"""
from pathlib import Path

from app.studio.autoedit import ffx

MODEL = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"
_cascade = None
_yunet = {}


def _get_cascade():
    global _cascade
    if _cascade is None:
        import cv2
        _cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return _cascade


def _detect_yunet(img) -> list[tuple]:
    import cv2
    h, w = img.shape[:2]
    det = _yunet.get((w, h))
    if det is None:
        det = cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), 0.6, 0.3, 50)
        _yunet.clear()
        _yunet[(w, h)] = det
    _, res = det.detect(img)
    return [(float(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in (res if res is not None else [])]


def detect_in_image(path: str | Path) -> dict | None:
    """The largest face in an image as {cx, cy, w, h} (normalised), or None."""
    import cv2
    img = cv2.imread(str(path))
    if img is None:
        return None
    h, w = img.shape[:2]
    try:
        boxes = _detect_yunet(img)
        if boxes:
            x, y, fw, fh = max(boxes, key=lambda b: b[2] * b[3])
            return {"cx": round(min(max((x + fw / 2) / w, 0), 1), 4), "cy": round(min(max((y + fh / 2) / h, 0), 1), 4),
                    "w": round(fw / w, 4), "h": round(fh / h, 4)}
    except Exception:
        pass
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = _get_cascade().detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(int(w * 0.08), int(w * 0.08)))
    if len(faces) == 0:
        return None
    x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
    return {"cx": round((x + fw / 2) / w, 4), "cy": round((y + fh / 2) / h, 4), "w": round(fw / w, 4), "h": round(fh / h, 4)}


def sample_faces(video: str | Path, work: str | Path, times: list[float]) -> list[dict]:
    """Face boxes at the given source times: [{t, cx, cy, w, h}] (times with no face are left out)."""
    out = []
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(times):
        p = work / f"face_{i:03d}.jpg"
        try:
            ffx.frame_at(video, t, p, width=480)
            f = detect_in_image(p)
        except Exception:
            f = None
        if f:
            out.append({"t": round(t, 3), **f})
        try:
            p.unlink()
        except OSError:
            pass
    return out


def nearest(faces: list[dict], t: float, max_gap: float = 1.5) -> dict | None:
    cand = [f for f in faces if abs(f["t"] - t) <= max_gap]
    return min(cand, key=lambda f: abs(f["t"] - t)) if cand else None
