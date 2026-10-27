"""Fast pre-deployment smoke checks; no external services or eBay mutations."""
import os
import unittest
from unittest import mock


class DeploymentSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DEMO_MODE", "true")
        os.environ.setdefault("EBAY_MUTATIONS_ENABLED", "false")
        import app as app_module
        cls.app_module = app_module
        cls.client = app_module.app.test_client()

    def test_health_is_json_and_reports_ok(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("photo_storage", body)
        self.assertIn("sentry_configured", body)

    def test_frontend_entrypoint_serves(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.content_type)

    def test_inventory_api_requires_authentication(self):
        response = self.client.get("/api/inventory/batches")
        # CI deliberately has no Supabase credentials. Production should return
        # 401/403; the local app may return 503 while auth is unconfigured.
        self.assertIn(response.status_code, (401, 403, 503))
        self.assertNotEqual(response.status_code, 200)

    def test_live_ebay_mutations_remain_disabled(self):
        with mock.patch.dict(os.environ, {"EBAY_MUTATIONS_ENABLED": "false"}):
            self.assertFalse(self.app_module.commerce_agent.ebay_mutations_enabled())


if __name__ == "__main__":
    unittest.main()
