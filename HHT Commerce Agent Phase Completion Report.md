# HHT Commerce Agent Phase Completion Report

**Date:** 2026-09-21  
**Repository:** `kraftedhaven/hhtcatalog`  
**Release commit:** `892889c` — `Complete Commerce Agent review controls and rollback safety`

## Executive status

The production implementation plan is complete for the code-controlled MVP phases. The application now supports active-listing reconciliation, resumable read-only enrichment, evidence-backed recommendations, eBay Taxonomy validation, title optimization, conservative pricing logic, demand proxies, seller-recovery indicators, NVIDIA as an optional provider, durable jobs, paginated review, approval-only eBay writes, audit history, and safe rollback eligibility.

The only external dependency that cannot be completed honestly from code alone is **verified sold-comparable pricing**. eBay Browse returns active asking prices, not historical sold prices, and eBay Marketplace Insights access is restricted. The application therefore labels active, AI, seller-fallback, and sold sources separately and refuses to claim active prices are sold prices.

## Important remaining dependency

The only phase that cannot be completed without an external data source is **verified sold-comparable pricing**. The system correctly refuses to treat active eBay asking prices as sold prices. You still need either:

- An approved sold-comps API configured through Heroku variables, or
- A curated sold-comparables CSV.

Until then, pricing is clearly labeled as active comparable, AI estimate, seller fallback, or sold data when genuinely available.

The complete status, first-run procedure, limitations, routes, testing results, and remaining manual setup are documented here:

- [`HHT Commerce Agent Phase Completion Report.md`](HHT%20Commerce%20Agent%20Phase%20Completion%20Report.md)
- [`DEPLOYMENT.md`](DEPLOYMENT.md)
- [`ENRICHMENT_PIPELINE.md`](ENRICHMENT_PIPELINE.md)

## Completed phases

| Plan phase | Status | Evidence |
|---|---:|---|
| Active catalog import and reconciliation | Complete | Trading API pagination, deduplication, stale-record handling, active-only audit scope, category parsing repair |
| Normalized evidence and provenance | Complete | Evidence records retain source and confidence; official GetItem details are preserved |
| eBay Taxonomy validation | Complete | Live category search, category-specific aspects, advisory validation, cached aspect rules |
| Title optimization | Complete | Deterministic, bounded title candidates with no-op prevention and 80-character enforcement |
| Sold/active/AI pricing distinction | Complete with external sold-source dependency | Numeric fallback and source labels are implemented; verified sold data still requires a configured provider or CSV |
| Demand scoring and recovery indicators | Complete within available data | Demand proxy and operational recovery metrics are exposed; unavailable seller-performance metrics are not fabricated |
| NVIDIA classification | Complete as optional integration | NVIDIA NIM is in the hosted provider chain when configured; manual/CPU fallback remains available |
| Durable worker and resumable enrichment | Complete | Checkpointed full-catalog enrichment, bounded chunks, retries, stale-claim recovery, Heroku worker manifest |
| Paginated review experience | Complete | 25-item approval pages, filters, pilot selection, enrichment controls, responsive review cards |
| Approval-only safety model | Complete | Recommendation mode remains default; high-risk items require explicit field-level review; approval is separate from apply |
| Explain/reject/skip/bulk approval | Complete in this release | New stored explanation endpoint, reject/skip decisions, and up-to-25-item low-risk bulk approval |
| Change history and rollback foundation | Complete in this release | History exposes old/new values and rollback eligibility; rollback verifies current eBay state before restoring prior values |
| Draft/CSV/category workflow | Complete | Inventory offers are clearly distinguished from Seller Hub drafts; Seller Hub feed export and live category/aspect controls are documented |
| Deployment and security hardening | Complete | Server-side secrets, redacted diagnostics, no automatic eBay mutation, build conflict checks, Supabase persistence, Cloudflare-safe routing guidance |

## New release details

The release adds the following backend routes:

