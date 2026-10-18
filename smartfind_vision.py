"""Image perception: YOLO object detection, optional OCR, and pixel-color suggestions.

YOLO supplies an item category and bounding box; it does not identify an individual
item or prove ownership. OCR extracts candidate text only (it cannot recognize logos).
"""
from functools import lru_cache
from pathlib import Path
import io
import os
import re

MODEL_PATH = Path(os.environ.get("SMARTFIND_MODEL_PATH", Path(__file__).parent / "models" / "best.pt"))
CONFIDENCE_THRESHOLD = float(os.environ.get("SMARTFIND_DETECTION_CONFIDENCE", "0.40"))
RECOGNIZER_MODE = "model" if MODEL_PATH.is_file() else "unavailable"
AI_CATEGORIES = {}

_COLOR_PROTOTYPES = {
    "Black": (25, 25, 25), "White": (235, 235, 235), "Gray": (128, 128, 128),
    "Silver": (190, 195, 200), "Red": (210, 35, 45), "Orange": (235, 130, 35),
    "Yellow": (235, 215, 45), "Green": (45, 145, 65), "Blue": (40, 85, 200),
    "Purple": (125, 65, 160), "Pink": (225, 125, 165), "Brown": (125, 80, 45),
}


@lru_cache(maxsize=1)
def _load_model():
    if not MODEL_PATH.is_file():
        raise FileNotFoundError(f"YOLO model was not found: {MODEL_PATH}")
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("YOLO dependencies are missing. Install requirements.txt.") from exc
    return YOLO(str(MODEL_PATH))


def get_model_categories():
    """Return class names embedded in the trained model, or an empty list if unavailable."""
    try:
        names = _load_model().names
    except Exception:
        # Metadata should not prevent the app from starting if model loading fails;
        # image analysis reports the actionable error when a photo is submitted.
        return []
    if isinstance(names, dict):
        return [str(names[k]) for k in sorted(names)]
    return [str(name) for name in names]


def classify_image(image_bytes, filename="uploaded-image"):
    """Detect the most confident item and analyze its crop.

    Returns category, confidence (percent), color suggestion, OCR text candidate,
    bounding box, and source. Missing optional OCR support does not block detection.
    """
    if not image_bytes:
        return _empty_result("No image was provided")

    try:
        from PIL import Image, ImageOps
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    except Exception as exc:
        raise ValueError("The uploaded file is not a readable image.") from exc

    model = _load_model()
    results = model.predict(source=image, conf=CONFIDENCE_THRESHOLD, verbose=False)
    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return _empty_result("No supported object detected")

    best_i = int(boxes.conf.argmax().item())
    box = boxes[best_i]
    class_id = int(box.cls.item())
    names = model.names
    category = str(names[class_id] if isinstance(names, dict) else names[class_id])
    confidence = float(box.conf.item()) * 100
    x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
    x1, x2 = max(0, x1), min(image.width, x2)
    y1, y2 = max(0, y1), min(image.height, y2)
    crop = image.crop((x1, y1, x2, y2))
    if crop.width and crop.height:
        # Ignore a narrow amount of box-edge background when estimating the item color.
        inset = (int(crop.width * .08), int(crop.height * .08),
                 int(crop.width * .92), int(crop.height * .92))
        inner = crop.crop(inset) if inset[2] > inset[0] and inset[3] > inset[1] else crop
        color = estimate_dominant_color(inner)
        brand_text = read_text_candidate(crop)
    else:
        color, brand_text = None, None

    return {"category": category, "confidence": confidence, "color": color,
            "brand": brand_text, "bbox": (x1, y1, x2, y2), "source": "model",
            "message": "YOLO category suggestion; review it before saving."}


def estimate_dominant_color(image):
    """Estimate a basic color from pixels; return None for near-uniformly ambiguous images."""
    small = image.convert("RGB")
    small.thumbnail((80, 80))
    pixels = list(small.get_flattened_data())
    if not pixels:
        return None
    # Quantize colors into coarse bins, then classify the most frequent non-transparent pixel.
    counts = {}
    for pixel in pixels:
        key = tuple((channel // 32) * 32 + 16 for channel in pixel)
        counts[key] = counts.get(key, 0) + 1
    dominant = max(counts, key=counts.get)
    name = min(_COLOR_PROTOTYPES, key=lambda n: sum(
        (dominant[i] - _COLOR_PROTOTYPES[n][i]) ** 2 for i in range(3)))
    return name


def read_text_candidate(image):
    """Use optional Tesseract OCR to read text; it is a candidate, never asserted as a brand."""
    try:
        import pytesseract
        from pytesseract import Output
        data = pytesseract.image_to_data(image, output_type=Output.DICT, config="--psm 11")
    except (ImportError, OSError, RuntimeError):
        return None
    candidates = []
    for text, confidence in zip(data.get("text", []), data.get("conf", [])):
        text = re.sub(r"[^\w&+.-]", "", str(text), flags=re.UNICODE).strip("._-")
        try:
            score = float(confidence)
        except (TypeError, ValueError):
            continue
        if len(text) >= 2 and score >= 30:
            candidates.append((score, text))
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1][:40]


def _empty_result(message):
    return {"category": None, "confidence": None, "color": None, "brand": None,
            "bbox": None, "source": "model", "message": message}
