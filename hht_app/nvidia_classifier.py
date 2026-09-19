from __future__ import annotations
import os, base64
from typing import Any
import requests

def classify(images: list[bytes], context: dict[str, Any] | None = None) -> dict[str, Any]:
    base = os.environ.get("NVIDIA_NIM_BASE_URL", "").strip().rstrip("/")
    key = os.environ.get("NVIDIA_NIM_API_KEY", "").strip()
    model = os.environ.get("NVIDIA_CATEGORY_MODEL", "").strip()
    if not (base and key and model):
        return {"status": "fallback", "provider": "manual_or_cpu", "confidence": "low", "message": "NVIDIA NIM is not configured; use hosted vision or seller entry."}
    content = [{"type": "text", "text": "Classify the product category and return JSON with category, attributes, confidence. Do not guess."}]
    for data in images[:3]:
        content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")}})
    try:
        response = requests.post(base + "/chat/completions", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json={"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0, "max_tokens": 500}, timeout=15)
        if response.status_code >= 400:
            return {"status": "fallback", "provider": "nvidia_nim", "confidence": "low", "message": "NVIDIA classification unavailable; use manual review."}
        return {"status": "ok", "provider": "nvidia_nim", "raw": response.json(), "confidence": "medium"}
    except requests.RequestException:
        return {"status": "fallback", "provider": "nvidia_nim", "confidence": "low", "message": "NVIDIA classification request failed; use manual review."}
