# HHT Catalog Heroku Deployment

This repository is the canonical Heroku source tree for the merged HHT eBay Listing Builder.
The Flask app serves the built Svelte frontend and the same-origin API.

## Start Command

Heroku uses `heroku.yml` with the Dockerfile web process:

```sh
gunicorn --bind 0.0.0.0:${PORT:-8080} --workers ${GUNICORN_WORKERS:-2} --timeout ${GUNICORN_TIMEOUT:-120} app:app
```

The app binds to `0.0.0.0` and `PORT`.

## Required Config Vars

Set the hosted vision provider explicitly in Heroku Config Vars. Do not commit real values.

```text
PRIMARY_VISION_PROVIDER=zai
ZAI_API_KEY
ZAI_BASE_URL=https://api.z.ai/api/paas/v4/
ZAI_MODEL=glm-4.6v-flash
ANALYZE_DEADLINE_SECONDS=28
PROVIDER_REQUEST_TIMEOUT_SECONDS=18
EBAY_CLIENT_ID
EBAY_CLIENT_SECRET
EBAY_ENVIRONMENT=production
EBAY_MARKETPLACE_ID=EBAY_US
EBAY_SITE_ID=0
OPENROUTER_API_KEY
OPENROUTER_MODEL
GEMINI_API_KEY
GEMINI_MODEL
GROQ_API_KEY
GROQ_MODEL
DEMO_MODE=false
```

`PRIMARY_VISION_PROVIDER=zai` calls only Z.AI and does not fan out to every configured provider. `DEMO_MODE=false` is the production default.
When no provider is configured, `/analyze` returns an actionable error instead of fabricated listing data.
Official eBay Browse pricing is optional. When `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET` are present, `/analyze` uses generated item keywords to fetch active eBay listings and labels the result `active_listing_estimate`. These are active listings, not sold comps. Without Browse access, the app keeps the Z.AI `ai_estimate`.
`ANALYZE_DEADLINE_SECONDS` and `PROVIDER_REQUEST_TIMEOUT_SECONDS` keep the synchronous `/analyze` call below Heroku's normal 30-second router limit while giving Z.AI enough time for multi-photo vision requests. Z.AI images are resized server-side and a timeout is retried once with smaller images.

Example commands:

```sh
heroku stack:set container -a hht-catalog-b34ed1b32417
heroku config:set PRIMARY_VISION_PROVIDER=zai ZAI_API_KEY=... ZAI_BASE_URL=https://api.z.ai/api/paas/v4/ ZAI_MODEL=glm-4.6v-flash ANALYZE_DEADLINE_SECONDS=28 PROVIDER_REQUEST_TIMEOUT_SECONDS=18 EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=... EBAY_ENVIRONMENT=production EBAY_MARKETPLACE_ID=EBAY_US EBAY_SITE_ID=0 DEMO_MODE=false -a hht-catalog-b34ed1b32417
git push heroku main
```

## API

`POST /analyze` accepts `multipart/form-data` with one to five `file` fields.
Files must be JPEG, PNG, WebP, GIF, or HEIC and fit under `MAX_UPLOAD_MB`.

`GET /health` returns provider availability booleans and never returns secrets.

`POST /export/csv` accepts:

```json
{
  "items": [],
  "sellerDefaults": {}
}
```

It returns an eBay Seller Hub fixed-price CSV using the exact 35-column header.

## Migration Note

The old Flask demo pipeline, cross-listing CSV, and Azure-specific provider path were replaced by:

- `hht_app/providers.py` for hosted provider calls.
- `hht_app/schema.py` for normalization, seller safeguards, title limits, and CSV generation.
- `frontend/src/App.svelte` for the phone-first eBay queue workflow ported from `hhtmobile-main`.

Rollback point: branch `backup-pre-hhtmobile-merge-20260830`.