| Route | Purpose |
|---|---|
| `GET /api/commerce/recommendations/<id>/explain` | Returns the stored rationale, evidence, current values, proposed values, taxonomy, pricing, and demand metadata without rerunning a model |
| `POST /api/commerce/recommendations/<id>/skip` | Records a deliberate skip decision without contacting eBay |
| `POST /api/commerce/recommendations/<id>/reject` | Records a deliberate rejection without contacting eBay |
| `POST /api/commerce/recommendations/bulk-approve` | Approves up to 25 selected low-risk recommendations without contacting eBay |
| `POST /api/commerce/actions/<id>/rollback` | Verifies that eBay still matches the applied values, then restores the stored prior values when safe |

The Commerce Agent UI now exposes **Explain**, **Skip**, **Reject**, **Approve selected (no eBay write)**, and **Rollback safely** where the server reports that rollback is eligible.

## Verification results

The complete backend suite passed: **147 tests, 0 failures**. Python compilation passed for the Flask app, worker, and all backend modules. The Svelte production build passed. The frontend eBay model tests passed: **6 tests, 0 failures**. Local API smoke tests returned `200` for the Commerce dashboard, `404` for an unknown explanation record, and `400` for an invalid empty bulk-approval request as expected. No unresolved Python merge markers or `git diff --check` errors remain in the committed release.

The release was pushed to `origin/main` at commit `892889c`. GitHub deployment records were available for the repository immediately after push; the Heroku release is triggered by the repository's existing deployment connection. The current working tree also contains pre-existing local pilot/report artifacts that were intentionally not included in this release commit.

## What remains manual

To enable verified sold pricing, configure one approved source through Heroku Config Vars: either a compatible `SOLD_COMPS_API_URL` plus `SOLD_COMPS_API_TOKEN`, or a curated `SOLD_COMPS_CSV`/path containing sold records with honest source metadata. Do not use eBay Browse results as sold data. If using eBay Marketplace Insights, the eBay developer account must first receive access; that permission cannot be granted by this repository.

The seller must still complete normal eBay production setup: confirm the production keyset, assigned OAuth scopes, refresh token, merchant location, payment, fulfillment, and return policies. The first safe operational sequence remains: refresh active listings, run read-only enrichment, run the audit, review the paginated queue, approve only evidence-backed low-risk fields, and apply changes one at a time. No recommendation or bulk approval sends a write to eBay; only the separate Apply action does.

## Known limitations

The application cannot infer authenticity, manufacturer country, model, material, or style as confirmed facts when the source is absent. It records unknown or uncertain attributes for seller review. eBay seller-performance metrics such as defect rate, late shipment rate, cases, impressions, clicks, and conversion remain unavailable unless supplied by a supported import/API source; the recovery dashboard therefore reports operational catalog indicators rather than claiming official seller standing.

Rollback is intentionally conservative. It requires an Applied action with stored old and new values, official listing and offer identifiers, and a live eBay readback that still matches the applied values. If another edit has occurred, rollback stops rather than overwriting the newer seller change.

## First-run operating checklist

1. Open the deployed HHT application and confirm `/health` reports the expected provider configuration.
2. Confirm eBay OAuth status and refresh the active listing catalog.
3. Run **Enrich active catalog (read-only)**; use **Resume failed enrichment** only for failed checkpoints.
4. Run the audit and review the approval queue page by page.
5. Use **Explain** for any recommendation whose evidence or price source is unclear.
6. Approve only low-risk, seller-confirmed field changes. Bulk approval is approval-only and does not contact eBay.
7. Use **Apply approved change** only when the exact field-level payload is accepted.
8. Confirm the result in Change History. Use **Rollback safely** only when the UI marks the action eligible and no later eBay edit has occurred.
9. Do not expand to the complete catalog until the 10–20 listing pilot has been reviewed and the sold-data source decision has been made.

## Final conclusion

The code and deployment foundations are ready for production use in **recommend-only mode**. The project is not blocked by Copilot, VS Code, Docker, Cloudflare, or NVIDIA. The remaining business-data decision is specifically the source of historical sold comparables. Until that source is supplied, the system is deliberately honest and safe rather than presenting unsupported sold prices.

See also: [`DEPLOYMENT.md`](DEPLOYMENT.md) and [`ENRICHMENT_PIPELINE.md`](ENRICHMENT_PIPELINE.md).
