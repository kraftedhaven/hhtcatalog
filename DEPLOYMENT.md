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
GROQ_FALLBACK_MODEL=qwen/qwen3.8-27b
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
NVIDIA_NIM_BASE_URL
NVIDIA_NIM_API_KEY
NVIDIA_CATEGORY_MODEL
DEMO_MODE=false
DATABASE_URL
```

`PRIMARY_VISION_PROVIDER=groq` calls only Groq and does not fan out to every configured provider. Groq model values are trimmed, and a 404/model-unavailable response is retried once with `GROQ_FALLBACK_MODEL`. The active hosted fallback order is `groq,openrouter,nvidia`; Gemini is not part of the active provider chain. Z.AI can remain configured but unused until you want to test it again. `DEMO_MODE=false` is the production default.
When no provider is configured, `/analyze` returns an actionable error instead of fabricated listing data.
Official eBay Browse pricing is optional. When `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET` are present, `/analyze` uses generated item keywords to fetch active eBay listings and labels the result `active_listing_estimate`. These are active listings, not sold comps. Without Browse access, the app keeps the vision provider's `ai_estimate`.
Seller OAuth for future inventory/offer work uses `EBAY_REDIRECT_URI`, `EBAY_RUNAME`, `EBAY_REFRESH_TOKEN`, and optional `EBAY_AUTH_STATE`/`EBAY_USER_SCOPES`. `EBAY_REDIRECT_URI` is the public callback URL that eBay sends the browser back to. `EBAY_RUNAME` is the OAuth-enabled RuName from the eBay Developer portal, and it is the value sent to eBay as the OAuth `redirect_uri` parameter. Use `GET /api/ebay/oauth/start` to generate a consent URL and `GET` or `POST /api/ebay/oauth/callback` to exchange the returned code. The callback returns the refresh token once so it can be copied into `EBAY_REFRESH_TOKEN`; it does not call eBay publish endpoints.
`POST /api/ebay/drafts` is named for legacy compatibility, but it creates an **unpublished Inventory API offer**, not a Seller Hub Draft. It creates or replaces the Inventory item and creates an unpublished offer using `EBAY_MERCHANT_LOCATION_KEY`, `EBAY_PAYMENT_POLICY_ID`, `EBAY_FULFILLMENT_POLICY_ID`, and `EBAY_RETURN_POLICY_ID`. It intentionally does not call `/publish`, so the app cannot create a live listing from this endpoint. Unpublished Inventory API offers are verified and published from HHT by offer ID; they do not appear in Seller Hub’s **Drafts** folder.
`ANALYZE_DEADLINE_SECONDS` and `PROVIDER_REQUEST_TIMEOUT_SECONDS` keep the synchronous `/analyze` call below Heroku's normal 30-second router limit while giving Groq enough time for multi-photo vision requests. Phone images are resized server-side before they are sent to a hosted provider.

## Commerce Agent MVP

The Commerce Agent is available from the **Commerce Agent** tab. It uses official eBay Inventory API calls to import existing inventory items and offers, stores normalized records and recommendation history in PostgreSQL when `DATABASE_URL` is present, and keeps the operating mode at `recommend` by default. The UI requires an explicit user approval before an action is sent through the existing eBay offer update flow. Auto-Optimize and Autonomous modes are represented as future modes but are not activated by this MVP.

Set Heroku's `DATABASE_URL` Config Var to the Supabase PostgreSQL connection string. The application creates the Commerce Agent tables automatically on startup. If `DATABASE_URL` is absent, local development falls back to `COMMERCE_AGENT_DB=commerce_agent.sqlite3`; do not use that SQLite fallback for durable Heroku data because the Heroku dyno filesystem is ephemeral. No Heroku filesystem setting needs to be changed.

Commerce Agent routes:

- `GET /api/commerce/dashboard` — summary counts and current mode.
- `POST /api/commerce/import` — imports existing eBay inventory items/offers through official APIs.
- `POST /api/commerce/import-active/start` — queues a paginated active-listing import and returns a job ID.
- `POST /api/commerce/enrich/start` — queues bounded, read-only GetItem enrichment for 1–20 selected active listing IDs.
- `POST /api/commerce/audit` — audits imported listings and stores structured recommendations.
- `POST /api/commerce/audit/start` — queues a read-only audit and returns a job ID.
- `GET /api/commerce/jobs/<id>` — returns queued, running, completed, or failed job status and result.
- `GET /api/commerce/listings` and `GET /api/commerce/recommendations` — review data.
- `GET /api/commerce/recommendations/<id>` — detailed recommendation and rationale.
- `POST /api/commerce/recommendations/<id>/approve` — records explicit field-level approval.
- `POST /api/commerce/actions/<id>/apply` — applies only the approved fields through the existing update flow.
- `GET /api/commerce/history` — action/change history.
- `GET`/`PUT /api/commerce/settings` — safety settings; this MVP still enforces Recommend mode.

The seller OAuth scopes used by the repository default to the Trading API base scope `https://api.ebay.com/oauth/api_scope` plus `https://api.ebay.com/oauth/api_scope/sell.inventory`. The exact scopes must be assigned to the Production keyset in eBay's Application Keys/User Tokens page; if `EBAY_USER_SCOPES` is set, it overrides the defaults and must contain only scopes assigned to that keyset. Inventory import requires the seller's Inventory API permissions and a valid refresh token. Policy/location variables remain required by the existing update flow. The first audit sequence is: complete eBay OAuth setup, verify `GET /api/ebay/oauth/status`, open Commerce Agent, select **Analyze Active Listings**, review each finding and proposed field, choose **Approve only** for acceptable recommendations, and use **Apply approved change** only when you are ready to send the approved fields to eBay.

