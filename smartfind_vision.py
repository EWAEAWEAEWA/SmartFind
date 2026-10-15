"""
SmartFind PERCEIVE layer (pure Python — no Streamlit, so it can be unit-tested).

PHASE 1 SCOPE: photo recognition is planned for CALCULATORS ONLY.
  * No real model is connected yet. Until one is, RECOGNIZER_MODE stays "placeholder",
    which only looks at the FILE NAME (e.g. "casio_calculator.jpg"). That is not AI and
    the UI labels it as a placeholder.
  * Every other item type (smartphone, ID card, keys, ...) is reported manually.
  * To add a category later: add it to AI_CATEGORIES (and make the model output it).
    Nothing else in the app needs to be redesigned.
"""
import re

# "placeholder" = guesses from the FILE NAME only (not real AI).
# Switch to "model" only after _real_model_classify() runs an actual model on the image.
RECOGNIZER_MODE = "placeholder"

# Categories the recognizer supports. Phase 1 = Calculator only. Edit to add more later.
# "keywords" are used ONLY by the placeholder (matched against the file name).
AI_CATEGORIES = {
    "Calculator": {"icon": "🧮", "keywords": ["calculator", "calc", "casio", "scientific"]},
}

COLOR_WORDS = ["black", "white", "silver", "blue", "red", "gold", "gray", "grey", "green", "brown", "pink"]


def classify_image(image_bytes, filename):
    """Return {"category": str|None, "confidence": float|None, "color": str|None, "source": str}.

    category is None when nothing in AI_CATEGORIES was recognized.
    confidence is a percentage (0-100) from a real model, or None for the placeholder.
    Raises NotImplementedError if RECOGNIZER_MODE == "model" but no model is connected."""
    if RECOGNIZER_MODE == "model":
        return _real_model_classify(image_bytes)
    return _placeholder_classify(filename)


def _placeholder_classify(filename):
    """NOT AI. Looks for keywords in the file name. Never returns a confidence value."""
    tokens = set(re.findall(r"[a-z]+", str(filename).lower()))
    category = next((c for c, v in AI_CATEGORIES.items() if tokens & set(v["keywords"])), None)
    color = next((c for c in COLOR_WORDS if c in tokens), None)
    return {"category": category, "confidence": None,
            "color": color.title() if color else None, "source": "placeholder"}


def _real_model_classify(image_bytes):
    """TODO: run the trained calculator model on image_bytes.
    Return the same dict shape as _placeholder_classify, with source="model" and confidence in 0-100.
    Return category=None when the image is not a supported item or confidence is too low."""
    raise NotImplementedError("No image-recognition model is connected yet.")
