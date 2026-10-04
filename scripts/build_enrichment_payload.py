import json
from pathlib import Path
report = json.loads(Path('/home/ubuntu/hhtcatalog/live_pilot_report.json').read_text())
ids = []
for row in report.get('listings', []):
    listing_id = str(row.get('listingId') or '').strip()
    if listing_id and listing_id not in ids:
        ids.append(listing_id)
Path('/tmp/hht-enrichment-payload.json').write_text(json.dumps({'listingIds': ids}, indent=2) + '\n')
print(json.dumps({'count': len(ids), 'listingIds': ids}, indent=2))
