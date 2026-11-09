"""Tests for official eBay Seller Hub Reports Create Drafts CSV export and Category Aspects validation."""
import csv
import io
import unittest
from unittest import mock

from hht_app import inventory_workspace
from hht_app.ebay_taxonomy import validate_listing
from hht_app.seller_hub_draft_template import (
    DraftExportValidationError,
    build_default_template_text,
    export_seller_hub_draft_csv,
    parse_official_template,
    validate_item_for_draft,
)


class SellerHubDraftCsvTests(unittest.TestCase):
    def setUp(self):
        self.seller_id = "test-seller-csv-123"
        self.valid_item = {
            "id": "item-001",
            "title": "Nike Air Zoom Pegasus 38 Running Shoes Mens Size 10.5",
            "cat": "93427",
            "price": "79.99",
            "quantity": "1",
            "cid": "1000",
            "desc": "<p>Brand new in box.</p>",
            "pic": "https://cos.ibm.com/bucket/pegasus1.jpg|https://cos.ibm.com/bucket/pegasus2.jpg",
            "brand": "Nike",
            "size": "10.5",
            "itemSpecifics": {
                "Brand": "Nike",
                "US Shoe Size": "10.5",
                "Color": "Black",
            },
            "workflowStatus": "approved",
            "sellerConfirmed": True,
        }

    def test_preservation_of_info_rows_and_official_headers(self):
        custom_template = (
            '#INFO,Version=0.0.2,Template= eBay-draft-listings-template_US\n'
            '#INFO Custom instructions line 1\n'
            '#INFO Custom instructions line 2\n'
            'Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8),Custom label (SKU),Category ID,Title,UPC,Price,Quantity,Item photo URL,Condition ID,Description,Format,C:Brand,C:Size\n'
        )
        csv_out = export_seller_hub_draft_csv([self.valid_item], template_text=custom_template)
        lines = csv_out.strip().split("\n")

        # Must preserve the 3 #INFO rows
        self.assertTrue(lines[0].startswith("#INFO"))
        self.assertIn("Version=0.0.2", lines[0])
        self.assertTrue(lines[1].startswith("#INFO Custom instructions line 1"))
        self.assertTrue(lines[2].startswith("#INFO Custom instructions line 2"))

        # Must preserve the exact header row
        header_row = lines[3]
        self.assertIn("Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)", header_row)
        self.assertIn("C:Brand", header_row)
        self.assertIn("C:Size", header_row)

    def test_action_is_strictly_draft_and_no_live_actions(self):
        # Even if someone attempts to pass an action or live action in item data
        item_with_live = dict(self.valid_item)
        item_with_live["Action"] = "Add"
        item_with_live["action"] = "Publish"

        csv_out = export_seller_hub_draft_csv([item_with_live])
        reader = csv.reader(io.StringIO(csv_out))
        data_rows = [r for r in reader if r and not r[0].startswith("#INFO")]
        headers = data_rows[0]
        data = data_rows[1]
        action_idx = headers.index("Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)")

        # Action MUST be Draft
        self.assertEqual(data[action_idx], "Draft")

        # Live actions must NEVER be present anywhere in the Action column
        for row in data_rows[1:]:
            val = row[action_idx].lower()
            self.assertNotIn(val, {"add", "revise", "relist", "end", "publish", "verifyadd"})

    def test_numeric_category_id_required(self):
        # Valid numeric Category ID passes
        item = dict(self.valid_item)
        item["cat"] = "93427"
        errors = validate_item_for_draft(item)
        self.assertEqual(len(errors), 0)

        # Non-numeric Category ID is blocked
        item["cat"] = "NotNumericCategory"
        errors = validate_item_for_draft(item)
        self.assertTrue(any(e["field"] == "Category ID" and "numeric" in e["message"] for e in errors))

        # Blank Category ID is blocked
        item["cat"] = ""
        errors = validate_item_for_draft(item)
        self.assertTrue(any(e["field"] == "Category ID" for e in errors))

    def test_80_character_title_limit(self):
        item = dict(self.valid_item)
        item["title"] = "A" * 81
        errors = validate_item_for_draft(item)
        self.assertTrue(any(e["field"] == "Title" and "80" in e["message"] for e in errors))

        # 80 characters is allowed
        item["title"] = "A" * 80
        errors = validate_item_for_draft(item)
        self.assertEqual(len(errors), 0)

    def test_price_and_quantity_validation(self):
        item = dict(self.valid_item)
        # Price zero or negative is blocked
        item["price"] = "0.00"
        errors = validate_item_for_draft(item)
        self.assertTrue(any(e["field"] == "Price" for e in errors))

        item["price"] = "-15.00"
        errors = validate_item_for_draft(item)
        self.assertTrue(any(e["field"] == "Price" for e in errors))

        # Quantity invalid
        item["price"] = "25.00"
        item["quantity"] = "0"
        errors = validate_item_for_draft(item)
        self.assertTrue(any(e["field"] == "Quantity" for e in errors))

        item["quantity"] = "invalid"
        errors = validate_item_for_draft(item)
        self.assertTrue(any(e["field"] == "Quantity" for e in errors))

    def test_pipe_separated_photo_url_formatting(self):
        item = dict(self.valid_item)
        item["pic"] = "https://example.com/photo1.jpg https://example.com/photo2.jpg"
        csv_out = export_seller_hub_draft_csv([item])
        reader = csv.reader(io.StringIO(csv_out))
        data_rows = [r for r in reader if r and not r[0].startswith("#INFO")]
        headers = data_rows[0]
        data = data_rows[1]
        photo_idx = headers.index("Item photo URL")
        photo_val = data[photo_idx]
        self.assertEqual(photo_val, "https://example.com/photo1.jpg|https://example.com/photo2.jpg")

    def test_https_photo_validation_rejects_insecure_or_blob_urls(self):
        # http:// is rejected
        item_http = dict(self.valid_item)
        item_http["pic"] = "http://insecure.com/photo.jpg"
        errors = validate_item_for_draft(item_http)
        self.assertTrue(any(e["field"] == "Item photo URL" and "HTTPS" in e["message"] for e in errors))

        # blob: is rejected
        item_blob = dict(self.valid_item)
        item_blob["pic"] = "blob:http://localhost:5173/uuid-1234"
        errors = validate_item_for_draft(item_blob)
        self.assertTrue(any(e["field"] == "Item photo URL" for e in errors))

        # file:// is rejected
        item_file = dict(self.valid_item)
        item_file["pic"] = "file:///C:/Users/photo.jpg"
        errors = validate_item_for_draft(item_file)
        self.assertTrue(any(e["field"] == "Item photo URL" for e in errors))

    def test_category_specific_required_field_blocking(self):
        mock_category_metadata = {
            "status": "ok",
            "categoryId": "93427",
            "requiredAspects": ["Brand", "US Shoe Size", "Department"],
            "fields": [
                {"name": "Brand", "required": True, "values": ["Nike", "Adidas", "Puma"]},
                {"name": "US Shoe Size", "required": True, "values": ["10", "10.5", "11"]},
                {"name": "Department", "required": True, "values": ["Men", "Women", "Unisex"]},
            ],
        }

        # Item missing "Department"
        item = dict(self.valid_item)
        item["itemSpecifics"] = {"Brand": "Nike", "US Shoe Size": "10.5"}
        item["dept"] = ""

        errors = validate_item_for_draft(item, category_metadata=mock_category_metadata)
        self.assertTrue(any(e["field"] == "Department" and "missing" in e["message"] for e in errors))

        # Trying to export should raise DraftExportValidationError
        with self.assertRaises(DraftExportValidationError) as ctx:
            export_seller_hub_draft_csv([item], category_metadata_map={"93427": mock_category_metadata})
        self.assertIn("Department", str(ctx.exception))

    def test_category_specific_invalid_value_blocking(self):
        mock_category_metadata = {
            "status": "ok",
            "categoryId": "93427",
            "requiredAspects": ["Brand"],
            "fields": [
                {"name": "Brand", "required": True, "values": ["Nike", "Adidas", "Puma"]},
            ],
        }

        item = dict(self.valid_item)
        item["itemSpecifics"] = {"Brand": "InvalidBrandFake123"}
        item["brand"] = "InvalidBrandFake123"

        errors = validate_item_for_draft(item, category_metadata=mock_category_metadata)
        self.assertTrue(any(e["field"] == "Brand" and "allowed" in e["message"] for e in errors))

    def test_unsupported_hht_only_fields_not_added_as_columns(self):
        item = dict(self.valid_item)
        item["workflowStatus"] = "approved"
        item["provenance"] = {"title": {"source": "photo_evidence"}}
        item["confidence"] = "high"
        item["analysisJobId"] = "job-999"

        csv_out = export_seller_hub_draft_csv([item])
        reader = csv.reader(io.StringIO(csv_out))
        data_rows = [r for r in reader if r and not r[0].startswith("#INFO")]
        headers = data_rows[0]

        # HHT-only fields MUST NOT be columns in the exported CSV
        hht_internals = {"workflowStatus", "workflow_status", "provenance", "confidence", "analysisJobId", "pricingOptions", "titleCandidates"}
        for col in headers:
            self.assertNotIn(col, hht_internals)

    def test_csv_can_be_parsed_with_official_template_structure(self):
        csv_out = export_seller_hub_draft_csv([self.valid_item])
        parsed = parse_official_template(csv_out)
        self.assertTrue(len(parsed["info_rows"]) >= 1)
        self.assertTrue("Action" in parsed["action_header"])
        self.assertIn("Category ID", parsed["headers"])
        self.assertIn("Title", parsed["headers"])
        self.assertIn("Price", parsed["headers"])


