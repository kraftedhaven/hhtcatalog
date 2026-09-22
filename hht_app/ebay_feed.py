"""Seller Hub feed upload helpers for draft CSV queue submissions."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

import requests

from .ebay_auth import EbayAuthError, seller_access_token
from .ebay_pricing import DEFAULT_MARKETPLACE_ID
from .schema import deduplicate_draft_items, export_ebay_draft_csv


DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_SELLER_HUB_DRAFT_FEED_TYPE = "FX_DRAFT"
SELLER_HUB_SCHEMA_VERSION = "1.0"
MIN_DRAFT_UPLOAD_ITEMS = 5


class EbayFeedError(RuntimeError):
    def __init__(self, status_code: int, category: str, message: str, code: str = "", operation: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.category = category
        self.safe_message = message
        self.code = code
        self.operation = operation

    def to_public(self) -> dict[str, Any]:
        body = {
            "provider": "ebay_feed",
            "status": self.status_code,
            "category": self.category,
            "retryable": self.status_code in {429, 500, 502, 503, 504},
            "message": self.safe_message,
        }
        if self.code:
            body["code"] = self.code
        if self.operation:
            body["operation"] = self.operation
        return body


def upload_seller_hub_draft_csv(items: list[dict[str, Any]], timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    if not isinstance(items, list) or not items:
        raise EbayFeedError(400, "invalid_request", "Queue must include at least one reviewed item.", operation="create_task")
    unique_items = deduplicate_draft_items(items)
    if len(unique_items) < MIN_DRAFT_UPLOAD_ITEMS:
        duplicate_note = " after removing duplicates" if len(unique_items) != len(items) else ""
        raise EbayFeedError(
            400,
            "invalid_request",
            f"Seller Hub draft upload requires at least {MIN_DRAFT_UPLOAD_ITEMS} unique reviewed items; received {len(unique_items)}{duplicate_note}.",
            operation="create_task",
        )
    if _environment() == "sandbox":
        raise EbayFeedError(503, "configuration", f"Seller Hub {_draft_feed_type()} feed uploads are production-only; eBay does not support this Seller Hub upload flow in sandbox.", operation="create_task")
    csv_text = export_ebay_draft_csv(unique_items)
    token = _seller_token(timeout)
    task_id = _create_task(token, timeout)
    filename = f"hht_seller_hub_drafts_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"
    upload = _upload_file(token, task_id, filename, csv_text.encode("utf-8"), timeout)
    task = get_feed_task(task_id, token=token, timeout=timeout)
    return {
        "status": "submitted",
        "provider": "ebay_feed",
        "feedType": _draft_feed_type(),
        "schemaVersion": SELLER_HUB_SCHEMA_VERSION,
        "taskId": task_id,
        "marketplaceId": _marketplace_id(),
        "itemCount": len(unique_items),
        "duplicateCount": len(items) - len(unique_items),
        "fileName": filename,
        "uploadStatus": upload.get("status", "accepted"),
        "task": task,
        "nextStep": "Open Seller Hub Reports or poll this task for processing results. This Seller Hub draft feed is intended for draft CSV files, not direct live publishing.",
    }


def get_feed_task(task_id: str, *, token: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    task_id = _task_id(task_id)
    token = token or _seller_token(timeout)
    return _request(
        "GET",
        f"{_api_base_url()}/sell/feed/v1/task/{task_id}",
        token,
        None,
        timeout,
        expected_statuses={200},
        operation="get_task",
    )


def get_feed_result_file(task_id: str, *, token: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> tuple[bytes, str, str]:
    """Download eBay's compressed result/error file for a completed feed task."""
    task_id = _task_id(task_id)
    token = token or _seller_token(timeout)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/octet-stream, application/gzip, text/csv, application/xml, application/json",
        "X-EBAY-C-MARKETPLACE-ID": _marketplace_id(),
    }
    url = f"{_api_base_url()}/sell/feed/v1/task/{task_id}/download_result_file"
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.Timeout as exc:
        raise EbayFeedError(504, "timeout", _safe_error_message(504, "", "download_result_file"), operation="download_result_file") from exc
    except requests.RequestException as exc:
        raise EbayFeedError(502, "transport", _safe_error_message(502, "", "download_result_file"), operation="download_result_file") from exc
    if response.status_code != 200:
        code = _ebay_error_code(response)
        raise EbayFeedError(response.status_code, _category_for_status(response.status_code), _safe_error_message(response.status_code, code, "download_result_file"), code, operation="download_result_file")
    content_type = response.headers.get("Content-Type", "application/octet-stream")
    disposition = response.headers.get("Content-Disposition", "")
    filename = "ebay_feed_result"
    match = re.search(r"filename=\"?([^\";]+)", disposition)
    if match:
        filename = match.group(1).strip()
    return response.content, content_type, filename


