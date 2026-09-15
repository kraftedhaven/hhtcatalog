import unittest

from PIL import Image
from io import BytesIO

from hht_app.photo_quality import assess_image
from hht_app.pricing_cache import clear, get, key, put


class FeatureTests(unittest.TestCase):
    def tearDown(self):
        clear()

    def test_photo_quality_flags_small_dark_image(self):
        image = Image.new("RGB", (320, 320), (10, 10, 10))
        output = BytesIO()
        image.save(output, format="JPEG")
        result = assess_image(output.getvalue(), "dark.jpg")
        self.assertEqual(result["status"], "review")
        self.assertTrue(result["issues"])

    def test_pricing_cache_round_trip(self):
        cache_key = key({"q": "Coach handbag"})
        self.assertIsNone(get(cache_key))
        put(cache_key, {"sampleSize": 4, "medianActivePrice": 89.99})
        self.assertEqual(get(cache_key)["medianActivePrice"], 89.99)


if __name__ == "__main__":
    unittest.main()
