import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests
from PIL import Image, UnidentifiedImageError

from .ebay_pricing import enrich_with_ebay_active_pricing
from .schema import normalize_listing, parse_model_json


def _register_heic_support() -> bool:
    try:
        from pillow_heif import register_heif_opener
    except ImportError:
        return False
    register_heif_opener()
    return True


HEIC_SUPPORT_ENABLED = _register_heic_support()


ZAI_DEFAULT_BASE_URL = "https://api.z.ai/api/paas/v4/"
ZAI_DEFAULT_MODEL = "glm-4.6v-flash"
MAX_PROVIDER_IMAGES = 5
MAX_ZAI_REQUEST_BYTES = 7 * 1024 * 1024
TRANSIENT_STATUS_CODES = {429, 502, 503}
DEFAULT_ANALYZE_DEADLINE_SECONDS = 28.0
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 18.0
ZAI_IMAGE_MAX_EDGE = 896
ZAI_IMAGE_RETRY_MAX_EDGE = 640
ZAI_IMAGE_QUALITY = 72
ZAI_IMAGE_RETRY_QUALITY = 64

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

ZAI_PROMPT = """Inspect all supplied resale item photos and return one JSON object only.
Fill these exact keys: title, price, cid, cnote, cat, brand, size, color, dept,
type, style, mat, pat, slv, nk, sea, occ, st, vin, desc, notes, madeIn,
serialNumber, measurements. Use close-up labels/tags for brand, size, material,
origin, serial, and measurements; use Not visible when evidence is missing.
Set price as a conservative Buy It Now estimate from visible item type, brand,
condition, and materials only. Do not claim checked sold comps or sold-through
data. Put pricing uncertainty in notes. Never claim luxury authentication."""


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int = 502,
        failures: list[dict[str, Any]] | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        category: str | None = None,
        retryable: bool = False,
        safe_message: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.failures = failures or []
        self.provider = provider
        self.model = model
        self.category = category or _category_for_status(status_code)
        self.retryable = retryable
        self.safe_message = safe_message


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
        "zai": bool(os.environ.get("ZAI_API_KEY")),
        "openrouter": bool(os.environ.get("OPENROUTER_API_KEY")),
        "gemini": bool(os.environ.get("GEMINI_API_KEY")),
        "groq": bool(os.environ.get("GROQ_API_KEY")),
    }


def demo_mode() -> bool:
    return os.environ.get("DEMO_MODE", "false").lower() in {"1", "true", "yes", "on"}


def analyze_images(images: list[UploadedImage], context: dict[str, Any] | None = None) -> dict[str, Any]:
    if not images:
        raise ProviderError("At least one image is required.", 400)
    if len(images) > MAX_PROVIDER_IMAGES:
        raise ProviderError("Upload one to five images.", 400, category="invalid_request")
    context = context or {}
    context.setdefault("deadline", time.monotonic() + _env_float("ANALYZE_DEADLINE_SECONDS", DEFAULT_ANALYZE_DEADLINE_SECONDS))
    compact_images = images[:MAX_PROVIDER_IMAGES]
    failures: list[dict[str, Any]] = []
    providers = _provider_plan()
    if not providers:
        if demo_mode():
            return _demo_listing()
        raise ProviderError(
            "Hosted analysis is not configured. Set PRIMARY_VISION_PROVIDER=zai with ZAI_API_KEY in Heroku Config Vars, or enable DEMO_MODE=true for development only.",
            503,
            category="configuration",
            retryable=False,
        )

    for name, caller in providers:
        if _remaining_seconds(context) < 5:
            failures.append(_failure(name, _model_for_provider(name), 504, "timeout", False))
            continue
        try:
            raw = caller(compact_images, context)
            try:
                parsed = parse_model_json(raw)
            except ValueError as exc:
                raise ProviderError(
                    "Provider returned malformed JSON.",
                    502,
                    provider=name,
                    model=_model_for_provider(name),
                    category="malformed_json",
                    retryable=False,
                ) from exc
            result = enrich_with_ebay_active_pricing(normalize_listing(parsed), timeout=min(5.0, _request_timeout(context)))
            result["provider"] = name
            result["demo"] = False
            return result
        except ProviderError as exc:
            failures.append(_failure(
                name,
                exc.model or _model_for_provider(name),
                exc.status_code,
                exc.category,
                exc.retryable,
                exc.safe_message,
            ))
            _log_provider(name, exc.status_code, exc.category, exc.retryable)
        except Exception as exc:
            failures.append(_failure(name, _model_for_provider(name), 502, "provider_error", False))
            _log_provider(name, 502, "provider_error", False)

    if demo_mode():
        demo = _demo_listing()
        demo["providerFailures"] = failures
        return demo
    raise ProviderError("Configured vision provider failed. No demo listing was generated.", 502, failures)


