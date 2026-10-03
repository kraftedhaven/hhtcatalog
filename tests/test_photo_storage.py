import io
import os
import unittest
from unittest import mock

from flask import g
from PIL import Image

import hht_app.photo_storage as photo_storage
from hht_app.photo_storage import PhotoStorageError, _compress, storage_status, store_photo
from app import app


class PhotoStorageTests(unittest.TestCase):
    def test_photo_storage_status_requires_authentication(self):
        with mock.patch.dict(os.environ, {"SUPABASE_URL": "https://example.supabase.co"}):
            response = app.test_client().get("/api/photos/storage")
        self.assertEqual(response.status_code, 401)

    def test_compresses_image_to_webp_derivative(self):
        output = io.BytesIO()
        Image.new("RGB", (2400, 1200), (20, 40, 60)).save(output, format="JPEG")
        derivative, mime = _compress(output.getvalue(), "image/jpeg")
        self.assertEqual(mime, "image/webp")
        self.assertGreater(len(derivative), 0)
        with Image.open(io.BytesIO(derivative)) as image:
            self.assertLessEqual(max(image.size), 1800)

    def test_stores_heic_original_bytes_and_creates_webp_derivative(self):
        from pillow_heif import register_heif_opener

        register_heif_opener()
        output = io.BytesIO()
        Image.new("RGB", (32, 32), (20, 40, 60)).save(output, format="HEIF")
        original = output.getvalue()
        with mock.patch.dict(os.environ, {
            "PHOTO_STORAGE_PROVIDER": "supabase",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "key",
            "PHOTO_STORAGE_BUCKET": "photos",
        }, clear=True), mock.patch.object(photo_storage, "_supabase_upload") as upload, mock.patch.object(
            photo_storage, "_supabase_signed_url", return_value="https://example.supabase.co/signed"
        ), mock.patch.object(photo_storage, "init_db"), mock.patch.object(
            photo_storage, "connect", return_value=mock.MagicMock()
        ):
            store_photo(seller_id="seller", filename="x.heic", mime_type="image/heic", data=original)
        self.assertEqual(upload.call_args_list[0].args[1:], (original, "image/heic"))
        self.assertEqual(upload.call_args_list[1].args[2], "image/webp")

    def test_storage_is_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(storage_status()["provider"], "disabled")
            with self.assertRaises(PhotoStorageError) as context:
                store_photo(seller_id="seller", filename="x.jpg", mime_type="image/jpeg", data=b"not-an-image")
        self.assertEqual(context.exception.category, "configuration")

    def test_storage_status_and_upload_require_provider_credentials(self):
        for provider, values in (
            ("supabase", {"SUPABASE_URL": "https://example.supabase.co", "PHOTO_STORAGE_BUCKET": "photos"}),
            ("ibm_cos", {"IBM_COS_ENDPOINT": "https://example.cos.com", "IBM_COS_BUCKET": "photos", "IBM_COS_ACCESS_KEY_ID": "key"}),
        ):
            with self.subTest(provider=provider), mock.patch.dict(os.environ, {"PHOTO_STORAGE_PROVIDER": provider, **values}, clear=True):
                self.assertFalse(storage_status()["configured"])
                with self.assertRaises(PhotoStorageError) as context:
                    store_photo(seller_id="seller", filename="x.jpg", mime_type="image/jpeg", data=b"image")
                self.assertEqual(context.exception.category, "configuration")

    def test_signing_failure_removes_uploaded_objects(self):
        output = io.BytesIO()
        Image.new("RGB", (32, 32), (20, 40, 60)).save(output, format="JPEG")
        with mock.patch.dict(os.environ, {
            "PHOTO_STORAGE_PROVIDER": "supabase",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "key",
            "PHOTO_STORAGE_BUCKET": "photos",
        }, clear=True), mock.patch.object(photo_storage, "_supabase_upload"), mock.patch.object(
            photo_storage, "_supabase_signed_url", side_effect=PhotoStorageError("signing failed", category="signing")
        ), mock.patch.object(photo_storage, "_supabase_delete") as delete:
            with self.assertRaises(PhotoStorageError):
                store_photo(seller_id="seller", filename="x.jpg", mime_type="image/jpeg", data=output.getvalue())
        self.assertEqual(delete.call_count, 2)
        self.assertTrue(delete.call_args_list[0].args[0].endswith("/ebay.webp"))
        self.assertIn("/original-x.jpg", delete.call_args_list[1].args[0])

    def test_metadata_failure_removes_uploaded_objects(self):
        output = io.BytesIO()
        Image.new("RGB", (32, 32), (20, 40, 60)).save(output, format="JPEG")
        with mock.patch.dict(os.environ, {
            "PHOTO_STORAGE_PROVIDER": "supabase",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "key",
            "PHOTO_STORAGE_BUCKET": "photos",
        }, clear=True), mock.patch.object(photo_storage, "_supabase_upload"), mock.patch.object(
            photo_storage, "_supabase_signed_url", return_value="https://example.supabase.co/signed"
        ), mock.patch.object(photo_storage, "init_db"), mock.patch.object(
            photo_storage, "connect", side_effect=RuntimeError("database unavailable")
        ), mock.patch.object(photo_storage, "_supabase_delete") as delete:
            with self.assertRaises(PhotoStorageError) as context:
                store_photo(seller_id="seller", filename="x.jpg", mime_type="image/jpeg", data=output.getvalue())
        self.assertEqual(context.exception.category, "metadata")
        self.assertEqual(delete.call_count, 2)

    def test_photo_batch_returns_successes_and_failures_per_file(self):
        def authenticate():
            g.supabase_user = {"sub": "user"}

        def store(**kwargs):
            if kwargs["filename"] == "failed.jpg":
                raise PhotoStorageError("Upload failed.", 502, "upload")
            return {"assetId": "saved"}

        with mock.patch("app.authenticate_request", side_effect=authenticate), mock.patch(
            "app.commerce_agent.ensure_seller_identity", return_value={"id": "seller"}
        ), mock.patch("app.store_photo", side_effect=store):
            response = app.test_client().post(
                "/api/photos",
                data={"file": [(io.BytesIO(b"one"), "saved.jpg"), (io.BytesIO(b"two"), "failed.jpg")]},
                content_type="multipart/form-data",
            )
        body = response.get_json()["result"]
        self.assertEqual(response.status_code, 207)
        self.assertEqual(body["count"], 1)
        self.assertEqual([entry["status"] for entry in body["files"]], ["stored", "error"])
        self.assertEqual(body["assets"][0]["assetId"], "saved")
        self.assertEqual(body["failures"][0]["category"], "upload")

    def test_rejects_unsupported_type_before_provider_call(self):
        with mock.patch.dict(os.environ, {"PHOTO_STORAGE_PROVIDER": "supabase"}, clear=True):
            with self.assertRaises(PhotoStorageError) as context:
                store_photo(seller_id="seller", filename="x.txt", mime_type="text/plain", data=b"x")
        self.assertEqual(context.exception.status_code, 415)
        self.assertEqual(context.exception.category, "invalid_type")


if __name__ == "__main__":
    unittest.main()