class CategoryAspectsValidationTests(unittest.TestCase):
    def test_validate_listing_blocks_invalid_aspect_value(self):
        mock_aspects = {
            "status": "ok",
            "categoryId": "52365",
            "requiredAspects": ["Brand", "Size"],
            "fields": [
                {"name": "Brand", "required": True, "values": ["Gucci", "Prada", "Chanel"]},
                {"name": "Size", "required": True, "values": ["S", "M", "L"]},
            ],
        }

        with mock.patch("hht_app.ebay_taxonomy.category_aspects", return_value=mock_aspects):
            # Valid item passes
            valid_res = validate_listing({
                "cat": "52365",
                "brand": "Gucci",
                "size": "M",
                "itemSpecifics": {"Brand": "Gucci", "Size": "M"},
            })
            self.assertEqual(valid_res["status"], "valid")
            self.assertEqual(len(valid_res["missingRequiredAspects"]), 0)

            # Invalid brand value is blocked
            invalid_res = validate_listing({
                "cat": "52365",
                "brand": "NotAValidBrand",
                "size": "M",
                "itemSpecifics": {"Brand": "NotAValidBrand", "Size": "M"},
            })
            self.assertEqual(invalid_res["status"], "invalid_value")
            self.assertTrue(len(invalid_res["invalidValues"]) > 0)
            self.assertEqual(invalid_res["invalidValues"][0]["aspect"], "Brand")


