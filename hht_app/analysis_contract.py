"""Provider-neutral contract for persisted, review-only image analysis."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol
import uuid


SUPPORTED_ANALYSIS_PROVIDERS = frozenset({"groq", "breakground", "nvidia_worker"})
BREAKGROUND_REQUIRED_CONTRACT = (
    "api_url_or_sdk",
    "authentication_method",
    "model_name",
    "image_request_format",
    "maximum_images_per_request",
    "token_accounting",
    "async_job_behavior",
    "rate_limits",
    "response_schema",
    "data_retention_and_privacy_terms",
    "credit_type",
)


class AnalysisProviderAdapter(Protocol):
    """Narrow interface for a provider that returns HHT's canonical result."""

    provider: str

    def analyze_listing_images(
        self,
        images: list[bytes],
        *,
        job_id: str = "",
        seller_hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


def breakground_readiness() -> dict[str, Any]:
    """Describe the intentionally disabled BreakGround provider slot."""
    return {
        "provider": "breakground",
        "enabled": False,
        "mode": "review_only_contract_pending",
        "requiredContract": list(BREAKGROUND_REQUIRED_CONTRACT),
        "eBayMutationAllowed": False,
    }


def canonical_analysis_run(
    provider: str,
    result: dict[str, Any],
    *,
    model: str = "",
    job_id: str = "",
    input_photo_count: int = 0,
    token_usage: dict[str, Any] | None = None,
    prompt_version: str = "",
    schema_version: str = "",
    started_at: str = "",
    completed_at: str = "",
    status: str = "completed",
    error: str = "",
) -> dict[str, Any]:
    """Create the one durable result shape every analysis provider must return."""
    if provider not in SUPPORTED_ANALYSIS_PROVIDERS:
        raise ValueError(f"Unsupported analysis provider: {provider}")
    if not isinstance(result, dict):
        raise TypeError("Analysis result must be a dictionary.")
    try:
        photo_count = max(0, int(input_photo_count))
    except (TypeError, ValueError):
        raise ValueError("input_photo_count must be an integer.") from None
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "provider": provider,
        "model": str(model or ""),
        "jobId": str(job_id or ""),
        "inputPhotoCount": photo_count,
        "tokenUsage": token_usage if isinstance(token_usage, dict) else {},
        "promptVersion": str(prompt_version or ""),
        "schemaVersion": str(schema_version or ""),
        "startedAt": str(started_at or now),
        "completedAt": str(completed_at or now),
        "status": str(status or "completed"),
        "error": str(error or ""),
        "result": result,
        "reviewOnly": True,
        "reviewStatus": "pending",
    }
