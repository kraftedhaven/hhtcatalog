import unittest
from unittest.mock import patch

from PIL import Image
from io import BytesIO

from hht_app.photo_quality import assess_image
from hht_app.pricing_cache import clear, get, key, put
from hht_app.ebay_active import fetch_listing_detail


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

    def test_get_item_parses_official_specifics(self):
        xml = b'''<?xml version="1.0"?><GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack><Item><ItemID>123</ItemID><Title>Coach Bag</Title><Description>Details</Description><PrimaryCategory><CategoryID>169291</CategoryID><CategoryName>Handbags</CategoryName></PrimaryCategory><ConditionID>3000</ConditionID><ConditionDescription>Used</ConditionDescription><SellingStatus><CurrentPrice currencyID="USD">58.88</CurrentPrice><QuantitySold>1</QuantitySold><WatchCount>4</WatchCount></SellingStatus><Quantity>1</Quantity><ItemSpecifics><NameValueList><Name>Brand</Name><Value>Coach</Value></NameValueList><NameValueList><Name>Material</Name><Value>Leather</Value></NameValueList></ItemSpecifics><PictureDetails><PictureURL>https://example.com/a.jpg</PictureURL></PictureDetails></Item></GetItemResponse>'''
        class Response:
            status_code = 200
            content = xml
        with patch('hht_app.ebay_active.seller_access_token', return_value='token'), patch('hht_app.ebay_active.requests.post', return_value=Response()):
            result = fetch_listing_detail('123')
        self.assertEqual(result['cat'], '169291')
        self.assertEqual(result['itemSpecifics']['Brand'], 'Coach')
        self.assertEqual(result['itemSpecifics']['Material'], 'Leather')
        self.assertEqual(result['watchCount'], 4)


if __name__ == "__main__":
    unittest.main()
