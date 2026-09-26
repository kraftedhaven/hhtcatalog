import re
import unittest
from pathlib import Path
from unittest import mock

from hht_app import commerce_agent


class MigrationTests(unittest.TestCase):
    migration_dir = Path(__file__).parents[1] / "supabase" / "migrations"

    def test_ordered_migrations_create_schema_before_phase4_and_versioning(self):
        names = sorted(path.name for path in self.migration_dir.glob("*.sql"))
        self.assertEqual(names[:3], [
            "202609250300_commerce_agent_schema.sql",
            "202609250330_phase4_truthful_metrics_rls.sql",
            "202609250345_recommendation_versioning.sql",
        ])
        baseline = (self.migration_dir / names[0]).read_text()
        versioning = (self.migration_dir / names[2]).read_text()
        for table in ("listings", "recommendations", "actions", "settings", "commerce_jobs", "enrichment_checkpoints", "listing_performance_daily", "listing_versions", "fulfillment_orders", "rotation_actions"):
            self.assertRegex(baseline, rf"CREATE TABLE IF NOT EXISTS public\.{table}")
        self.assertIn("recommendations_one_current_per_listing", versioning)
        self.assertIn("WHERE is_current = true", versioning)
        self.assertIn("listing_state_hash", versioning)
        rls = (self.migration_dir / names[1]).read_text()
        self.assertIn("public.rotation_actions ENABLE ROW LEVEL SECURITY", rls)
        self.assertIn("public.rotation_actions FROM anon", rls)

    def test_postgres_init_db_is_read_only_schema_check(self):
        required_tables = [
            {"table_name": table} for table in (
                "listings", "recommendations", "actions", "settings", "commerce_jobs",
                "enrichment_checkpoints", "listing_performance_daily", "listing_versions",
                "fulfillment_orders", "rotation_actions", "sellers", "ebay_accounts",
            )
        ]
        columns = [
            {"table_name": table, "column_name": column}
            for table, values in {
                "recommendations": ("version_number", "is_current", "seller_id"),
                "actions": ("recommendation_version", "listing_snapshot_json", "listing_state_hash", "seller_id", "approved_by", "applied_by", "rolled_back_by"),
                "listings": ("lifecycle_status", "listing_start_time", "quantity_sold", "watch_count", "ownership_classification", "seller_id", "ebay_account_id"),
            }.items()
            for column in values
        ]

        class FakeDB:
            postgres = True

            def __init__(self):
                self.queries = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, params=()):
                self.queries.append(query)
                if "information_schema.tables" in query:
                    return _Rows(required_tables)
                if "information_schema.columns" in query:
                    return _Rows(columns)
                raise AssertionError(f"Unexpected runtime query: {query}")

        class _Rows(list):
            def fetchall(self):
                return self

        fake = FakeDB()
        with mock.patch.object(commerce_agent, "connect", return_value=fake):
            commerce_agent.init_db()
        self.assertTrue(fake.queries)
        self.assertFalse(any(re.search(r"\b(CREATE|ALTER|DROP|INSERT|UPDATE|DELETE)\b", query, re.I) for query in fake.queries))


if __name__ == "__main__":
    unittest.main()