class FeedApiOfflineTrackingTests(unittest.TestCase):
    def test_summarize_feed_result_with_csv_errors(self):
        from hht_app.ebay_feed import summarize_feed_result
        csv_content = (
            "Line Number,Action,Status,ErrorCode,ErrorMessage,CustomLabel,ItemID\n"
            '1,Draft,Failure,21919301,"Required item specific Brand is missing.",ITEM-001,\n'
            '2,Draft,Success,,,ITEM-002,123456\n'
        ).encode("utf-8")
        summary = summarize_feed_result(csv_content, "text/csv")
        self.assertEqual(summary["format"], "csv")
        self.assertEqual(summary["errorCount"], 1)
        self.assertEqual(summary["errors"][0]["code"], "21919301")
        self.assertEqual(summary["errors"][0]["customLabel"], "ITEM-001")

    def test_summarize_feed_result_with_xml_errors(self):
        from hht_app.ebay_feed import summarize_feed_result
        xml_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<BulkDataExchangeResponses>\n'
            '  <Responses>\n'
            '    <Response>\n'
            '      <Status>Failure</Status>\n'
            '      <Errors>\n'
            '        <Error>\n'
            '          <Code>BAF.Error.5</Code>\n'
            '          <SeverityCode>Error</SeverityCode>\n'
            '          <ShortMessage>Header is missing or invalid</ShortMessage>\n'
            '          <LongMessage>The file header row is invalid or missing required columns.</LongMessage>\n'
            '        </Error>\n'
            '      </Errors>\n'
            '    </Response>\n'
            '  </Responses>\n'
            '</BulkDataExchangeResponses>'
        ).encode("utf-8")
        summary = summarize_feed_result(xml_content, "application/xml")
        self.assertEqual(summary["format"], "xml")
        self.assertEqual(summary["errorCount"], 1)
        self.assertEqual(summary["errors"][0]["code"], "BAF.Error.5")
        self.assertIn("The file header row is invalid", summary["errors"][0]["message"])

    def test_summarize_feed_result_with_compressed_gzip_xml(self):
        import gzip
        from hht_app.ebay_feed import summarize_feed_result
        xml_text = (
            '<FeedProcessingResult>\n'
            '  <Error>\n'
            '    <Code>21916585</Code>\n'
            '    <Message>Duplicate custom label detected</Message>\n'
            '    <CustomLabel>HHT-12345</CustomLabel>\n'
            '  </Error>\n'
            '</FeedProcessingResult>'
        )
        compressed = gzip.compress(xml_text.encode("utf-8"))
        summary = summarize_feed_result(compressed, "application/gzip")
        self.assertEqual(summary["format"], "xml")
        self.assertEqual(summary["errorCount"], 1)
        self.assertEqual(summary["errors"][0]["code"], "21916585")
        self.assertEqual(summary["errors"][0]["customLabel"], "HHT-12345")

    def test_validate_upload_bytes_strictly_enforces_draft_and_blocks_live_actions(self):
        from hht_app.ebay_feed import _validate_upload_bytes, EbayFeedError
        # Valid draft CSV bytes pass
        valid_csv = (
            "#INFO,Version=0.0.2\n"
            "Action,Custom label (SKU),Category ID,Title,Price\n"
            "Draft,SKU-001,93427,Sample Nike Shoes,49.99\n"
        ).encode("utf-8")
        _validate_upload_bytes(valid_csv)

        # Live publishing action "Add" is strictly rejected
        invalid_csv = (
            "#INFO,Version=0.0.2\n"
            "Action,Custom label (SKU),Category ID,Title,Price\n"
            "Add,SKU-001,93427,Sample Nike Shoes,49.99\n"
        ).encode("utf-8")
        with self.assertRaises(EbayFeedError) as ctx:
            _validate_upload_bytes(invalid_csv)
        self.assertIn("unauthorized action 'Add'", str(ctx.exception))

        # Action "Publish" is strictly rejected
        publish_csv = (
            "Action,Custom label (SKU),Category ID,Title,Price\n"
            "Publish,SKU-001,93427,Sample Nike Shoes,49.99\n"
        ).encode("utf-8")
        with self.assertRaises(EbayFeedError) as ctx:
            _validate_upload_bytes(publish_csv)
        self.assertIn("unauthorized action 'Publish'", str(ctx.exception))

    def test_get_item_submission_status_distinguishes_in_process_from_terminal(self):
        import uuid
        seller_id = f"test-seller-{uuid.uuid4()}"
        batch = inventory_workspace.create_batch(seller_id)
        photos = inventory_workspace.register_photos(batch["id"], seller_id, [{"previewUrl": "https://example.com/p.jpg"}])
        item = inventory_workspace.create_item(batch["id"], seller_id, {
            "photoIds": [photos[0]["id"]],
            "listing": {"title": "Test Tracking Item", "price": "29.99", "cat": "93427", "sku": "SKU-TRACK-1"},
        })
        now = inventory_workspace._now_iso()
        sub_id = str(uuid.uuid4())
        task_id = f"task-async-{uuid.uuid4()}"
        with inventory_workspace.connect() as db:
            db.execute(
                "INSERT INTO inventory_submissions(id, item_id, seller_id, batch_id, feed_task_id, status, result_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'submitted', '{}', ?, ?)",
                (sub_id, item["id"], seller_id, batch["id"], task_id, now, now),
            )

        mock_in_process_details = {
            "taskId": task_id,
            "status": "IN_PROCESS",
            "uploadSummary": {"failureCount": 0, "successCount": 0},
        }
        with mock.patch("hht_app.inventory_workspace.get_feed_task_details", return_value=mock_in_process_details):
            status_res = inventory_workspace.get_item_submission_status(item["id"], seller_id)

        self.assertEqual(status_res["status"], "processing")
        self.assertEqual(status_res["itemWorkflowStatus"], "draft_submitted")

    def test_get_item_submission_status_handles_partial_failure(self):
        import uuid
        seller_id = f"test-seller-{uuid.uuid4()}"
        batch = inventory_workspace.create_batch(seller_id)
        photos = inventory_workspace.register_photos(batch["id"], seller_id, [{"previewUrl": "https://example.com/p.jpg"}])
        item = inventory_workspace.create_item(batch["id"], seller_id, {
            "photoIds": [photos[0]["id"]],
            "listing": {"title": "Test Partial Failure", "price": "29.99", "cat": "93427", "sku": "SKU-FAIL-1"},
        })
        now = inventory_workspace._now_iso()
        sub_id = str(uuid.uuid4())
        task_id = f"task-partial-{uuid.uuid4()}"
        with inventory_workspace.connect() as db:
            db.execute(
                "INSERT INTO inventory_submissions(id, item_id, seller_id, batch_id, feed_task_id, status, result_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'submitted', '{}', ?, ?)",
                (sub_id, item["id"], seller_id, batch["id"], task_id, now, now),
            )

        mock_partial_details = {
            "taskId": task_id,
            "status": "COMPLETED",
            "uploadSummary": {"failureCount": 1, "successCount": 2},
            "resultDetails": {
                "format": "csv",
                "errorCount": 1,
                "errors": [{"code": "21919301", "message": "Brand missing", "customLabel": "SKU-FAIL-1"}],
            },
        }
        with mock.patch("hht_app.inventory_workspace.get_feed_task_details", return_value=mock_partial_details):
            status_res = inventory_workspace.get_item_submission_status(item["id"], seller_id)

        self.assertEqual(status_res["status"], "completed_with_errors")
        self.assertEqual(status_res["itemWorkflowStatus"], "draft_failed")
