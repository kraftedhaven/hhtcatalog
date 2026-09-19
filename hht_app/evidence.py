"""Normalized attribute evidence used for human review and safe recommendations."""
from __future__ import annotations
from typing import Any

CONFIDENCE_LEVELS = {"low", "medium", "high"}
ATTRIBUTE_KEYS = ("brand", "model", "material", "madeIn", "style", "theme", "vin", "size", "color", "type")

def normalize_evidence(raw: Any, *, source: str = "unknown") -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return result
    for key in ATTRIBUTE_KEYS:
        value = raw.get(key)
        if isinstance(value, dict):
            value = value.get("value")
        if value is None or not str(value).strip() or str(value).strip().lower() in {"not visible", "unknown", "n/a"}:
            continue
        confidence = str((raw.get(key) or {}).get("confidence", "medium")) if isinstance(raw.get(key), dict) else "medium"
        if confidence not in CONFIDENCE_LEVELS:
            confidence = "medium"
        evidence = (raw.get(key) or {}).get("evidence", "") if isinstance(raw.get(key), dict) else ""
        result[key] = {"value": str(value).strip(), "confidence": confidence, "source": source, "evidence": str(evidence)[:300]}
    return result

def evidence_for_listing(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    stored = item.get("attributeEvidence")
    if isinstance(stored, dict) and stored:
        return stored
    result = {}
    for key in ATTRIBUTE_KEYS:
        value = item.get(key) or item.get({"material": "mat", "model": "model"}.get(key, key))
        if value and str(value).lower() not in {"not visible", "unknown", "n/a"}:
            result[key] = {"value": str(value), "confidence": "medium", "source": item.get("evidenceSource", "import"), "evidence": "Imported listing field; seller confirmation recommended."}
    return result

def evidence_summary(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"field": key, **value} for key, value in evidence_for_listing(item).items()]
