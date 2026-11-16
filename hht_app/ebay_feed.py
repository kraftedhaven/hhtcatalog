"""Seller Hub feed upload helpers for draft CSV queue submissions."""
from __future__ import annotations

import csv
import gzip
import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from typing import Any

import requests

from .ebay_auth import EbayAuthError, seller_access_token
from .ebay_pricing import DEFAULT_MARKETPLACE_ID
from .photo_url_validator import (
    PHOTO_URL_TIMEOUT_SECONDS,
    PHOTO_VALIDATION_MAX_BYTES,
    PRIVATE_IP_NETWORKS,
    PhotoCheckContext,
    is_image_magic_header,
    is_private_hostname,
    validate_single_image_url,
)
from .schema import deduplicate_draft_items, draft_photo_urls, export_ebay_draft_csv


DEFAULT_TIMEOUT_SECONDS = 15.0
# Seller Hub creates drafts through the FX_LISTING feed plus the CSV's
# Draft action. FX_DRAFT is not a supported Sell Feed API task type.
DEFAULT_SELLER_HUB_DRAFT_FEED_TYPE = "FX_LISTING"
SELLER_HUB_SCHEMA_VERSION = "1.0"
DEFAULT_MIN_DRAFT_UPLOAD_ITEMS = 3
_PRIVATE_NETWORKS = PRIVATE_IP_NETWORKS


def _min_draft_upload_items() -> int:
    """Return the configured minimum, bounded to a safe pilot range."""
    try:
        return min(50, max(3, int(os.environ.get("EBAY_DRAFT_MIN_ITEMS", str(DEFAULT_MIN_DRAFT_UPLOAD_ITEMS)))))
    except (TypeError, ValueError):
        return DEFAULT_MIN_DRAFT_UPLOAD_ITEMS


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


def _parse_draft_csv_rows(text: str) -> tuple[list[str], list[list[str]]]:
    header_row: list[str] = []
    data_rows: list[list[str]] = []
    for row in csv.reader(io.StringIO(text)):
        if not row or row[0].strip().startswith("#INFO"):
            continue
        if not header_row:
            header_row = [c.strip() for c in row]
        else:
            data_rows.append(row)
    return header_row, data_rows


def _assert_draft_actions_only(header_row: list[str], data_rows: list[list[str]]) -> None:
    action_idx = next((idx for idx, col in enumerate(header_row) if col.lower().startswith("action")), -1)
    if action_idx == -1:
        raise EbayFeedError(400, "invalid_csv", "Draft CSV missing Action header.", operation="validate_upload_bytes")
    if not data_rows:
        raise EbayFeedError(400, "invalid_csv", "Draft CSV contains no item rows.", operation="validate_upload_bytes")
    for r_idx, d_row in enumerate(data_rows, start=1):
        if action_idx >= len(d_row):
            raise EbayFeedError(400, "invalid_csv", f"Row {r_idx} missing Action value.", operation="validate_upload_bytes")
        val = d_row[action_idx].strip()
        if val.lower() != "draft":
            raise EbayFeedError(
                400,
                "invalid_csv",
                f"Row {r_idx} contains unauthorized action '{val}'. Only 'Draft' is permitted.",
                operation="validate_upload_bytes",
            )


