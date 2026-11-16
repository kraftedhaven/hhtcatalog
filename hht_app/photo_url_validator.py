"""Safe hosted photo URL verification helpers for eBay Seller Hub draft feeds."""
from __future__ import annotations

import io
import ipaddress
import socket
from typing import Any, NoReturn
from urllib.parse import urljoin, urlparse

import requests

PHOTO_URL_TIMEOUT_SECONDS = 5.0
PHOTO_VALIDATION_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_MAX_REDIRECT_HOPS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_PREFIX_MAGIC = (
    b"\xff\xd8\xff",       # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"GIF87a",             # GIF87
    b"GIF89a",             # GIF89
    b"BM",                 # BMP
    b"II*\x00",            # TIFF little-endian
    b"MM\x00*",            # TIFF big-endian
)
PRIVATE_IP_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _raise_photo_error(
    item_index: int, detail: str, cause: Exception | None = None
) -> NoReturn:
    from .ebay_feed import EbayFeedError

    err = EbayFeedError(
        400,
        "seller_review_required",
        f"Seller Hub draft photo review required: item {item_index} {detail}",
        operation="validate_photo_url",
    )
    if cause is not None:
        raise err from cause
    raise err


def is_private_ip(addr_str: str, networks: list[Any] | None = None) -> bool:
    """Return True when an IP string belongs to a private, loopback, or link-local range."""
    net_list = PRIVATE_IP_NETWORKS if networks is None else networks
    try:
        addr = ipaddress.ip_address(addr_str)
    except ValueError:
        return False
    if addr.is_private or addr.is_loopback:
        return True
    return any(addr in net for net in net_list)


def is_private_hostname(hostname: str, networks: list[Any] | None = None) -> bool:
    """Return True if any DNS record for hostname resolves to a private address."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return False
    return any(is_private_ip(sockaddr[0], networks) for _, _, _, _, sockaddr in infos)


def _is_webp_header(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _is_ftyp_header(data: bytes) -> bool:
    return len(data) >= 12 and data[4:8] == b"ftyp"


def is_image_magic_header(data: bytes) -> bool:
    """Check magic bytes for common image formats."""
    if not data:
        return False
    if any(data.startswith(sig) for sig in _PREFIX_MAGIC):
        return True
    return _is_webp_header(data) or _is_ftyp_header(data)


def _verify_https_and_public_host(
    url: str, item_index: int, is_private_fn: Any, *, is_redirect: bool = False
) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        detail = (
            "has an unsafe redirect to a non-HTTPS URL."
            if is_redirect
            else "has a photo URL that is not HTTPS."
        )
        _raise_photo_error(item_index, detail)
    if not parsed.netloc:
        _raise_photo_error(item_index, "has a photo URL that is missing a host.")
    hostname = parsed.hostname or ""
    if is_private_fn(hostname):
        detail = (
            "has a redirect to an internal address."
            if is_redirect
            else "has a photo URL pointing to an internal address."
        )
        _raise_photo_error(item_index, detail)


def _fetch_single_hop(
    url: str, item_index: int, timeout: float, get_fn: Any, *, is_redirect: bool = False
) -> Any:
    try:
        return get_fn(url, stream=True, timeout=timeout, allow_redirects=False)
    except requests.Timeout as exc:
        _raise_photo_error(item_index, "has an unreachable photo URL.", exc)
    except requests.RequestException as exc:
        suffix = " after redirect." if is_redirect else "."
        _raise_photo_error(item_index, f"has an unreachable photo URL{suffix}", exc)


def _follow_safe_redirects(
    initial_response: Any,
    initial_url: str,
    item_index: int,
    timeout: float,
    get_fn: Any,
    is_private_fn: Any,
) -> tuple[Any, list[Any]]:
    followed = initial_response
    current_url = initial_url
    opened = [initial_response]
    for _ in range(_MAX_REDIRECT_HOPS):
        if followed.status_code not in _REDIRECT_STATUSES:
            break
        location = followed.headers.get("Location", "")
        if not location:
            break
        resolved_url = urljoin(current_url, location)
        _verify_https_and_public_host(resolved_url, item_index, is_private_fn, is_redirect=True)
        followed = _fetch_single_hop(resolved_url, item_index, timeout, get_fn, is_redirect=True)
        opened.append(followed)
        current_url = resolved_url
    return followed, opened


def _verify_connected_peer(response: Any, item_index: int, networks: list[Any]) -> None:
    raw_conn = getattr(getattr(response, "raw", None), "_connection", None)
    sock = getattr(raw_conn, "sock", None)
    if sock is None:
        return
    try:
        peer = sock.getpeername()
    except OSError:
        return
    if not peer or not isinstance(peer, tuple):
        return
    if is_private_ip(peer[0], networks):
        _raise_photo_error(item_index, f"connected to a private destination ({peer[0]}).")


def _verify_response_headers(response: Any, item_index: int, max_bytes: int) -> None:
    if response.status_code != 200:
        _raise_photo_error(item_index, "has a photo URL that did not return HTTP 200.")
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            length_val = int(content_length)
        except ValueError:
            length_val = 0
        if length_val > max_bytes:
            _raise_photo_error(
                item_index, "has a photo URL returning a response that exceeds the size limit."
            )
    content_type = response.headers.get("Content-Type", "")
    if not content_type.lower().startswith("image/"):
        _raise_photo_error(item_index, "has a photo URL that is not an image.")


def _read_bounded_body(response: Any, item_index: int, max_bytes: int) -> bytes:
    iter_fn = getattr(response, "iter_content", None)
    if not callable(iter_fn):
        return getattr(response, "content", b"")
    chunks: list[bytes] = []
    total = 0
    for chunk in iter_fn(chunk_size=16384):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            _raise_photo_error(
                item_index, "has a photo URL returning a response that exceeds the size limit."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _verify_decoded_image(full_data: bytes, item_index: int) -> None:
    if not full_data:
        return
    if len(full_data) < 16 or not is_image_magic_header(full_data[:16]):
        _raise_photo_error(
            item_index, "has a photo URL that does not return valid image content."
        )
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(full_data))
        img.verify()
    except Exception as exc:
        _raise_photo_error(
            item_index, "has a photo URL with truncated or corrupted image data.", exc
        )


def _close_responses(responses: list[Any]) -> None:
    for resp in responses:
        close = getattr(resp, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except Exception:
            pass


def validate_single_image_url(
    url: str,
    item_index: int,
    timeout: float,
    *,
    get_fn: Any = requests.get,
    is_private_fn: Any = is_private_hostname,
    max_bytes: int = PHOTO_VALIDATION_MAX_BYTES,
    networks: list[Any] | None = None,
) -> None:
    """Validate a hosted photo URL via HTTPS, public IP checks, bounded streaming, and decoding."""
    _verify_https_and_public_host(url, item_index, is_private_fn)
    initial_response = _fetch_single_hop(url, item_index, timeout, get_fn)
    opened: list[Any] = [initial_response]
    try:
        followed, opened = _follow_safe_redirects(
            initial_response, url, item_index, timeout, get_fn, is_private_fn
        )
        net_list = PRIVATE_IP_NETWORKS if networks is None else networks
        _verify_connected_peer(followed, item_index, net_list)
        _verify_response_headers(followed, item_index, max_bytes)
        full_data = _read_bounded_body(followed, item_index, max_bytes)
        _verify_decoded_image(full_data, item_index)
    finally:
        _close_responses(opened)