def _provider_plan():
    selected = os.environ.get("PRIMARY_VISION_PROVIDER", "").strip().lower()
    callers = {
        "zai": ("ZAI_API_KEY", _zai),
        "openrouter": ("OPENROUTER_API_KEY", _openrouter),
        "gemini": ("GEMINI_API_KEY", _gemini),
        "groq": ("GROQ_API_KEY", _groq),
    }
    if not selected:
        return []
    if selected not in callers:
        raise ProviderError(
            "Unsupported PRIMARY_VISION_PROVIDER. Set PRIMARY_VISION_PROVIDER=zai.",
            503,
            category="configuration",
        )
    api_key, caller = callers[selected]
    return [(selected, caller)] if os.environ.get(api_key) else []


def _zai(images: list[UploadedImage], context: dict[str, Any]) -> str:
    model = _zai_model()
    if not _looks_like_vision_model(model):
        raise ProviderError(
            "Configured Z.AI model does not appear to support vision.",
            400,
            provider="zai",
            model=model,
            category="non_vision_model",
            retryable=False,
        )
    try:
        return _zai_once(images, context, model, ZAI_IMAGE_MAX_EDGE, ZAI_IMAGE_QUALITY)
    except ProviderError as exc:
        if exc.category != "timeout" or _remaining_seconds(context) < 8:
            raise
        print(f"[provider] zai retrying timeout with smaller images model={model}")
        return _zai_once(images, context, model, ZAI_IMAGE_RETRY_MAX_EDGE, ZAI_IMAGE_RETRY_QUALITY)


def _zai_once(images: list[UploadedImage], context: dict[str, Any], model: str, max_edge: int, quality: int) -> str:
    content = [{"type": "image_url", "image_url": {"url": _compressed_data_url(image, max_edge, quality)}} for image in images]
    content.append({"type": "text", "text": _zai_prompt(context)})
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1,
        "max_tokens": 1000,
        "stream": False,
    }
    _reject_oversized_zai_payload(content, model)
    return _post_openai_compatible("zai", model, _zai_chat_url(), os.environ["ZAI_API_KEY"], payload, context)


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


def _post_openai_compatible(
    provider: str,
    model: str,
    url: str,
    api_key: str,
    payload: dict[str, Any],
    context: dict[str, Any],
) -> str:
    attempts = 0
    while True:
        attempts += 1
        try:
            response = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept-Language": "en-US,en",
                },
                json=payload,
                timeout=_request_timeout(context),
            )
        except requests.Timeout as exc:
            raise ProviderError(
                "Provider request timed out.",
                504,
                provider=provider,
                model=model,
                category="timeout",
                retryable=True,
            ) from exc
        except requests.RequestException as exc:
            raise ProviderError(
                "Provider request failed.",
                502,
                provider=provider,
                model=model,
                category="transport",
                retryable=True,
            ) from exc

        retryable = response.status_code in TRANSIENT_STATUS_CODES
        if response.status_code >= 400:
            category = _category_for_status(response.status_code)
            if attempts == 1 and retryable and _remaining_seconds(context) >= 5:
                _provider_backoff(attempts)
                continue
            upstream_error = _upstream_error_info(response)
            raise ProviderError(
                "Provider returned an error.",
                response.status_code,
                provider=provider,
                model=model,
                category=category,
                retryable=retryable,
                safe_message=_safe_provider_message(provider, response.status_code, category, upstream_error),
            )
        return _chat_response(response, provider=provider, model=model)


