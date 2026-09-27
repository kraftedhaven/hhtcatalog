import unittest

from hht_app.analysis_contract import breakground_readiness, canonical_analysis_run
from hht_app.providers import DEFAULT_HOSTED_PROVIDER_ORDER, PROVIDER_CALLERS


class AnalysisContractTests(unittest.TestCase):
    def test_breakground_is_ready_but_not_an_active_provider(self):
        readiness = breakground_readiness()
        self.assertFalse(readiness["enabled"])
        self.assertFalse(readiness["eBayMutationAllowed"])
        self.assertNotIn("breakground", PROVIDER_CALLERS)
        self.assertNotIn("breakground", DEFAULT_HOSTED_PROVIDER_ORDER)
        self.assertIn("api_url_or_sdk", readiness["requiredContract"])

    def test_canonical_run_is_review_only_for_supported_providers(self):
        for provider in ("groq", "breakground", "nvidia_worker"):
            run = canonical_analysis_run(provider, {"title": "Example"}, input_photo_count=2)
            self.assertEqual(run["provider"], provider)
            self.assertTrue(run["reviewOnly"])
            self.assertEqual(run["reviewStatus"], "pending")
            self.assertEqual(run["inputPhotoCount"], 2)

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(ValueError):
            canonical_analysis_run("unknown", {})
