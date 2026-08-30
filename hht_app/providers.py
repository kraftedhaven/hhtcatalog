import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import requests

from .schema import normalize_listing, parse_model_json


PROMPT = """You are an eBay listing assistant. Inspect every supplied clothing, shoe, or bag photo.
Return one concise JSON object only with these keys:
title, price, cid, cnote, cat, brand, size, color, dept, type, style, mat, pat,
slv, nk, sea, occ, st, vin, desc, notes, madeIn, serialNumber, measurements.
Use Not visible when evidence is missing. Do not guess brand, size, material,
country, serial number, measurements, condition, category, authenticity, or vintage.
Use only these category IDs when confident: women's tops 15724; dresses 63861;
women's jeans/pants 63867; women's sweaters/cardigans 11484; jackets/coats 57988;
skirts 63866; activewear pants/leggings 185100; men's T-shirts 15687; men's jeans
11483; men's casual shirts/polos 57990; men's sweaters/hoodies 11484; men's
sweatshirts/hoodies 155183; men's casual shoes 93427; handbags 169291; backpacks
169284. Bags need sleeve length, neckline, size, and size type as N/A - bag. Shoes
need sleeve length and neckline as N/A - footwear. Never claim luxury authentication."""


class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, failures: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.failures = failures or []


@dataclass
class UploadedImage:
    data: bytes
    mime_type: str
    filename: str = "image.jpg"

    @property
    def data_url(self) -> str:
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


def configured_providers() -> dict[str, bool]:
    return {
        "openrouter": bool(os.environ.get("OPENROUTER_API_KEY")),
        "gemini": bool(os.environ.get("GEMINI_API_KEY")),
        "groq": bool(os.environ.get("GROQ_API_KEY")),
    }


def demo_mode() -> bool:
    return os.environ.get("DEMO_MODE", "false").lower() in {"1", "true", "yes", "on"}


def analyze_images(images: list[UploadedImage], context: dict[str, Any] | None = None) -> dict[str, Any]:
    if not images:
        raise ProviderError("At least one image is required.", 400)
    context = context or {}
    context.setdefault("deadline", time.monotonic() + 24)
    compact_images = images[:3]
    failures: list[dict[str, str]] = []
    providers = _provider_plan()
    if not providers:
        if demo_mode():
            return _demo_listing()
        raise ProviderError(
            "Hosted analysis is not configured. Set OPENROUTER_API_KEY, GEMINI_API_KEY, or GROQ_API_KEY in Heroku Config Vars, or enable DEMO_MODE=true for development only.",
            503,
        )

    for name, caller in providers:
        if _remaining_seconds(context) < 5:
            failures.append({"provider": name, "error": "timeout_budget_exhausted"})
            continue
        try:
            raw = caller(compact_images, context)
            result = normalize_listing(parse_model_json(raw))
            result["provider"] = name
            result["demo"] = False
            return result
        except ProviderError as exc:
            failures.append({"provider": name, "error": _safe_error(str(exc))})
            _log_provider(name, exc.status_code, str(exc))
        except Exception as exc:
            failures.append({"provider": name, "error": _safe_error(str(exc))})
            _log_provider(name, 502, str(exc))

    if demo_mode():
        demo = _demo_listing()
        demo["providerFailures"] = failures
        return demo
    raise ProviderError("All configured vision providers failed. No demo listing was generated.", 502, failures)


def _provider_plan():
    plan = []
    if os.environ.get("OPENROUTER_API_KEY"):
        plan.append(("openrouter", _openrouter))
    if os.environ.get("GEMINI_API_KEY"):
        plan.append(("gemini", _gemini))
    if os.environ.get("GROQ_API_KEY"):
        plan.append(("groq", _groq))
    return plan


