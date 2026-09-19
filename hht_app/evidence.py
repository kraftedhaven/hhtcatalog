"""Normalized attribute evidence used for human review and safe recommendations."""
from __future__ import annotations
from typing import Any

CONFIDENCE_LEVELS = {"low", "medium", "high"}
ATTRIBUTE_KEYS = ("brand", "model", "material", "madeIn", "style", "theme", "vin", "size", "color", "type")
UNCONFIRMED_VALUES = {"", "not visible", "unknown", "n/a", "n/a - bag", "n/a - footwear", "none"}


def has_confirmed_value(value: Any) -> bool:
    return str(value or "").strip().casefold() not in UNCONFIRMED_VALUES

def normalize_evidence(
    raw: Any, *, source: str = "unknown", default_evidence: str = ""
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return result
    for key in ATTRIBUTE_KEYS:
        value = raw.get(key)
        if isinstance(value, dict):
            value = value.get("value")
        if not has_confirmed_value(value):
            continue
        confidence = str((raw.get(key) or {}).get("confidence", "medium")) if isinstance(raw.get(key), dict) else "medium"
        if confidence not in CONFIDENCE_LEVELS:
            confidence = "medium"
        evidence = (raw.get(key) or {}).get("evidence", default_evidence) if isinstance(raw.get(key), dict) else default_evidence
        result[key] = {"value": str(value).strip(), "confidence": confidence, "source": source, "evidence": str(evidence)[:300]}
    return result

def evidence_for_listing(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    stored = item.get("attributeEvidence")
    if isinstance(stored, dict) and stored:
        return stored
    result = {}
    for key in ATTRIBUTE_KEYS:
        value = item.get(key) or item.get({"material": "mat", "model": "model"}.get(key, key))
        if has_confirmed_value(value):
            result[key] = {"value": str(value), "confidence": "medium", "source": item.get("evidenceSource", "import"), "evidence": "Imported listing field; seller confirmation recommended."}
    return result

def evidence_summary(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"field": key, **value} for key, value in evidence_for_listing(item).items()]
