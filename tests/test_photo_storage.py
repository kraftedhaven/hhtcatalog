import io
import os
import unittest
from unittest import mock

from PIL import Image

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

    def test_storage_is_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(storage_status()["provider"], "disabled")
            with self.assertRaises(PhotoStorageError) as context:
                store_photo(seller_id="seller", filename="x.jpg", mime_type="image/jpeg", data=b"not-an-image")
        self.assertEqual(context.exception.category, "configuration")

    def test_rejects_unsupported_type_before_provider_call(self):
        with mock.patch.dict(os.environ, {"PHOTO_STORAGE_PROVIDER": "supabase"}, clear=True):
            with self.assertRaises(PhotoStorageError) as context:
                store_photo(seller_id="seller", filename="x.txt", mime_type="text/plain", data=b"x")
        self.assertEqual(context.exception.status_code, 415)
        self.assertEqual(context.exception.category, "invalid_type")


if __name__ == "__main__":
    unittest.main()