def _openrouter(images: list[UploadedImage], context: dict[str, Any]) -> str:
    content = [{"type": "text", "text": _prompt(context)}]
    content.extend({"type": "image_url", "image_url": {"url": image.data_url}} for image in images)
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://hht-catalog-b34ed1b32417.herokuapp.com/",
            "X-Title": "HHT Catalog",
        },
        json={
            "model": os.environ.get("OPENROUTER_MODEL", "openrouter/free"),
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
            "max_tokens": 1400,
            "response_format": {"type": "json_object"},
        },
        timeout=_request_timeout(context),
    )
    return _chat_response(response)


def _gemini(images: list[UploadedImage], context: dict[str, Any]) -> str:
    parts: list[dict[str, Any]] = [{"text": _prompt(context)}]
    for image in images:
        parts.append({
            "inline_data": {
                "mime_type": image.mime_type,
                "data": base64.b64encode(image.data).decode("ascii"),
            }
        })
    model = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]},
        json={
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 1400,
                "responseMimeType": "application/json",
            },
        },
        timeout=_request_timeout(context),
    )
    if response.status_code >= 400:
        raise ProviderError(f"Gemini returned HTTP {response.status_code}", response.status_code)
    body = response.json()
    return "".join(part.get("text", "") for part in body.get("candidates", [{}])[0].get("content", {}).get("parts", []))


def _groq(images: list[UploadedImage], context: dict[str, Any]) -> str:
    model = os.environ.get("GROQ_MODEL") or "meta-llama/llama-4-scout-17b-16e-instruct"
    content = [{"type": "text", "text": _prompt(context)}]
    content.extend({"type": "image_url", "image_url": {"url": image.data_url}} for image in images[:2])
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
            "max_completion_tokens": 1200,
            "response_format": {"type": "json_object"},
        },
        timeout=_request_timeout(context),
    )
    return _chat_response(response)


def _chat_response(response: requests.Response) -> str:
    if response.status_code >= 400:
        raise ProviderError(f"Provider returned HTTP {response.status_code}", response.status_code)
    body = response.json()
    message = body.get("choices", [{}])[0].get("message", {})
    content = message.get("content")
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not content:
        raise ProviderError("Provider returned no analysis.", 502)
    return content


def _prompt(context: dict[str, Any]) -> str:
    defaults = context.get("seller_defaults") or {}
    location = defaults.get("location") or "Kettering, Ohio"
    return f"{PROMPT}\nSeller location for description: {location}. Keep desc under 700 characters."


def _demo_listing() -> dict[str, Any]:
    result = normalize_listing({
        "title": "Demo No Brand Denim Jacket",
        "price": 24.99,
        "cid": "3000",
        "cnote": "Demo data only. Replace with seller-reviewed condition notes.",
        "cat": "57988",
        "brand": "No Brand",
        "size": "Not visible",
        "color": "Blue",
        "dept": "Unisex Adults",
        "type": "Jacket",
        "style": "Casual",
        "mat": "Not visible",
        "pat": "Solid",
        "notes": "DEMO MODE: this listing was not generated from the uploaded item.",
    })
    result["provider"] = "demo"
    result["demo"] = True
    return result


def _safe_error(message: str) -> str:
    lowered = message.lower()
    if "429" in lowered or "rate" in lowered:
        return "rate_limit"
    if "401" in lowered or "403" in lowered or "key" in lowered:
        return "auth_or_permission"
    if "json" in lowered:
        return "malformed_json"
    return "provider_error"


def _log_provider(provider: str, status_code: int, message: str) -> None:
    print(f"[provider] {provider} status={status_code} category={_safe_error(message)}")


def _remaining_seconds(context: dict[str, Any]) -> float:
    return max(0.0, float(context.get("deadline", time.monotonic())) - time.monotonic())


def _request_timeout(context: dict[str, Any]) -> float:
    remaining = _remaining_seconds(context)
    if remaining < 5:
        raise ProviderError("Provider timeout budget exhausted before request.", 504)
    return min(12.0, max(3.0, remaining - 2.0))