def _validate_upload_bytes(data: bytes) -> None:
    """Ensure the uploaded bytes are exactly valid UTF-8 eBay draft CSV with no live actions."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EbayFeedError(400, "invalid_csv", "Draft CSV upload payload must be valid UTF-8.", operation="validate_upload_bytes") from exc
    if not text.strip():
        raise EbayFeedError(400, "invalid_csv", "Draft CSV upload payload is empty.", operation="validate_upload_bytes")
    header_row, data_rows = _parse_draft_csv_rows(text)
    if not header_row:
        raise EbayFeedError(400, "invalid_csv", "Draft CSV missing column headers.", operation="validate_upload_bytes")
    _assert_draft_actions_only(header_row, data_rows)


def _prepare_unique_draft_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(items, list) or not items:
        raise EbayFeedError(400, "invalid_request", "Queue must include at least one reviewed item.", operation="create_task")
    unique_items = deduplicate_draft_items(items)
    minimum_items = _min_draft_upload_items()
    if len(unique_items) < minimum_items:
        duplicate_note = " after removing duplicates" if len(unique_items) != len(items) else ""
        raise EbayFeedError(
            400,
            "invalid_request",
            f"Seller Hub draft upload requires at least {minimum_items} unique reviewed items; received {len(unique_items)}{duplicate_note}.",
            operation="create_task",
        )
    if _environment() == "sandbox":
        raise EbayFeedError(503, "configuration", f"Seller Hub {_draft_feed_type()} feed uploads are production-only; eBay does not support this Seller Hub upload flow in sandbox.", operation="create_task")
    return unique_items


def _render_validated_draft_csv_bytes(
    unique_items: list[dict[str, Any]],
    template_text: str | None,
    category_metadata_map: dict[str, Any] | None,
) -> bytes:
    from .seller_hub_draft_template import export_seller_hub_draft_csv, DraftExportValidationError
    try:
        csv_text = export_seller_hub_draft_csv(
            unique_items, template_text=template_text, category_metadata_map=category_metadata_map
        )
    except DraftExportValidationError as exc:
        raise EbayFeedError(400, "validation_failed", str(exc), operation="generate_draft_csv") from exc
    except ValueError as exc:
        raise EbayFeedError(400, "invalid_request", str(exc), operation="generate_draft_csv") from exc
    payload_bytes = csv_text.encode("utf-8")
    _validate_upload_bytes(payload_bytes)
    return payload_bytes


def _notify_stage_callback(callback: Any, task_id: str) -> None:
    if callable(callback):
        callback(task_id)


def _fetch_task_summary_safe(task_id: str, token: str, timeout: float) -> dict[str, Any]:
    try:
        return get_feed_task(task_id, token=token, timeout=timeout)
    except Exception as exc:
        # Upload succeeded and was confirmed. An error querying task status (e.g. 404,
        # 500, timeout) must not mark the upload as failed or lose the task ID.
        return {"taskId": task_id, "status": "SUBMITTED", "uploadConfirmed": True, "taskPollError": str(exc)}


def upload_seller_hub_draft_csv(
    items: list[dict[str, Any]],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    template_text: str | None = None,
    category_metadata_map: dict[str, Any] | None = None,
    *,
    on_task_created: Any = None,
    on_upload_sent: Any = None,
    on_upload_confirmed: Any = None,
) -> dict[str, Any]:
    unique_items = _prepare_unique_draft_items(items)
    validate_draft_photo_urls(unique_items)
    payload_bytes = _render_validated_draft_csv_bytes(unique_items, template_text, category_metadata_map)

    token = _seller_token(timeout)
    task_id = _create_task(token, timeout)
    _notify_stage_callback(on_task_created, task_id)

    filename = f"hht_seller_hub_drafts_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"
    _notify_stage_callback(on_upload_sent, task_id)

    upload = _upload_file(token, task_id, filename, payload_bytes, timeout)
    _notify_stage_callback(on_upload_confirmed, task_id)
    task = _fetch_task_summary_safe(task_id, token, timeout)

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


def validate_draft_photo_urls(items: list[dict[str, Any]], timeout: float = PHOTO_URL_TIMEOUT_SECONDS) -> None:
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        for url in draft_photo_urls(item):
            if url in seen:
                continue
            seen.add(url)
            _validate_image_url(url, index, timeout)


def _is_private_address(hostname: str) -> bool:
    """Return True if any resolved address for hostname is private/loopback."""
    return is_private_hostname(hostname, _PRIVATE_NETWORKS)


def _validate_image_url(url: str, item_index: int, timeout: float) -> None:
    validate_single_image_url(
        url,
        PhotoCheckContext(
            item_index=item_index,
            timeout=timeout,
            get_fn=requests.get,
            is_private_fn=_is_private_address,
            max_bytes=PHOTO_VALIDATION_MAX_BYTES,
            networks=_PRIVATE_NETWORKS,
        ),
    )


def _is_image_header(data: bytes) -> bool:
    """Check magic bytes for common image formats."""
    return is_image_magic_header(data)


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


def _parse_xml_feed_errors(text: str) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(text.strip())
        error_elems = [e for e in root.iter() if e.tag.split("}")[-1].lower() in {"error", "errordetail"}]
        if not error_elems:
            error_elems = [e for e in root.iter() if e.tag.split("}")[-1].lower() in {"errorresponse", "response"}]

        for elem in error_elems:
            code = ""
            msg = ""
            severity = ""
            line_num = ""
            custom_label = ""
            item_id = ""
            for child in elem.iter():
                if child is elem:
                    continue
                ctag = child.tag.split("}")[-1].lower()
                cval = (child.text or "").strip()
                if not cval:
                    continue
                if ctag in {"errorcode", "code", "errorid"}:
                    code = cval
                elif ctag in {"message", "errormessage", "longmessage", "shortmessage", "description"}:
                    if not msg or ctag == "longmessage":
                        msg = cval
                elif ctag in {"severitycode", "severity", "status"}:
                    severity = cval
                elif ctag in {"linenumber", "line"}:
                    line_num = cval
                elif ctag in {"customlabel", "sku"}:
                    custom_label = cval
                elif ctag == "itemid":
                    item_id = cval
            if code or msg or (severity and severity.lower() in {"error", "failure"}):
                errors.append({
                    "lineNumber": line_num,
                    "action": "Draft",
                    "status": severity or "Failure",
                    "code": code,
                    "message": msg,
                    "customLabel": custom_label,
                    "itemId": item_id,
                })
    except Exception:
        # Fallback regex parser for non-wellformed or fragmented XML
        for match in re.finditer(r"<Error\b[^>]*>(.*?)</Error>", text, re.IGNORECASE | re.DOTALL):
            block = match.group(1)
            code_m = re.search(r"<(?:Code|ErrorCode|errorId)>(.*?)</", block, re.IGNORECASE)
            msg_m = re.search(r"<(?:LongMessage|Message|ErrorMessage|ShortMessage)>(.*?)</", block, re.IGNORECASE)
            code = code_m.group(1).strip() if code_m else ""
            msg = msg_m.group(1).strip() if msg_m else ""
            if code or msg:
                errors.append({
                    "lineNumber": "",
                    "action": "Draft",
                    "status": "Failure",
                    "code": code,
                    "message": msg,
                    "customLabel": "",
                    "itemId": "",
                })
    return errors


def summarize_feed_result(content: bytes, content_type: str = "") -> dict[str, Any]:
    """Return safe, UI-ready row errors from an eBay feed result file (CSV or XML)."""
    raw = content
    try:
        if raw[:2] == b"\x1f\x8b" or "gzip" in content_type.lower():
            raw = gzip.decompress(raw)
        elif raw[:2] == b"PK" or "zip" in content_type.lower():
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names = archive.namelist()
                if names:
                    raw = archive.read(names[0])
    except (OSError, zipfile.BadZipFile) as exc:
        return {"error": "Unable to decode eBay's result file.", "details": str(exc)[:200]}

    text = raw.decode("utf-8-sig", errors="replace").strip()
    if text.startswith("<") or "<?xml" in text[:50]:
        xml_errors = _parse_xml_feed_errors(text)
        return {
            "format": "xml",
            "errorCount": len(xml_errors),
            "errors": xml_errors,
            "rawPreview": text[:500],
        }

    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except csv.Error:
        rows = []
    if not rows:
        return {"format": "csv", "errorCount": 0, "errors": [], "rawPreview": text[:500]}

    errors: list[dict[str, Any]] = []
    for row in rows:
        status = str(row.get("Status") or "").strip()
        code = str(row.get("ErrorCode") or row.get("Code") or "").strip()
        message = str(row.get("ErrorMessage") or row.get("Message") or "").strip()
        if status.lower() in {"failure", "error"} or code or message:
            errors.append({
                "lineNumber": str(row.get("Line Number") or row.get("LineNumber") or "").strip(),
                "action": str(row.get("Action") or "").strip(),
                "status": status or "Failure",
                "code": code,
                "message": message,
                "customLabel": str(row.get("CustomLabel") or row.get("Custom label (SKU)") or "").strip(),
                "itemId": str(row.get("ItemID") or "").strip(),
            })
    return {"format": "csv", "errorCount": len(errors), "errors": errors, "rowCount": len(rows)}


def get_feed_task_details(task_id: str, *, token: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Fetch task status and attach detailed eBay errors when processing is complete."""
    task = get_feed_task(task_id, token=token, timeout=timeout)
    status = str(task.get("status") or "").upper()
    summary = task.get("uploadSummary") or {}
    failures = int(summary.get("failureCount") or 0)
    if status in {"COMPLETED", "COMPLETED_WITH_ERROR", "FAILED"}:
        try:
            content, content_type, _ = get_feed_result_file(task_id, token=token, timeout=timeout)
            task["resultDetails"] = summarize_feed_result(content, content_type)
        except Exception as exc:
            if failures:
                task["resultDetails"] = {"error": str(exc), "errorCount": failures, "errors": []}
    return task


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
    # eBay documents only FX_LISTING and FX_FULFILLMENT for Seller Hub Feed
    # tasks. Draft creation is a CSV action under FX_LISTING, not its own task
    # type. Ignore stale configuration values such as FX_DRAFT so a deploy
    # cannot silently submit an unsupported task.
    configured = os.environ.get("EBAY_SELLER_HUB_DRAFT_FEED_TYPE", "").strip().upper()
    return configured if configured == DEFAULT_SELLER_HUB_DRAFT_FEED_TYPE else DEFAULT_SELLER_HUB_DRAFT_FEED_TYPE


def seller_hub_feed_type() -> str:
    """Return the non-secret Seller Hub task type used for draft uploads."""
    return _draft_feed_type()


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
