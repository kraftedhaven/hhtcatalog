# Optional Resource Integrations

HHT keeps the normal production path unchanged: Groq handles normal analysis, NVIDIA handles heavy batches when configured, Supabase/PostgreSQL remains the application database, and eBay mutations remain seller-approved.

## IBM Cloud Object Storage

Set `PHOTO_STORAGE_PROVIDER=ibm_cos` only after the bucket is private and the service credentials are stored in Heroku Config Vars. Required variables are:

- `IBM_COS_ENDPOINT`
- `IBM_COS_BUCKET`
- `IBM_COS_REGION` (optional; defaults to `us-standard`)
- `IBM_COS_ACCESS_KEY_ID`
- `IBM_COS_SECRET_ACCESS_KEY`
- `PHOTO_URL_TTL_SECONDS` (optional; defaults to 3600)

The server stores an original and a compressed WebP derivative under a seller-owned prefix. Only the derivative’s time-limited signed URL is returned to HHT. The service-role or HMAC credentials never reach the browser. The image remains in storage until a later retention/cleanup policy is explicitly implemented.

Supabase Storage can be used instead by setting `PHOTO_STORAGE_PROVIDER=supabase`, `PHOTO_STORAGE_BUCKET`, `SUPABASE_URL`, and `SUPABASE_SERVICE_ROLE_KEY`. The service-role key is server-only.

## BreakGround Pro

The dashboard onboarding layer is controlled by `VITE_BREAKGROUND_ENABLED=true` at frontend build time. The current implementation is intentionally privacy-safe and provider-optional: it displays a short checklist and does not transmit photos, tokens, seller identity, or complete listing payloads. If the BreakGround SDK is later added, it must be loaded only behind this flag and restricted to an allowlist of non-sensitive events.

## NVIDIA Inception/Developer/Brev

The existing provider configuration remains authoritative:

- `NVIDIA_NIM_API_KEY`
- `NVIDIA_NIM_BASE_URL`
- `NVIDIA_CATEGORY_MODEL`
- `NVIDIA_ITEM_THRESHOLD` (optional; current default is 30)
- `NVIDIA_PHOTO_THRESHOLD` (optional; current default is 300)

Normal requests remain on Groq. Heavy batches are queued to the NVIDIA worker and normalized into the same listing schema. NVIDIA output is review-only and cannot publish or mutate eBay listings.

## Verification sequence

1. Check `/api/photos/storage` while signed in; it must report the intended provider and `configured: true` before uploading.
2. Upload a small test group from the Analyze staging screen.
3. Confirm that the returned `ebayUrl` values are HTTPS, time-limited, and associated with the seller-owned photo asset record.
4. Run one normal Groq analysis and one heavy NVIDIA worker analysis when the NVIDIA credentials/model are available.
5. Compare provider, model, latency, failure category, and canonical field completeness in the analysis-run audit data.
6. Keep `EBAY_MUTATIONS_ENABLED=false`; use only the existing five-item Seller Hub draft-feed pilot after seller review.
