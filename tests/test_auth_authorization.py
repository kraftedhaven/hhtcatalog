import os
import tempfile
import unittest
from unittest import mock

import jwt

import app as hht_app
from hht_app import commerce_agent
from hht_app.supabase_auth import authenticate_request


class JwtVerificationTests(unittest.TestCase):
    def setUp(self):
        self.old_url = os.environ.get("SUPABASE_URL")
        os.environ["SUPABASE_URL"] = "https://project.supabase.co"

    def tearDown(self):
        if self.old_url is None:
            os.environ.pop("SUPABASE_URL", None)
        else:
            os.environ["SUPABASE_URL"] = self.old_url

    def test_missing_jwt_is_401(self):
        response = hht_app.app.test_client().post("/api/commerce/actions/action/apply")
        self.assertEqual(response.status_code, 401)

    def _assert_invalid_token_is_401(self, error):
        with hht_app.app.test_request_context(
            "/api/commerce/actions/action/apply",
            headers={"Authorization": "Bearer invalid"},
        ):
            with mock.patch("hht_app.supabase_auth.PyJWKClient") as jwks, mock.patch(
                "hht_app.supabase_auth.jwt.decode", side_effect=error
            ):
                jwks.return_value.get_signing_key_from_jwt.return_value.key = object()
                response, status = authenticate_request()
        self.assertEqual(status, 401)
        self.assertIn("invalid", response.get_json()["error"].lower())

    def test_invalid_signature_is_401(self):
        self._assert_invalid_token_is_401(jwt.InvalidSignatureError())

    def test_expired_token_is_401(self):
        self._assert_invalid_token_is_401(jwt.ExpiredSignatureError())

    def test_wrong_audience_is_401(self):
        self._assert_invalid_token_is_401(jwt.InvalidAudienceError())


class SellerOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.db_file.close()
        self.old_db = os.environ.get("COMMERCE_AGENT_DB")
        os.environ["COMMERCE_AGENT_DB"] = self.db_file.name
        commerce_agent.init_db()
        self.seller_id = "seller-a"
        self.other_seller_id = "seller-b"
        self.auth_user_id = "user-a"
        self.other_auth_user_id = "user-b"
        now = commerce_agent.utc_now()
        with commerce_agent.connect() as db:
            db.execute(
                "INSERT INTO sellers(id,auth_user_id,status,created_at) VALUES(?,?,?,?)",
                (self.seller_id, self.auth_user_id, "active", now),
            )
            db.execute(
                "INSERT INTO sellers(id,auth_user_id,status,created_at) VALUES(?,?,?,?)",
                (self.other_seller_id, self.other_auth_user_id, "active", now),
            )
            db.execute(
                "INSERT INTO listings(listing_id,offer_id,sku,marketplace,data_json,imported_at,seller_id,ownership_classification) VALUES(?,?,?,?,?,?,?,?)",
                (
                    "L1",
                    "O1",
                    "SKU1",
                    "EBAY_US",
                    commerce_agent._json({
                        "listingId": "L1",
                        "offerId": "O1",
                        "sku": "SKU1",
                        "title": "Short Coat",
                        "price": 100,
                        "cat": "57988",
                        "brand": "Brand",
                        "size": "M",
                        "color": "Blue",
                        "ownershipClassification": "inventory_api_managed",
                    }),
                    now,
                    self.seller_id,
                    "inventory_api_managed",
                ),
            )
        commerce_agent.audit_all(self.seller_id)
        self.recommendation_id = commerce_agent.recommendations(seller_id=self.seller_id)[0]["recommendationId"]

    def tearDown(self):
        try:
            os.unlink(self.db_file.name)
        except FileNotFoundError:
            pass
        if self.old_db is None:
            os.environ.pop("COMMERCE_AGENT_DB", None)
        else:
            os.environ["COMMERCE_AGENT_DB"] = self.old_db

    def _approved_action(self):
        return commerce_agent.approve_recommendation(
            self.recommendation_id,
            {"title": "Brand Short Coat"},
            self.seller_id,
            self.auth_user_id,
        )["actionId"]

    def test_wrong_seller_makes_zero_ebay_calls(self):
        action_id = self._approved_action()
        with mock.patch.object(commerce_agent, "update_ebay_offer") as update:
            with self.assertRaises(PermissionError):
                commerce_agent.apply_action(action_id, self.other_seller_id, self.other_auth_user_id)
        update.assert_not_called()

    def test_non_inventory_ownership_makes_zero_ebay_calls(self):
        action_id = self._approved_action()
        with commerce_agent.connect() as db:
            db.execute(
                "UPDATE listings SET ownership_classification=? WHERE listing_id=?",
                ("trading_legacy_managed", "L1"),
            )
        with mock.patch.object(commerce_agent, "update_ebay_offer") as update:
            with self.assertRaises(PermissionError):
                commerce_agent.apply_action(action_id, self.seller_id, self.auth_user_id)
        update.assert_not_called()

    def test_stale_approval_makes_zero_ebay_calls(self):
        action_id = self._approved_action()
        with commerce_agent.connect() as db:
            db.execute(
                "UPDATE listings SET data_json=? WHERE listing_id=?",
                (commerce_agent._json({"listingId": "L1", "offerId": "O1", "sku": "SKU1", "title": "Changed since approval"}), "L1"),
            )
        with mock.patch.object(commerce_agent, "update_ebay_offer") as update:
            with self.assertRaises(ValueError):
                commerce_agent.apply_action(action_id, self.seller_id, self.auth_user_id)
        update.assert_not_called()

    def test_authorized_inventory_action_updates_ebay_once(self):
        action_id = self._approved_action()
        with mock.patch.object(commerce_agent, "update_ebay_offer", return_value={"status": "offer_updated"}) as update:
            result = commerce_agent.apply_action(action_id, self.seller_id, self.auth_user_id)
        update.assert_called_once()
        self.assertEqual(result["status"], "Applied")
        with commerce_agent.connect() as db:
            row = db.execute("SELECT approved_by,applied_by FROM actions WHERE id=?", (action_id,)).fetchone()
        self.assertEqual(row["approved_by"], self.auth_user_id)
        self.assertEqual(row["applied_by"], self.auth_user_id)


if __name__ == "__main__":
    unittest.main()
