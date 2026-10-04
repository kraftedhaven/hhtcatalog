import json
from datetime import datetime, timezone
from pathlib import Path

DASHBOARD = json.loads(Path('/tmp/hht-dashboard.json').read_text())
PAYLOAD = json.loads(Path('/tmp/hht-recommendations.json').read_text())
RECS = PAYLOAD.get('result', []) if isinstance(PAYLOAD, dict) else []

seen = set()
unique = []
for rec in RECS:
    listing = rec.get('listing') or {}
    key = str(listing.get('listingId') or listing.get('sku') or listing.get('offerId') or listing.get('title') or rec.get('recommendationId'))
    if key in seen:
        continue
    seen.add(key)
    unique.append(rec)

def text(rec):
    listing = rec.get('listing') or {}
    return ' '.join(str(listing.get(k) or '') for k in ('title','brand','model','type','style','theme','mat')).lower()

def is_handbag(rec):
    listing = rec.get('listing') or {}
    return str(listing.get('cat') or '') in {'169291','169284'} or any(x in text(rec) for x in ('handbag','purse','clutch','crossbody','tote','backpack'))

def is_shoe_accessory(rec):
    listing = rec.get('listing') or {}
    return str(listing.get('cat') or '') in {'93427'} or any(x in text(rec) for x in ('shoe','sneaker','boot','loafer','sandal','belt','watch','scarf','jewelry','jewellery','accessory'))

def is_clothing(rec):
    listing = rec.get('listing') or {}
    return str(listing.get('cat') or '') in {'15724','63861','63867','11484','57988','63866','185100','15687','11483','57990','155183'} or any(x in text(rec) for x in ('shirt','t-shirt','dress','jacket','coat','jeans','pants','sweater','hoodie','skirt','blouse','top','clothing'))

def missing_or_weak(rec):
    listing = rec.get('listing') or {}
    title = str(listing.get('title') or '')
    findings = rec.get('findings') or []
    return len(title) < 45 or any(f.get('field') in {'item_specifics','brand','model','material','cat','price','taxonomy'} for f in findings if isinstance(f, dict)) or rec.get('risk') == 'high'

def take(predicate, count, used):
    result = []
    for rec in unique:
        key = str((rec.get('listing') or {}).get('listingId') or (rec.get('listing') or {}).get('sku') or (rec.get('listing') or {}).get('offerId') or (rec.get('listing') or {}).get('title') or rec.get('recommendationId'))
        if key in used or not predicate(rec):
            continue
        used.add(key)
        result.append(rec)
        if len(result) == count:
            break
    return result

used = set()
selected = []
selected.extend(("handbag_or_designer", r) for r in take(is_handbag, 5, used))
selected.extend(("clothing", r) for r in take(is_clothing, 5, used))
selected.extend(("shoes_or_accessories", r) for r in take(is_shoe_accessory, 5, used))
selected.extend(("missing_specifics_or_weak_title", r) for r in take(missing_or_weak, 5, used))

rows = []
for bucket, rec in selected:
    listing = rec.get('listing') or {}
    evidence = {e.get('field'): e for e in (rec.get('evidence') or []) if isinstance(e, dict)}
    pricing = rec.get('soldPricing') or {}
    demand = rec.get('demand') or {}
    rows.append({
        'bucket': bucket,
        'recommendationId': rec.get('recommendationId'),
        'listingId': listing.get('listingId') or '',
        'sku': listing.get('sku') or '',
        'offerId': listing.get('offerId') or '',
        'currentTitle': listing.get('title') or '',
        'proposedTitle': (rec.get('proposed') or {}).get('title') or '',
        'brandConfidence': (evidence.get('brand') or {}).get('confidence', 'not_available'),
        'modelConfidence': (evidence.get('model') or {}).get('confidence', 'not_available'),
        'materialConfidence': (evidence.get('material') or {}).get('confidence', 'not_available'),
        'countryOfManufactureConfidence': (evidence.get('madeIn') or {}).get('confidence', 'not_available'),
        'categoryValidation': (rec.get('taxonomy') or {}).get('status', 'not_available'),
        'currentPrice': listing.get('price'),
        'recommendedPrice': pricing.get('recommendedPrice'),
        'pricingSource': pricing.get('pricingSource', 'not_available'),
        'demandScore': demand.get('score'),
        'demandConfidence': demand.get('confidence', 'not_available'),
        'risk': rec.get('risk'),
        'recommendationStatus': rec.get('status'),
        'notes': 'Read-only pilot selection. No approval, apply, update, or publish request was made.'
    })

report = {
    'generatedAt': datetime.now(timezone.utc).isoformat(),
    'mode': 'read_only',
    'source': 'https://hht.ebbiehq.me/api/commerce/recommendations?status=Pending',
    'dashboard': DASHBOARD.get('result', {}),
    'sourceRecommendationCount': len(RECS),
    'uniqueCandidateCount': len(unique),
    'requestedCount': 20,
    'selectedCount': len(rows),
    'selectionShortfall': 20 - len(rows),
    'selectionShortfallReason': 'The live queue did not contain enough unique records in every requested bucket.' if len(rows) < 20 else '',
    'listings': rows,
    'warnings': [
        'The queue was regenerated from the deployed backend and now has one pending recommendation per stored listing.',
        'Nine stored rows are inventory-only records without an eBay listing ID; 508 rows have eBay listing IDs.',
        'Many active Trading API records do not include category or item-specific evidence, so they remain high-risk and are not approval candidates.',
        'No eBay changes were approved, applied, or published during this report.',
        'Pricing returns a numeric seller-price fallback when no sold or active comparable data is available; that fallback is not sold pricing.'
    ]
}
Path('/home/ubuntu/hhtcatalog/live_pilot_report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
lines = ['# HHT Live 20-Listing Pilot Report', '', f"Generated: {report['generatedAt']}", '', f"Selected: **{len(rows)} / 20**", f"Unique live candidates: **{len(unique)}**", '', '## Safety', '', 'This was a read-only pilot selection. No approval, apply, eBay update, or publish request was made.', '', '## Results', '', '| Bucket | Listing ID | SKU | Current title | Proposed title | Current price | Pricing source | Demand | Risk | Status |', '|---|---|---|---|---|---:|---|---:|---|---|']
for r in rows:
    def cell(v): return str(v if v not in (None, '') else 'Not available').replace('|', '\\|').replace('\n', ' ')
    lines.append('| ' + ' | '.join(cell(r[k]) for k in ('bucket','listingId','sku','currentTitle','proposedTitle','currentPrice','pricingSource','demandScore','risk','recommendationStatus')) + ' |')
lines += ['', '## Warnings', ''] + [f'- {w}' for w in report['warnings']]
Path('/home/ubuntu/hhtcatalog/live_pilot_report.md').write_text('\n'.join(lines) + '\n')
print(json.dumps({'selectedCount': len(rows), 'selectionShortfall': report['selectionShortfall'], 'report': '/home/ubuntu/hhtcatalog/live_pilot_report.md'}, indent=2))
