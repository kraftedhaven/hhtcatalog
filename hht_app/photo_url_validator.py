"""Safe hosted photo URL verification helpers for eBay Seller Hub draft feeds."""
from __future__ import annotations

import io
import ipaddress
import socket
from dataclasses import dataclass
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


@dataclass(frozen=True)
class PhotoCheckContext:
    """Configuration bundle for hosted photo validation hops."""

    item_index: int
    timeout: float
    get_fn: Any = requests.get
    is_private_fn: Any = None
    max_bytes: int = PHOTO_VALIDATION_MAX_BYTES
    networks: list[Any] | None = None

    def effective_networks(self) -> list[Any]:
        return PRIVATE_IP_NETWORKS if self.networks is None else self.networks


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
    raise err from cause if cause is not None else err


def is_private_ip(addr_str: str, networks: list[Any] | None = None) -> bool:
    """Return True when an IP string belongs to a private, loopback, or link-local range."""
    net_list = PRIVATE_IP_NETWORKS if networks is None else networks
    try:
        addr = ipaddress.ip_address(addr_str)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or any(addr in net for net in net_list)


def is_private_hostname(hostname: str, networks: list[Any] | None = None) -> bool:
    """Return True if any DNS record for hostname resolves to a private address."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return False
    return any(is_private_ip(sockaddr[0], networks) for _, _, _, _, sockaddr in infos)


_HTTPS_ERR = {
    False: "has a photo URL that is not HTTPS.",
    True: "has an unsafe redirect to a non-HTTPS URL.",
}
_PRIVATE_ERR = {
    False: "has a photo URL pointing to an internal address.",
    True: "has a redirect to an internal address.",
}


def is_image_magic_header(data: bytes) -> bool:
    """Check magic bytes for common image formats."""
    if not data:
        return False
    if any(data.startswith(sig) for sig in _PREFIX_MAGIC):
        return True
    if len(data) < 12:
        return False
    return (data[0:4], data[8:12]) == (b"RIFF", b"WEBP") or data[4:8] == b"ftyp"


def _verify_https_and_public_host(
    url: str, ctx: PhotoCheckContext, *, is_redirect: bool = False
) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        _raise_photo_error(ctx.item_index, _HTTPS_ERR[is_redirect])
    if not parsed.netloc:
        _raise_photo_error(ctx.item_index, "has a photo URL that is missing a host.")
    checker = ctx.is_private_fn or (lambda host: is_private_hostname(host, ctx.effective_networks()))
    if checker(parsed.hostname or ""):
        _raise_photo_error(ctx.item_index, _PRIVATE_ERR[is_redirect])


def _fetch_single_hop(
    url: str, ctx: PhotoCheckContext, *, is_redirect: bool = False
) -> Any:
    try:
        return ctx.get_fn(url, stream=True, timeout=ctx.timeout, allow_redirects=False)
    except requests.Timeout as exc:
        _raise_photo_error(ctx.item_index, "has an unreachable photo URL.", exc)
    except requests.RequestException as exc:
        suffix = " after redirect." if is_redirect else "."
        _raise_photo_error(ctx.item_index, f"has an unreachable photo URL{suffix}", exc)


def _follow_safe_redirects(
    initial_response: Any, initial_url: str, ctx: PhotoCheckContext
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
        _verify_https_and_public_host(resolved_url, ctx, is_redirect=True)
        followed = _fetch_single_hop(resolved_url, ctx, is_redirect=True)
        opened.append(followed)
        current_url = resolved_url
    return followed, opened


def _peer_ip_address(response: Any) -> str | None:
    raw_conn = getattr(getattr(response, "raw", None), "_connection", None)
    sock = getattr(raw_conn, "sock", None)
    if sock is None:
        return None
    try:
        peer = sock.getpeername()
    except OSError:
        return None
    return peer[0] if isinstance(peer, tuple) and peer else None


def _verify_connected_peer(response: Any, ctx: PhotoCheckContext) -> None:
    peer_host = _peer_ip_address(response)
    if peer_host and is_private_ip(peer_host, ctx.effective_networks()):
        _raise_photo_error(ctx.item_index, f"connected to a private destination ({peer_host}).")


def _parse_int_header(raw_value: Any) -> int:
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return 0


def _verify_response_headers(response: Any, ctx: PhotoCheckContext) -> None:
    if response.status_code != 200:
        _raise_photo_error(ctx.item_index, "has a photo URL that did not return HTTP 200.")
    if _parse_int_header(response.headers.get("Content-Length")) > ctx.max_bytes:
        _raise_photo_error(ctx.item_index, "has a photo URL returning a response that exceeds the size limit.")
    content_type = response.headers.get("Content-Type", "")
    if not content_type.lower().startswith("image/"):
        _raise_photo_error(ctx.item_index, "has a photo URL that is not an image.")


def _read_bounded_body(response: Any, ctx: PhotoCheckContext) -> bytes:
    iter_fn = getattr(response, "iter_content", None)
    if not callable(iter_fn):
        return getattr(response, "content", b"")
    chunks: list[bytes] = []
    total = 0
    for chunk in iter_fn(chunk_size=16384):
        if not chunk:
            continue
        total += len(chunk)
        if total > ctx.max_bytes:
            _raise_photo_error(ctx.item_index, "has a photo URL returning a response that exceeds the size limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def _verify_decoded_image(full_data: bytes, ctx: PhotoCheckContext) -> None:
    if not full_data:
        return
    if len(full_data) < 16 or not is_image_magic_header(full_data[:16]):
        _raise_photo_error(ctx.item_index, "has a photo URL that does not return valid image content.")
    try:
        from PIL import Image

        Image.open(io.BytesIO(full_data)).verify()
    except Exception as exc:
        _raise_photo_error(ctx.item_index, "has a photo URL with truncated or corrupted image data.", exc)


def _close_one(resp: Any) -> None:
    close = getattr(resp, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def validate_single_image_url(url: str, ctx: PhotoCheckContext) -> None:
    """Validate a hosted photo URL via HTTPS, public IP checks, bounded streaming, and decoding."""
    _verify_https_and_public_host(url, ctx)
    initial_response = _fetch_single_hop(url, ctx)
    opened: list[Any] = [initial_response]
    try:
        followed, opened = _follow_safe_redirects(initial_response, url, ctx)
        _verify_connected_peer(followed, ctx)
        _verify_response_headers(followed, ctx)
        full_data = _read_bounded_body(followed, ctx)
        _verify_decoded_image(full_data, ctx)
    finally:
        for resp in opened:
            _close_one(resp)
