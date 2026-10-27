"""Unit tests for Sentry configuration and privacy scrubbing."""

import os
import unittest
from unittest import mock

from hht_app.sentry import (
    init_sentry,
    sanitize_string,
    sanitize_url,
    sanitize_value,
    scrub_breadcrumb,
    scrub_event,
)


class SentryPrivacyScrubberTests(unittest.TestCase):
    def test_init_sentry_inactive_without_dsn(self):
        with mock.patch.dict(os.environ, {"SENTRY_DSN": ""}, clear=True):
            self.assertFalse(init_sentry())

    def test_init_sentry_active_with_dsn(self):
        with mock.patch.dict(
            os.environ,
            {
                "SENTRY_DSN": "https://examplePublicKey@o0.ingest.sentry.io/0",
                "SENTRY_ENVIRONMENT": "production",
                "SENTRY_RELEASE": "v432",
            },
        ):
            with mock.patch("sentry_sdk.init") as mock_init:
                self.assertTrue(init_sentry())
                mock_init.assert_called_once()
                kwargs = mock_init.call_args.kwargs
                self.assertEqual(kwargs["dsn"], "https://examplePublicKey@o0.ingest.sentry.io/0")
                self.assertEqual(kwargs["environment"], "production")
                self.assertEqual(kwargs["release"], "v432")
                self.assertFalse(kwargs["send_default_pii"])

    def test_sanitize_database_urls(self):
        url = "postgresql://postgres:mySuperSecret123@ep-cool-db.us-east-2.aws.neon.tech/hht_catalog?sslmode=require"
        scrubbed = sanitize_url(url)
        self.assertNotIn("mySuperSecret123", scrubbed)
        self.assertIn("[SCRUBBED]", scrubbed)
        self.assertIn("ep-cool-db.us-east-2.aws.neon.tech", scrubbed)

    def test_sanitize_signed_photo_urls(self):
        signed_url = (
            "https://s3.us-south.cloud-object-storage.appdomain.cloud/my-bucket/item123.jpg"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Credential=SECRET_CRED_123%2F20261026"
            "&X-Amz-Date=20261026T000000Z"
            "&X-Amz-Signature=d8f9e0a1b2c3d4e5f6a7b8c9d0"
        )
        scrubbed = sanitize_url(signed_url)
        self.assertNotIn("d8f9e0a1b2c3d4e5f6a7b8c9d0", scrubbed)
        self.assertNotIn("SECRET_CRED_123", scrubbed)
        self.assertIn("[SCRUBBED]", scrubbed)
        self.assertIn("my-bucket/item123.jpg", scrubbed)

    def test_sanitize_tokens_and_jwt_strings(self):
        jwt_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        text = f"User logged in with token {jwt_token} successfully."
        scrubbed = sanitize_string(text)
        self.assertNotIn(jwt_token, scrubbed)
        self.assertIn("[JWT_SCRUBBED]", scrubbed)

    def test_sanitize_image_data_uris_and_bytes(self):
        data_uri = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="
        scrubbed_uri = sanitize_string(data_uri)
        self.assertEqual(scrubbed_uri, "[IMAGE_DATA_URL_SCRUBBED]")

        binary_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 50
        scrubbed_bytes = sanitize_value("photo_bytes", binary_bytes)
        self.assertIn("[IMAGE_BYTES_SCRUBBED", scrubbed_bytes)

    def test_scrub_event_cleans_request_headers_and_body(self):
        raw_event = {
            "request": {
                "url": "https://api.hht.com/api/inventory/items?token=secret-query-param",
                "headers": {
                    "Authorization": "Bearer v1.secret.token.here",
                    "Cookie": "sb-access-token=super_secret_cookie_val",
                    "Content-Type": "application/json",
                    "X-Custom-Header": "harmless",
                },
                "data": {
                    "sellerId": "seller-123",
                    "apiKey": "groq-key-abc123secret",
                    "ebayRefreshToken": "v^1.1#i^1#r^1#secret_refresh_token",
                    "photos": [
                        {
                            "url": "https://s3.cos.ibm.com/b/p.jpg?X-Amz-Signature=bad_secret_sig",
                            "data": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
                        }
                    ],
                },
            },
            "breadcrumbs": {
                "values": [
                    {
                        "message": "Connected to postgresql://admin:pass987@localhost:5432/db",
                        "data": {"access_token": "token123", "normal": "ok"},
                    }
                ]
            },
            "extra": {
                "DATABASE_URL": "postgresql://postgres:secret_pass@127.0.0.1:5432/prod",
                "raw_response": "data:image/jpeg;base64,/9j/4AAQSkZJRg...",
            },
        }

        scrubbed = scrub_event(raw_event)
        req = scrubbed["request"]
        headers = req["headers"]

        # Assert headers scrubbed
        self.assertEqual(headers["Authorization"], "[SCRUBBED]")
        self.assertEqual(headers["Cookie"], "[SCRUBBED]")
        self.assertEqual(headers["X-Custom-Header"], "harmless")

        # Assert body scrubbed
        data = req["data"]
        self.assertEqual(data["apiKey"], "[SCRUBBED]")
        self.assertEqual(data["ebayRefreshToken"], "[SCRUBBED]")
        self.assertNotIn("bad_secret_sig", str(data["photos"]))
        self.assertNotIn("iVBORw0KGgoAAAANSUhEUg", str(data["photos"]))

        # Assert breadcrumbs scrubbed
        crumb = scrubbed["breadcrumbs"]["values"][0]
        self.assertNotIn("pass987", crumb["message"])
        self.assertEqual(crumb["data"]["access_token"], "[SCRUBBED]")

        # Assert extra scrubbed
        self.assertNotIn("secret_pass", scrubbed["extra"]["DATABASE_URL"])
        self.assertIn("[SCRUBBED]", scrubbed["extra"]["DATABASE_URL"])

    def test_scrub_breadcrumb(self):
        crumb = {
            "message": "Query failed on postgresql://root:mypass@localhost/db",
            "data": {"auth_token": "secret_token_123"},
        }
        res = scrub_breadcrumb(crumb)
        self.assertNotIn("mypass", res["message"])
        self.assertEqual(res["data"]["auth_token"], "[SCRUBBED]")


if __name__ == "__main__":
    unittest.main()
