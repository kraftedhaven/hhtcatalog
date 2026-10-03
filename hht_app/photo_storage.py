"""Seller-owned photo storage and eBay-ready URL generation.

The adapter is intentionally opt-in. HHT remains usable with storage disabled,
and no provider secret is ever returned to the browser.
"""
from __future__ import annotations

import hashlib
import io
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from PIL import Image, ImageOps

from .commerce_agent import connect, init_db


class PhotoStorageError(RuntimeError):
    def __init__(self, message: str, status_code: int = 503, category: str = "storage"):
        super().__init__(message)
        self.status_code = status_code
        self.category = category


ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif"}
MAX_ORIGINAL_BYTES = int(os.environ.get("PHOTO_MAX_BYTES", str(12 * 1024 * 1024)))
MAX_DIMENSION = int(os.environ.get("PHOTO_MAX_DIMENSION", "1800"))


def storage_provider() -> str:
    return os.environ.get("PHOTO_STORAGE_PROVIDER", "disabled").strip().lower() or "disabled"


def storage_status() -> dict[str, Any]:
    provider = storage_provider()
    configured = _provider_configured(provider)
    return {"provider": provider, "configured": configured, "maxBytes": MAX_ORIGINAL_BYTES, "maxDimension": MAX_DIMENSION}


def _provider_configured(provider: str) -> bool:
    if provider == "supabase":
        return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY") and os.environ.get("PHOTO_STORAGE_BUCKET"))
    if provider == "ibm_cos":
        return bool(os.environ.get("IBM_COS_ENDPOINT") and os.environ.get("IBM_COS_BUCKET") and os.environ.get("IBM_COS_ACCESS_KEY_ID") and os.environ.get("IBM_COS_SECRET_ACCESS_KEY"))
    return False


