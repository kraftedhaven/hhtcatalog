import os
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from hht_app import commerce_agent

fd, path = tempfile.mkstemp(suffix=".sqlite3")
os.close(fd)
os.environ["COMMERCE_AGENT_DB"] = path
commerce_agent.init_db()
with commerce_agent.connect() as db:
    for i in range(20):
        db.execute("INSERT INTO listings(listing_id,offer_id,sku,marketplace,data_json,imported_at) VALUES(?,?,?,?,?,?)", (f"L{i}", f"O{i}", f"SKU{i}", "EBAY_US", commerce_agent._json({"listingId": f"L{i}", "offerId": f"O{i}", "sku": f"SKU{i}", "title": "Brown Signature Handbag", "brand": "Coach", "type": "Handbag", "price": 99.0, "cat": "169291", "pic": "https://example.com/item.jpg"}), commerce_agent.utc_now()))
first = commerce_agent.audit_all()
second = commerce_agent.audit_all()
recs = commerce_agent.recommendations()
assert first["count"] == 20 and second["count"] == 20
assert len(recs) == 20
assert all(r["proposed"].get("title") for r in recs)
assert all(r["listing"].get("attributeEvidence") is not None for r in recs)
approved = commerce_agent.approve_recommendation(recs[0]["recommendationId"], recs[0]["proposed"])
assert approved["status"] == "Approved"
print({"pilotListings": 20, "recommendations": len(recs), "deduplicated": True, "evidence": True, "approvalOnly": True})
try:
    os.unlink(path)
except FileNotFoundError:
    pass
