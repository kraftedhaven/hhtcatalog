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
PRIMARY_VISION_PROVIDER=groq
GROQ_API_KEY
GROQ_MODEL=qwen/qwen3.6-27b
ZAI_API_KEY
ZAI_BASE_URL=https://api.z.ai/api/paas/v4/
ZAI_MODEL=glm-4.6v-flash
ANALYZE_DEADLINE_SECONDS=28
PROVIDER_REQUEST_TIMEOUT_SECONDS=18
EBAY_CLIENT_ID
EBAY_CLIENT_SECRET
EBAY_REDIRECT_URI
EBAY_RUNAME
EBAY_REFRESH_TOKEN
EBAY_AUTH_STATE
EBAY_USER_SCOPES
EBAY_MERCHANT_LOCATION_KEY
EBAY_PAYMENT_POLICY_ID
EBAY_FULFILLMENT_POLICY_ID
EBAY_RETURN_POLICY_ID
EBAY_CURRENCY=USD
EBAY_LISTING_DURATION=GTC
EBAY_ENVIRONMENT=production
EBAY_MARKETPLACE_ID=EBAY_US
EBAY_SITE_ID=0
OPENROUTER_API_KEY
OPENROUTER_MODEL
GEMINI_API_KEY
GEMINI_MODEL
DEMO_MODE=false
DATABASE_URL
```

`PRIMARY_VISION_PROVIDER=groq` calls only Groq and does not fan out to every configured provider. Z.AI can remain configured but unused until you want to test it again. `DEMO_MODE=false` is the production default.
When no provider is configured, `/analyze` returns an actionable error instead of fabricated listing data.
Official eBay Browse pricing is optional. When `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET` are present, `/analyze` uses generated item keywords to fetch active eBay listings and labels the result `active_listing_estimate`. These are active listings, not sold comps. Without Browse access, the app keeps the vision provider's `ai_estimate`.
Seller OAuth for future inventory/offer work uses `EBAY_REDIRECT_URI`, `EBAY_RUNAME`, `EBAY_REFRESH_TOKEN`, and optional `EBAY_AUTH_STATE`/`EBAY_USER_SCOPES`. `EBAY_REDIRECT_URI` is the public callback URL that eBay sends the browser back to. `EBAY_RUNAME` is the OAuth-enabled RuName from the eBay Developer portal, and it is the value sent to eBay as the OAuth `redirect_uri` parameter. Use `GET /api/ebay/oauth/start` to generate a consent URL and `GET` or `POST /api/ebay/oauth/callback` to exchange the returned code. The callback returns the refresh token once so it can be copied into `EBAY_REFRESH_TOKEN`; it does not call eBay publish endpoints.
Direct eBay draft creation uses `POST /api/ebay/drafts` after an item has been reviewed. It creates or replaces the Inventory item and creates an unpublished Inventory offer using `EBAY_MERCHANT_LOCATION_KEY`, `EBAY_PAYMENT_POLICY_ID`, `EBAY_FULFILLMENT_POLICY_ID`, and `EBAY_RETURN_POLICY_ID`. It intentionally does not call `/publish`, so the app cannot create a live listing from this endpoint.
`ANALYZE_DEADLINE_SECONDS` and `PROVIDER_REQUEST_TIMEOUT_SECONDS` keep the synchronous `/analyze` call below Heroku's normal 30-second router limit while giving Groq enough time for multi-photo vision requests. Phone images are resized server-side before they are sent to a hosted provider.

## Commerce Agent MVP

The Commerce Agent is available from the **Commerce Agent** tab. It uses official eBay Inventory API calls to import existing inventory items and offers, stores normalized records and recommendation history in PostgreSQL when `DATABASE_URL` is present, and keeps the operating mode at `recommend` by default. The UI requires an explicit user approval before an action is sent through the existing eBay offer update flow. Auto-Optimize and Autonomous modes are represented as future modes but are not activated by this MVP.

Set Heroku's `DATABASE_URL` Config Var to the Supabase PostgreSQL connection string. The application creates the Commerce Agent tables automatically on startup. If `DATABASE_URL` is absent, local development falls back to `COMMERCE_AGENT_DB=commerce_agent.sqlite3`; do not use that SQLite fallback for durable Heroku data because the Heroku dyno filesystem is ephemeral. No Heroku filesystem setting needs to be changed.

Commerce Agent routes:

- `GET /api/commerce/dashboard` — summary counts and current mode.
- `POST /api/commerce/import` — imports existing eBay inventory items/offers through official APIs.
- `POST /api/commerce/audit` — audits imported listings and stores structured recommendations.
- `GET /api/commerce/listings` and `GET /api/commerce/recommendations` — review data.
- `GET /api/commerce/recommendations/<id>` — detailed recommendation and rationale.
- `POST /api/commerce/recommendations/<id>/approve` — records explicit field-level approval.
- `POST /api/commerce/actions/<id>/apply` — applies only the approved fields through the existing update flow.
- `GET /api/commerce/history` — action/change history.
- `GET`/`PUT /api/commerce/settings` — safety settings; this MVP still enforces Recommend mode.

The seller OAuth scopes already used by the repository include `sell.inventory`, `sell.account`, and `sell.fulfillment`. Inventory import requires the seller's Inventory API permissions and a valid refresh token. Policy/location variables remain required by the existing update flow. The first audit sequence is: complete eBay OAuth setup, verify `GET /api/ebay/oauth/status`, open Commerce Agent, select **Analyze My Listings**, review each finding and proposed field, then select **Approve & Apply** only for changes you want sent to eBay.

Example commands:

```sh
heroku stack:set container -a hht-catalog-b34ed1b32417
heroku config:set PRIMARY_VISION_PROVIDER=groq GROQ_API_KEY=... GROQ_MODEL=qwen/qwen3.6-27b ANALYZE_DEADLINE_SECONDS=28 PROVIDER_REQUEST_TIMEOUT_SECONDS=18 EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=... EBAY_REDIRECT_URI=https://hht.ebbiehq.me/api/ebay/oauth/callback EBAY_RUNAME=... EBAY_AUTH_STATE=... EBAY_ENVIRONMENT=production EBAY_MARKETPLACE_ID=EBAY_US EBAY_SITE_ID=0 DEMO_MODE=false -a hht-catalog-b34ed1b32417
git push heroku main
```

## API

`POST /analyze` accepts `multipart/form-data` with one to five `file` fields.
Files must be JPEG, PNG, WebP, GIF, or HEIC and fit under `MAX_UPLOAD_MB`.
Optional form field `tryAlternate=1` attempts one alternate hosted provider once.

`GET /health` returns provider availability booleans and never returns secrets.

`GET /api/ebay/oauth/start` returns an eBay seller-consent URL.

`GET` or `POST /api/ebay/oauth/callback` exchanges an eBay authorization code for a refresh token during setup.

`GET /api/ebay/oauth/status` verifies that `EBAY_REFRESH_TOKEN` can mint a seller access token.

`POST /api/ebay/drafts` accepts a reviewed item and returns an unpublished eBay Inventory offer ID:

```json
{
  "item": {
    "sku": "LEVIS-123",
    "title": "Levi's Denim Jacket",
    "price": 24.99,
    "cat": "57988",
    "cid": "3000",
    "brand": "Levi's",
    "type": "Jacket",
    "pic": "https://example.com/photo.jpg"
  }
}
```

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