def store_photo(*, seller_id: str, filename: str, mime_type: str, data: bytes, listing_key: str = "unassigned") -> dict[str, Any]:
    if not seller_id:
        raise PhotoStorageError("Seller ownership is required for photo storage.", 403, "ownership")
    if mime_type not in ALLOWED_TYPES:
        raise PhotoStorageError("Unsupported image type. Use JPEG, PNG, WebP, GIF, HEIC, or HEIF.", 415, "invalid_type")
    if not data or len(data) > MAX_ORIGINAL_BYTES:
        raise PhotoStorageError("Photo is empty or exceeds the configured size limit.", 413, "payload_too_large")
    provider = storage_provider()
    if provider not in {"supabase", "ibm_cos"} or not _provider_configured(provider):
        raise PhotoStorageError("Persistent photo storage is not configured. Set PHOTO_STORAGE_PROVIDER and its provider credentials.", 503, "configuration")

    safe_listing = _safe_segment(listing_key)
    asset_id = str(uuid.uuid4())
    original_key = f"{seller_id}/{safe_listing}/{asset_id}/original-{_safe_filename(filename)}"
    derivative, derivative_type = _compress(data, mime_type)
    derivative_key = f"{seller_id}/{safe_listing}/{asset_id}/ebay.webp"
    checksum = hashlib.sha256(data).hexdigest()

    uploaded_keys = []
    try:
        if provider == "supabase":
            _supabase_upload(original_key, data, mime_type)
            uploaded_keys.append(original_key)
            _supabase_upload(derivative_key, derivative, derivative_type)
            uploaded_keys.append(derivative_key)
            public_url = _supabase_signed_url(derivative_key)
        else:
            _ibm_upload(original_key, data, mime_type)
            uploaded_keys.append(original_key)
            _ibm_upload(derivative_key, derivative, derivative_type)
            uploaded_keys.append(derivative_key)
            public_url = _ibm_signed_url(derivative_key)

        now = datetime.now(timezone.utc).isoformat()
        init_db()
        with connect() as db:
            db.execute(
                """INSERT INTO photo_assets(
                    id,seller_id,listing_key,original_key,derivative_key,provider,
                    original_mime,derivative_mime,original_bytes,derivative_bytes,
                    checksum_sha256,ebay_url,url_expires_at,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (asset_id, seller_id, safe_listing, original_key, derivative_key, provider,
                 mime_type, derivative_type, len(data), len(derivative), checksum,
                 public_url, _url_expiry().isoformat(), now),
            )
    except Exception as exc:
        _delete_uploaded_objects(provider, uploaded_keys)
        if isinstance(exc, PhotoStorageError):
            raise
        raise PhotoStorageError("Photo asset could not be recorded.", 502, "metadata") from exc

    return {
        "assetId": asset_id,
        "provider": provider,
        "listingKey": safe_listing,
        "ebayUrl": public_url,
        "originalBytes": len(data),
        "derivativeBytes": len(derivative),
        "checksumSha256": checksum,
        "urlExpiresAt": _url_expiry().isoformat(),
    }


def _delete_uploaded_objects(provider: str, keys: list[str]) -> None:
    for key in reversed(keys):
        try:
            if provider == "supabase":
                _supabase_delete(key)
            else:
                _ibm_delete(key)
        except Exception:
            pass


def _compress(data: bytes, mime_type: str) -> tuple[bytes, str]:
    try:
        if mime_type in {"image/heic", "image/heif"}:
            from pillow_heif import register_heif_opener

            register_heif_opener()
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA")
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=86, method=6)
        return output.getvalue(), "image/webp"
    except Exception as exc:
        raise PhotoStorageError("Photo could not be decoded or compressed.", 415, "invalid_image") from exc


def _supabase_headers(content_type: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['SUPABASE_SERVICE_ROLE_KEY']}", "apikey": os.environ["SUPABASE_SERVICE_ROLE_KEY"], "Content-Type": content_type, "x-upsert": "false"}


def _supabase_upload(key: str, data: bytes, content_type: str) -> None:
    url = f"{os.environ['SUPABASE_URL'].rstrip('/')}/storage/v1/object/{os.environ['PHOTO_STORAGE_BUCKET'].strip('/')}/{key}"
    try:
        response = requests.post(url, headers=_supabase_headers(content_type), data=data, timeout=20)
    except requests.RequestException as exc:
        raise PhotoStorageError("Photo storage request failed.", 502, "transport") from exc
    if response.status_code not in {200, 201}:
        raise PhotoStorageError("Supabase Storage rejected the photo upload.", response.status_code if response.status_code < 500 else 502, "upload")


def _supabase_delete(key: str) -> None:
    url = f"{os.environ['SUPABASE_URL'].rstrip('/')}/storage/v1/object/{os.environ['PHOTO_STORAGE_BUCKET'].strip('/')}"
    try:
        response = requests.delete(url, headers=_supabase_headers("application/json"), json={"prefixes": [key]}, timeout=10)
    except requests.RequestException as exc:
        raise PhotoStorageError("Supabase Storage could not remove a partial photo upload.", 502, "cleanup") from exc
    if response.status_code not in {200, 204}:
        raise PhotoStorageError("Supabase Storage could not remove a partial photo upload.", 502, "cleanup")


def _supabase_signed_url(key: str) -> str:
    expires = int(os.environ.get("PHOTO_URL_TTL_SECONDS", "3600"))
    url = f"{os.environ['SUPABASE_URL'].rstrip('/')}/storage/v1/object/sign/{os.environ['PHOTO_STORAGE_BUCKET'].strip('/')}/{key}"
    try:
        response = requests.post(url, headers=_supabase_headers("application/json"), json={"expiresIn": expires}, timeout=10)
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise PhotoStorageError("Supabase Storage could not create a signed photo URL.", 502, "signing") from exc
    if response.status_code not in {200, 201} or not isinstance(body, dict) or not body.get("signedURL"):
        raise PhotoStorageError("Supabase Storage returned no signed photo URL.", 502, "signing")
    signed = str(body["signedURL"])
    if signed.startswith("/"):
        return f"{os.environ['SUPABASE_URL'].rstrip('/')}/storage/v1{signed}"
    return signed


def _ibm_client():
    try:
        import boto3
    except ImportError as exc:
        raise PhotoStorageError("IBM Cloud Object Storage support requires boto3 in the deployed worker.", 503, "configuration") from exc
    return boto3.client(
        "s3",
        endpoint_url=os.environ["IBM_COS_ENDPOINT"].rstrip("/"),
        aws_access_key_id=os.environ["IBM_COS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["IBM_COS_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("IBM_COS_REGION", "us-standard"),
    )


def _ibm_upload(key: str, data: bytes, content_type: str) -> None:
    try:
        _ibm_client().put_object(Bucket=os.environ["IBM_COS_BUCKET"], Key=key, Body=data, ContentType=content_type)
    except Exception as exc:
        raise PhotoStorageError("IBM Cloud Object Storage rejected the photo upload.", 502, "upload") from exc


def _ibm_delete(key: str) -> None:
    try:
        _ibm_client().delete_object(Bucket=os.environ["IBM_COS_BUCKET"], Key=key)
    except Exception as exc:
        raise PhotoStorageError("IBM Cloud Object Storage could not remove a partial photo upload.", 502, "cleanup") from exc


def _ibm_signed_url(key: str) -> str:
    expires = int(os.environ.get("PHOTO_URL_TTL_SECONDS", "3600"))
    try:
        return str(_ibm_client().generate_presigned_url("get_object", Params={"Bucket": os.environ["IBM_COS_BUCKET"], "Key": key}, ExpiresIn=expires))
    except Exception as exc:
        raise PhotoStorageError("IBM Cloud Object Storage could not create a signed photo URL.", 502, "signing") from exc


def _url_expiry() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=int(os.environ.get("PHOTO_URL_TTL_SECONDS", "3600")))


def _safe_segment(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "unassigned").strip())
    return (cleaned.strip("-") or "unassigned")[:80]


def _safe_filename(value: str) -> str:
    return _safe_segment(value.rsplit("/", 1)[-1] or "photo.jpg")[:120]
