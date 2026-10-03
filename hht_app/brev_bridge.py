"""Capability tokens and lifecycle helpers for the isolated Brev worker.

The Brev VM is deliberately not a trusted application node. It gets one
short-lived capability for one queued vision job and can only read that job's
photos and submit a read-only analysis result.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shlex
import subprocess
import time
from typing import Any


class BrevBridgeError(RuntimeError):
    pass


def enabled() -> bool:
    return os.environ.get("NVIDIA_BREV_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _secret() -> bytes:
    value = os.environ.get("NVIDIA_BREV_WORKER_SECRET", "").strip()
    if len(value) < 32:
        raise BrevBridgeError("NVIDIA_BREV_WORKER_SECRET must be at least 32 characters.")
    return value.encode("utf-8")


def _ttl_seconds() -> int:
    try:
        return min(4 * 60 * 60, max(10 * 60, int(os.environ.get("NVIDIA_BREV_TOKEN_TTL_SECONDS", "7200"))))
    except ValueError:
        return 7200


def issue_job_token(job_id: str) -> str:
    payload = {"jobId": str(job_id), "exp": int(time.time()) + _ttl_seconds(), "scope": "brev_vision"}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).rstrip(b"=")
    signature = hmac.new(_secret(), encoded, hashlib.sha256).digest()
    return encoded.decode("ascii") + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


def verify_job_token(token: str, job_id: str) -> bool:
    try:
        encoded_text, signature_text = str(token).split(".", 1)
        encoded = encoded_text.encode("ascii")
        supplied = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
        expected = hmac.new(_secret(), encoded, hashlib.sha256).digest()
        payload = json.loads(base64.urlsafe_b64decode(encoded_text + "=" * (-len(encoded_text) % 4)))
    except (ValueError, UnicodeError, json.JSONDecodeError, TypeError):
        return False
    return bool(hmac.compare_digest(supplied, expected) and payload.get("scope") == "brev_vision" and hmac.compare_digest(str(payload.get("jobId", "")), str(job_id)) and int(payload.get("exp", 0)) >= int(time.time()))


def dispatch(job_id: str, token: str) -> dict[str, Any]:
    """Start the configured instance and launch its one-job worker process."""
    instance = os.environ.get("NVIDIA_BREV_INSTANCE_NAME", "").strip()
    base_url = os.environ.get("HHT_BREV_CALLBACK_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get("NVIDIA_BREV_API_KEY", "").strip()
    cli = os.environ.get("NVIDIA_BREV_CLI_PATH", "brev").strip() or "brev"
    command = os.environ.get("NVIDIA_BREV_WORKER_COMMAND", "python /home/ubuntu/workspace/hhtcatalog/brev_worker.py").strip()
    if not instance or not base_url or not api_key:
        raise BrevBridgeError("Brev dispatch requires NVIDIA_BREV_INSTANCE_NAME, HHT_BREV_CALLBACK_BASE_URL, and NVIDIA_BREV_API_KEY.")
    environment = os.environ.copy()
    environment["BREV_API_KEY"] = api_key
    try:
        subprocess.run([cli, "start", instance], env=environment, check=True, capture_output=True, text=True, timeout=180)
        remote = "nohup " + command + " --base-url " + shlex.quote(base_url) + " --job-id " + shlex.quote(job_id) + " --token " + shlex.quote(token) + " >/tmp/hht-brev-worker.log 2>&1 &"
        subprocess.run([cli, "exec", instance, remote], env=environment, check=True, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BrevBridgeError("Brev worker could not be started.") from exc
    return {"instance": instance, "provider": "brev", "readOnly": True}
