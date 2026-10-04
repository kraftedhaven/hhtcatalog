import json
from pathlib import Path

recs = json.loads(Path('/tmp/hht-recommendations.json').read_text()).get('result', [])
payload = json.loads(Path('/tmp/hht-enrichment-payload.json').read_text())
ids = set(payload['listingIds'])
selected = []
for rec in recs:
    listing = rec.get('listing') or {}
    if str(listing.get('listingId') or '') in ids:
        selected.append(rec)
# Include the original inventory-only pilot record so the cohort remains 20.
for rec in recs:
    listing = rec.get('listing') or {}
    if not listing.get('listingId') and str(listing.get('sku') or '') == '15e099f2-cbee-4a8c-78a3-5b9a437ac84d':
        selected.insert(0, rec)
        break
selected = selected[:20]

def bucket(rec):
    listing = rec.get('listing') or {}
    text = ' '.join(str(listing.get(k) or '') for k in ('title','brand','model','type','style','theme','mat')).lower()
    if any(x in text for x in ('handbag','purse','clutch','crossbody','tote','backpack')): return 'handbag_or_designer'
    if any(x in text for x in ('shirt','t-shirt','dress','jacket','coat','jeans','pants','sweater','hoodie','skirt','blouse','top','clothing')): return 'clothing'
    return 'shoes_or_accessories'

def cell(v): return str(v if v not in (None, '') else 'Not available').replace('|','\\|').replace('\n',' ')
rows=[]
for rec in selected:
    listing=rec.get('listing') or {}; pricing=rec.get('soldPricing') or {}; demand=rec.get('demand') or {}; evidence={e.get('field'):e for e in (rec.get('evidence') or []) if isinstance(e,dict)}
    rows.append({'bucket':bucket(rec),'listingId':listing.get('listingId') or '', 'sku':listing.get('sku') or '', 'currentTitle':listing.get('title') or '', 'proposedTitle':(rec.get('proposed') or {}).get('title') or '', 'brandConfidence':(evidence.get('brand') or {}).get('confidence','not_available'), 'modelConfidence':(evidence.get('model') or {}).get('confidence','not_available'), 'materialConfidence':(evidence.get('material') or {}).get('confidence','not_available'), 'countryConfidence':(evidence.get('madeIn') or {}).get('confidence','not_available'), 'categoryValidation':(rec.get('taxonomy') or {}).get('status','not_available'), 'categoryId':listing.get('cat') or '', 'currentPrice':listing.get('price'), 'recommendedPrice':pricing.get('recommendedPrice'), 'pricingSource':pricing.get('pricingSource','not_available'), 'demandScore':demand.get('score'), 'demandConfidence':demand.get('confidence','not_available'), 'risk':rec.get('risk'), 'status':rec.get('status'), 'findings':len(rec.get('findings') or []), 'recommendationId':rec.get('recommendationId')})
report={'mode':'read_only_exact_enriched_cohort','selectedCount':len(rows),'enrichedListingCount':len([r for r in rows if r['listingId']]),'rows':rows,'warnings':['No eBay changes were approved, applied, updated, or published.','The inventory-only row has no listing ID and cannot be enriched through GetItem.','Taxonomy validation ran against the configured eBay category tree; valid means the category and required aspects passed, while needs_specifics or unavailable remains review-only.']}
Path('/home/ubuntu/enriched_pilot_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
lines=['# Exact Enriched 20-Listing Pilot Report','','Selected: **%d / 20**'%len(rows),'Enriched eBay listings: **%d**'%report['enrichedListingCount'],'','## Safety','','Read-only cohort evaluation. No eBay mutation was performed.','','| Listing ID | SKU | Category | Current title | Proposed title | Current price | Recommended price | Pricing source | Taxonomy | Risk | Status |','|---|---|---:|---|---|---:|---:|---|---|---|---|']
for r in rows: lines.append('| '+' | '.join(cell(r[k]) for k in ('listingId','sku','categoryId','currentTitle','proposedTitle','currentPrice','recommendedPrice','pricingSource','categoryValidation','risk','status'))+' |')
lines += ['', '## Warnings', ''] + ['- '+w for w in report['warnings']]
Path('/home/ubuntu/enriched_pilot_report.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({'selectedCount':len(rows),'enrichedListingCount':report['enrichedListingCount'],'report':'/home/ubuntu/enriched_pilot_report.md'},indent=2))