def _chat_response(response: requests.Response, provider: str | None = None, model: str | None = None) -> str:
    if response.status_code >= 400:
        raise ProviderError(
            "Provider returned an error.",
            response.status_code,
            provider=provider,
            model=model,
            category=_category_for_status(response.status_code),
            retryable=response.status_code in TRANSIENT_STATUS_CODES,
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderError(
            "Provider returned malformed JSON.",
            502,
            provider=provider,
            model=model,
            category="malformed_json",
        ) from exc
    message = body.get("choices", [{}])[0].get("message", {})
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not content:
        raise ProviderError(
            "Provider returned no analysis.",
            502,
            provider=provider,
            model=model,
            category="non_vision_model",
            retryable=False,
        )
    return content


def _prompt(context: dict[str, Any]) -> str:
    defaults = context.get("seller_defaults") or {}
    location = defaults.get("location") or "Kettering, Ohio"
    return f"{PROMPT}\nSeller location for description: {location}. Keep desc under 700 characters."


def _zai_prompt(context: dict[str, Any]) -> str:
    defaults = context.get("seller_defaults") or {}
    location = defaults.get("location") or "Kettering, Ohio"
    return f"{ZAI_PROMPT}\nUse only supported eBay category IDs. Seller location: {location}. Keep desc under 700 characters."


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


def _failure(
    provider: str,
    model: str,
    status_code: int,
    category: str,
    retryable: bool,
    safe_message: str | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "status": status_code,
        "httpStatus": status_code,
        "category": category,
        "retryable": retryable,
        "message": safe_message or _safe_provider_message(provider, status_code, category),
    }


def _category_for_status(status_code: int) -> str:
    if status_code == 401:
        return "authentication"
    if status_code == 403:
        return "permission"
    if status_code == 404:
        return "not_found"
    if status_code == 413:
        return "payload_too_large"
    if status_code == 429:
        return "rate_limit"
    if status_code == 504:
        return "timeout"
    if status_code >= 500:
        return "server_error"
    if status_code >= 400:
        return "request_error"
    return "provider_error"


def _log_provider(provider: str, status_code: int, category: str, retryable: bool) -> None:
    print(f"[provider] {provider} status={status_code} category={category} retryable={retryable}")


def _remaining_seconds(context: dict[str, Any]) -> float:
    return max(0.0, float(context.get("deadline", time.monotonic())) - time.monotonic())


def _request_timeout(context: dict[str, Any]) -> float:
    remaining = _remaining_seconds(context)
    if remaining < 5:
        raise ProviderError("Provider timeout budget exhausted before request.", 504)
    configured = _env_float("PROVIDER_REQUEST_TIMEOUT_SECONDS", DEFAULT_PROVIDER_TIMEOUT_SECONDS)
    return min(configured, DEFAULT_PROVIDER_TIMEOUT_SECONDS, max(3.0, remaining - 4.0))


def _env_float(name: str, default: float) -> float:
    try:
        return max(1.0, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _zai_base_url() -> str:
    return (os.environ.get("ZAI_BASE_URL") or ZAI_DEFAULT_BASE_URL).rstrip("/") + "/"


def _zai_chat_url() -> str:
    base_url = _zai_base_url()
    if base_url.lower().endswith("chat/completions/"):
        base_url = base_url[: -len("chat/completions/")]
    elif base_url.lower().endswith("chat/completions"):
        base_url = base_url[: -len("chat/completions")]
    return urljoin(base_url, "chat/completions")


def _zai_model() -> str:
    return os.environ.get("ZAI_MODEL") or ZAI_DEFAULT_MODEL


def _model_for_provider(provider: str) -> str:
    return {
        "zai": _zai_model(),
        "openrouter": os.environ.get("OPENROUTER_MODEL", "openrouter/free"),
        "gemini": os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
        "groq": os.environ.get("GROQ_MODEL") or "meta-llama/llama-4-scout-17b-16e-instruct",
    }.get(provider, "")


def _looks_like_vision_model(model: str) -> bool:
    lowered = model.lower()
    return "vision" in lowered or "vl" in lowered or "4.6v" in lowered or "5v" in lowered


def _compressed_data_url(image: UploadedImage, max_edge: int = ZAI_IMAGE_MAX_EDGE, quality: int = ZAI_IMAGE_QUALITY) -> str:
    try:
        from io import BytesIO

        with Image.open(BytesIO(image.data)) as source:
            source.thumbnail((max_edge, max_edge))
            if source.mode not in {"RGB", "L"}:
                source = source.convert("RGB")
            out = BytesIO()
            source.save(out, format="JPEG", quality=quality, optimize=True)
            encoded = base64.b64encode(out.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{encoded}"
    except (UnidentifiedImageError, OSError, ValueError):
        return image.data_url


def _reject_oversized_zai_payload(content: list[dict[str, Any]], model: str) -> None:
    size = len(json.dumps({"model": model, "messages": [{"role": "user", "content": content}]}, separators=(",", ":")).encode("utf-8"))
    if size > MAX_ZAI_REQUEST_BYTES:
        raise ProviderError(
            "Compressed image payload is too large.",
            413,
            provider="zai",
            model=model,
            category="payload_too_large",
            retryable=False,
        )


def _provider_backoff(attempt: int) -> None:
    time.sleep(min(0.2, 0.1 * (2 ** max(0, attempt - 1))))


def _upstream_error_info(response: requests.Response) -> dict[str, str]:
    try:
        body = response.json()
    except ValueError:
        return {}
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return {}
    info: dict[str, str] = {}
    code = error.get("code")
    if code is not None:
        info["code"] = str(code)[:32]
    message = error.get("message")
    if message is not None:
        info["message"] = str(message)[:160]
    return info


def _safe_provider_message(
    provider: str,
    status_code: int,
    category: str,
    upstream_error: dict[str, str] | None = None,
) -> str:
    upstream_error = upstream_error or {}
    suffix = f" (code {upstream_error['code']})." if upstream_error.get("code") else "."
    if provider == "zai" and status_code == 401:
        return f"Z.AI authentication failed{suffix}"
    if provider == "zai" and status_code == 403:
        return f"Z.AI denied access to this model or account{suffix}"
    if provider == "zai" and status_code == 404:
        return f"Z.AI endpoint or model was not found{suffix}"
    if provider == "zai" and status_code == 400:
        return f"Z.AI rejected the request parameters{suffix}"
    if category == "payload_too_large":
        return "Image payload is too large after compression."
    if category == "rate_limit":
        return f"Provider rate limit reached{suffix}"
    if category == "timeout":
        return "Provider request timed out."
    if category == "malformed_json":
        return "Provider returned invalid JSON."
    if category == "non_vision_model":
        return "Configured model did not return a vision analysis."
    return f"Provider request failed{suffix}"
