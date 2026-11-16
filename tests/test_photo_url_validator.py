"""Tests for hosted photo URL validation and safe redirect handling."""
from __future__ import annotations

import io
import unittest
from unittest import mock

from PIL import Image

from hht_app.ebay_feed import (
    EbayFeedError,
    PHOTO_VALIDATION_MAX_BYTES,
    _validate_image_url,
)


def _jpeg_bytes(color: str = "blue", size: tuple[int, int] = (80, 80)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


class PhotoUrlValidatorTests(unittest.TestCase):
    def test_hosted_image_rejects_http_and_internal_destinations(self):
        with self.assertRaises(EbayFeedError) as ctx:
            _validate_image_url("http://example.com/photo.jpg", 1, 5.0)
        self.assertIn("not HTTPS", ctx.exception.safe_message)

        with self.assertRaises(EbayFeedError) as ctx2:
            _validate_image_url("https://127.0.0.1/photo.jpg", 1, 5.0)
        self.assertIn("internal", ctx2.exception.safe_message.lower())

    def test_hosted_image_streams_with_hard_byte_limit(self):
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "image/jpeg", "Content-Length": str(PHOTO_VALIDATION_MAX_BYTES + 1024)}
        mock_resp.iter_content.return_value = [b"A" * 16384]

        with mock.patch("hht_app.ebay_feed.requests.get", return_value=mock_resp), \
             mock.patch("hht_app.ebay_feed._is_private_address", return_value=False):
            with self.assertRaises(EbayFeedError) as ctx:
                _validate_image_url("https://example.com/oversized.jpg", 1, 5.0)
            self.assertIn("size limit", ctx.exception.safe_message.lower())

    def test_hosted_image_rejects_truncated_or_corrupt_content(self):
        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"truncated"
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "image/jpeg"}
        mock_resp.iter_content.return_value = [fake_jpeg]

        with mock.patch("hht_app.ebay_feed.requests.get", return_value=mock_resp), \
             mock.patch("hht_app.ebay_feed._is_private_address", return_value=False):
            with self.assertRaises(EbayFeedError) as ctx:
                _validate_image_url("https://example.com/corrupt.jpg", 1, 5.0)
            self.assertIn("truncated or corrupt", ctx.exception.safe_message.lower())

    def test_hosted_image_accepts_valid_image(self):
        valid_bytes = _jpeg_bytes("blue", (100, 100))
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "image/jpeg", "Content-Length": str(len(valid_bytes))}
        mock_resp.iter_content.return_value = [valid_bytes]

        with mock.patch("hht_app.ebay_feed.requests.get", return_value=mock_resp), \
             mock.patch("hht_app.ebay_feed._is_private_address", return_value=False):
            _validate_image_url("https://example.com/valid.jpg", 1, 5.0)

    def test_hosted_image_resolves_relative_https_redirect_and_rejects_unsafe_redirect(self):
        valid_bytes = _jpeg_bytes("green", (60, 60))
        redir = mock.MagicMock(status_code=302, headers={"Location": "/cdn/resolved_photo.jpg"})
        final_resp = mock.MagicMock(
            status_code=200,
            headers={"Content-Type": "image/jpeg", "Content-Length": str(len(valid_bytes))},
        )
        final_resp.iter_content.return_value = [valid_bytes]
        seen_urls: list[str] = []

        def fake_get(url, **kwargs):
            seen_urls.append(url)
            return redir if url.endswith("/initial.jpg") else final_resp

        with mock.patch("hht_app.ebay_feed.requests.get", side_effect=fake_get), \
             mock.patch("hht_app.ebay_feed._is_private_address", return_value=False):
            _validate_image_url("https://cdn.example.com/photos/initial.jpg", 1, 5.0)
        self.assertEqual(seen_urls, ["https://cdn.example.com/photos/initial.jpg", "https://cdn.example.com/cdn/resolved_photo.jpg"])

        bad_redir = mock.MagicMock(status_code=301, headers={"Location": "http://insecure.example.com/photo.jpg"})
        with mock.patch("hht_app.ebay_feed.requests.get", return_value=bad_redir), \
             mock.patch("hht_app.ebay_feed._is_private_address", return_value=False):
            with self.assertRaises(EbayFeedError) as err_ctx:
                _validate_image_url("https://cdn.example.com/photos/initial.jpg", 1, 5.0)
        self.assertIn("non-HTTPS", err_ctx.exception.safe_message)


if __name__ == "__main__":
    unittest.main()
