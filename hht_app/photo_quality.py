"""Deterministic photo quality checks used before export or listing updates."""
from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageFilter, ImageStat


def assess_image(data: bytes, filename: str = "") -> dict[str, Any]:
    try:
        image = Image.open(BytesIO(data)).convert("L")
    except Exception:
        return {"filename": filename, "status": "invalid", "score": 0, "issues": ["Image could not be decoded."]}
    width, height = image.size
    stat = ImageStat.Stat(image)
    mean = float(stat.mean[0])
    variance = float(stat.var[0])
    edge_stat = ImageStat.Stat(image.filter(ImageFilter.FIND_EDGES))
    sharpness = float(edge_stat.mean[0])
    issues: list[str] = []
    if min(width, height) < 800:
        issues.append("Resolution is below 800px on the shortest side.")
    if mean < 45:
        issues.append("Photo may be too dark.")
    elif mean > 220:
        issues.append("Photo may be overexposed.")
    if sharpness < 8:
        issues.append("Photo may be blurry or lacking edge detail.")
    score = max(0, min(100, 100 - len(issues) * 25))
    return {"filename": filename, "status": "review" if issues else "pass", "score": score, "width": width, "height": height, "brightness": round(mean, 2), "sharpness": round(sharpness, 2), "issues": issues}