The **Analyze Active Listings** action uses the Trading API `GetMyeBaySelling` with pagination and is separate from **Import API Inventory**, which only covers Inventory API records. Active-listing import may require reauthorizing the seller token with the appropriate Trading API user scope. `POST /api/photo-quality` provides deterministic resolution, brightness, and sharpness checks before export. Active pricing lookups use a bounded in-process TTL cache controlled by `PRICING_CACHE_TTL_SECONDS`; active asking prices are not sold prices.

NVIDIA GPU category classification is optional and reserved for an approved NVIDIA NIM/OpenAI-compatible endpoint configured with `NVIDIA_NIM_BASE_URL`, `NVIDIA_NIM_API_KEY`, and `NVIDIA_CATEGORY_MODEL`. The app remains usable without those variables. Bulk active import is paginated and bounded; future GPU image workers must use bounded concurrency and must not issue unbounded eBay requests.

### Background worker

The web process can claim a read-only catalog job immediately, so small pilots work without a separate process. For durable queue processing at catalog scale, `heroku.yml` also defines a `worker` process using the same container image. Scale it only after confirming the available Heroku plan capacity:

```sh
heroku ps:scale worker=1 -a hht-catalog-b34ed1b32417
```

The worker can run **only** active imports, GetItem enrichment, and recommendation audits. It never approves, updates, publishes, or otherwise mutates an eBay listing. The Docker Compose stack uses the same shared catalog storage for the API and worker, with no Azure, Appwrite, DigitalOcean, or Gemini configuration.

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

It returns a legacy/File Exchange-style 35-column CSV. Use it only with a matching legacy listing template; it is not the same as the current Seller Hub **Create new drafts** template.

### Seller Hub Draft CSV workflow

`POST /export/draft-csv` returns the current compact Seller Hub draft-template shape. In the application, use **Download Seller Hub Draft CSV** and upload it through **Seller Hub → Reports → Uploads → Upload template**, after downloading the matching **Create new drafts** template for your selected category at least once.

The generated file uses only the 11 columns eBay documents for the Drafts feed. Its rows set `Action` to `Draft`, preserve the selected numeric Category ID, use `NEW` or `USED` rather than API condition IDs, leave photo URL blank unless it is a public `http(s)` URL, and do not include a fake image URL placeholder. After upload, open the upload result file. Accepted rows appear in **Seller Hub → Listings → Drafts**; eBay notes that the feed can take up to roughly 15 minutes and that the result file is the authoritative record of rejected rows.

### Category and field workflow

The editor now provides a live **Find eBay categories** picker. Search specific terms such as `kids baseball hat`, `women's belt`, or `Coach crossbody`. The Taxonomy API returns eBay leaf categories; selecting one retains its actual ID rather than limiting the record to the original clothing-and-bag quick menu. It then loads eBay’s current required and recommended category-specific item fields under **Current eBay fields**. Enter only seller-confirmed facts; those field values are sent as Inventory API product aspects.

## Migration Note

The old Flask demo pipeline, cross-listing CSV, and Azure-specific provider path were replaced by:

- `hht_app/providers.py` for hosted provider calls.
- `hht_app/schema.py` for normalization, seller safeguards, title limits, and CSV generation.
- `frontend/src/App.svelte` for the phone-first eBay queue workflow ported from `hhtmobile-main`.

Rollback point: branch `backup-pre-hhtmobile-merge-20260830`.