def _create_task(token: str, timeout: float) -> str:
    response = _request(
        "POST",
        f"{_api_base_url()}/sell/feed/v1/task",
        token,
        {"feedType": _draft_feed_type(), "schemaVersion": SELLER_HUB_SCHEMA_VERSION},
        timeout,
        expected_statuses={200, 201, 202},
        operation="create_task",
    )
    task_id = str(response.get("taskId") or response.get("id") or "")
    if not task_id:
        location = str(response.get("location") or response.get("Location") or "")
        match = re.search(r"/task/([^/?#]+)", location)
        task_id = match.group(1) if match else ""
    if not task_id:
        raise EbayFeedError(502, "malformed_json", "eBay Feed API did not return a task ID.", operation="create_task")
    return task_id


def _upload_file(token: str, task_id: str, filename: str, payload: bytes, timeout: float) -> dict[str, Any]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return _request(
        "POST",
        f"{_api_base_url()}/sell/feed/v1/task/{task_id}/upload_file",
        token,
        None,
        timeout,
        expected_statuses={200, 201, 202, 204},
        operation="upload_file",
        data={"fileName": filename, "name": "file", "type": "form-data", "creationDate": now, "modificationDate": now},
        files={"file": (filename, payload, "text/csv")},
    ) or {"status": "accepted"}


def _seller_token(timeout: float) -> str:
    try:
        return seller_access_token(timeout=timeout)
    except EbayAuthError as exc:
        raise EbayFeedError(exc.status_code, exc.category, exc.safe_message, exc.code, operation="oauth") from exc


def _request(
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None,
    timeout: float,
    expected_statuses: set[int],
    operation: str,
    *,
    data: dict[str, str] | None = None,
    files: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": _marketplace_id(),
    }
    kwargs: dict[str, Any] = {"headers": headers, "timeout": timeout}
    if payload is not None:
        kwargs["json"] = payload
        headers["Content-Type"] = "application/json"
    if data is not None:
        kwargs["data"] = data
    if files is not None:
        kwargs["files"] = files
    try:
        response = requests.request(method, url, **kwargs)
    except requests.Timeout as exc:
        raise EbayFeedError(504, "timeout", _safe_error_message(504, "", operation), operation=operation) from exc
    except requests.RequestException as exc:
        raise EbayFeedError(502, "transport", _safe_error_message(502, "", operation), operation=operation) from exc
    if response.status_code not in expected_statuses:
        code = _ebay_error_code(response)
        raise EbayFeedError(response.status_code, _category_for_status(response.status_code), _safe_error_message(response.status_code, code, operation), code, operation)
    if response.status_code == 204:
        return {}
    location = response.headers.get("Location") or response.headers.get("location") if hasattr(response, "headers") else ""
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if location:
        body.setdefault("location", location)
    return body


def _task_id(value: Any) -> str:
    task_id = str(value or "").strip()
    if not task_id or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", task_id):
        raise EbayFeedError(400, "invalid_request", "Valid eBay feed task ID is required.", operation="get_task")
    return task_id


def _environment() -> str:
    return os.environ.get("EBAY_ENVIRONMENT", "production").strip().lower()


def _marketplace_id() -> str:
    return os.environ.get("EBAY_MARKETPLACE_ID") or DEFAULT_MARKETPLACE_ID


def _draft_feed_type() -> str:
    return os.environ.get("EBAY_SELLER_HUB_DRAFT_FEED_TYPE", DEFAULT_SELLER_HUB_DRAFT_FEED_TYPE).strip() or DEFAULT_SELLER_HUB_DRAFT_FEED_TYPE


def _api_base_url() -> str:
    return "https://api.sandbox.ebay.com" if _environment() == "sandbox" else "https://api.ebay.com"


def _category_for_status(status_code: int) -> str:
    if status_code in {401, 403}:
        return "authentication"
    if status_code == 409:
        return "conflict"
    if status_code == 429:
        return "rate_limit"
    if status_code >= 500:
        return "server_error"
    return "request_error"


def _ebay_error_code(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("errorId") or errors[0].get("errorId") or "")[:32]
    return str(body.get("error") or body.get("code") or "")[:32]


def _safe_error_message(status_code: int, code: str, operation: str = "") -> str:
    suffix = f" ({code})." if code else "."
    step = f" during {operation}" if operation else ""
    if status_code in {401, 403}:
        return f"eBay feed authentication failed{step}{suffix}"
    if status_code == 409:
        return f"eBay feed task conflicts with another task{step}{suffix}"
    if status_code == 429:
        return f"eBay feed rate limit reached{step}{suffix}"
    if status_code >= 500:
        return f"eBay feed service failed{step}{suffix}"
    return f"eBay feed request was rejected{step}{suffix}"
