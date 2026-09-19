# Catalog Optimization Rollout

## Safety contract

The system stores attribute evidence, taxonomy results, pricing signals, demand proxies, and seller-recovery indicators as decision metadata. These fields are not sent to eBay. Every eBay mutation remains a separate approval followed by an explicit apply action; live publishing still requires its existing confirmation gate.

## Normalized attribute/evidence schema

Each supported attribute may have a value plus `confidence`, `source`, and `evidence`. Supported attributes are brand, model, material, madeIn, style, theme, vintage, size, color, and type. Values marked Not visible, unknown, or N/A are not treated as confirmed facts.

## Pricing and demand

The active Browse estimate remains labeled active-listing estimate. Sold pricing is intentionally disabled unless `SOLD_COMPS_API_URL` is configured with an approved provider. The demand score is a low-confidence proxy from imported quantity and quantitySold fields; it is not presented as eBay-wide sell-through.

## Taxonomy

Set `EBAY_TAXONOMY_ENABLED=true` only after the production eBay application has the required Taxonomy permission. Taxonomy results are advisory in audits and must be reviewed by the seller. The final eBay draft/update validators remain the hard gate.

## NVIDIA

Set `NVIDIA_NIM_BASE_URL`, `NVIDIA_NIM_API_KEY`, and `NVIDIA_CATEGORY_MODEL` to enable the optional adapter. When absent or unavailable, the system falls back to hosted vision, CPU/mock behavior, or manual seller entry. NVIDIA output is never an automatic eBay mutation.

## Pilot

1. Import 10–20 representative listings, including one high-value designer item, one category with missing specifics, one ordinary active listing, and one item with a known model.
2. Run audit and inspect evidence, taxonomy status, title candidate, pricing source, demand confidence, and risk.
3. Approve recommendations without applying them. Confirm the approval record is created and no eBay call occurs.
4. Apply only two low-risk title/specifics changes after seller review.
5. Compare the resulting eBay records and document false positives, missing attributes, and title quality.
6. Expand in batches of 50–100 only after the pilot passes.

## Cloudflare

Deploy `cloudflare/worker.js` only after replacing the broad `hht.ebbiehq.me/*` OAuth Worker route. The template forwards normal app traffic to Heroku, never caches OAuth endpoints or non-GET requests, and caches only the shell and health response for 30 seconds. Keep the existing Worker script but narrow its route or replace it with this proxy; do not cache tokens, callbacks, analysis results, or Commerce Agent responses.
