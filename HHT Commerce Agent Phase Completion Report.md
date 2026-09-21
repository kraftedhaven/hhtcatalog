# HHT Commerce Agent Phase Completion Report

**Date:** 2026-09-21  
**Repository:** `kraftedhaven/hhtcatalog`  
**Latest deployed commit:** `9515c1e` — latest merged workflow release
**Heroku deployment:** successful, deployment `6576036232`

## Executive status

The production implementation plan is complete for the code-controlled MVP phases. The application supports active-listing reconciliation, resumable read-only enrichment, evidence-backed recommendations, eBay Taxonomy validation, title optimization, conservative pricing logic, demand proxies, seller-recovery indicators, NVIDIA as an optional provider, durable jobs, paginated review, approval-only eBay writes, audit history, safe rollback eligibility, guided eBay reconnect, and Seller Hub draft-feed submission.

The public deployment is active at [hht.ebbiehq.me](https://hht.ebbiehq.me). The latest Heroku deployment completed successfully. Live verification confirms:

- `/health` returns `status: ok` with demo mode disabled.
- eBay OAuth status returns `configured: true`.
- Groq, OpenRouter, NVIDIA, and Z.ai provider configuration is available.
- The deployed frontend contains the **Reconnect eBay** and **Send Seller Hub Drafts to eBay** workflows.
- The Seller Hub upload path uses the production-only `FX_LISTING` feed and does not publish live listings.
- The exposed direct API offer publish route is removed and returns `404`; the legacy unpublished Inventory API offer endpoint does not publish.

The only external dependency that cannot be completed honestly from code alone is **verified sold-comparable pricing**. eBay Browse returns active asking prices, not historical sold prices, and eBay Marketplace Insights access is restricted. The application therefore labels active, AI, seller-fallback, and sold sources separately and refuses to claim active prices are sold prices.

## Important remaining dependency

The only phase that cannot be completed without an external data source is **verified sold-comparable pricing**. The system correctly refuses to treat active eBay asking prices as sold prices. You still need either:

- An approved sold-comps API configured through Heroku variables, or
- A curated sold-comparables CSV.

Until then, pricing is clearly labeled as active comparable, AI estimate, seller fallback, or sold data when genuinely available.

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
| Explain/reject/skip/bulk approval | Complete | Stored explanations, reject/skip decisions, and up-to-25-item low-risk bulk approval |
| Change history and rollback foundation | Complete | History exposes old/new values and rollback eligibility; rollback verifies current eBay state before restoring prior values |
| Guided eBay reconnect | Complete and deployed | Settings-tab reconnect button starts OAuth; callback provides an HTML copy page for `EBAY_REFRESH_TOKEN` and does not publish |
| Seller Hub FX_LISTING feed workflow | Complete and deployed | Queue submission calls `/api/ebay/draft-feed`, creates an `FX_LISTING` feed task, and directs the seller to Seller Hub Reports for processing results |
| Direct API offer verification/publishing exposure | Removed | Direct `/api/ebay/offers/<id>/publish` exposure returns `404`; legacy Inventory API offer creation remains unpublished and does not call publish |
| Draft/CSV/category workflow | Complete | Seller Hub draft CSV export, FX_LISTING upload, legacy File Exchange export distinction, and live category/aspect controls are documented |
| Deployment and security hardening | Complete | Server-side secrets, redacted diagnostics, no automatic eBay mutation, build conflict checks, Supabase persistence, Cloudflare-safe routing guidance |

## Reconnect and activation workflow

The Settings tab now includes **Reconnect eBay**. Selecting it starts the configured OAuth authorization flow. After the seller approves access, the callback displays an HTML completion page containing the refresh token once for copying into the Heroku Config Var `EBAY_REFRESH_TOKEN`. The callback does not call eBay publish endpoints. After saving the Config Var, restart the application and confirm `GET /api/ebay/oauth/status` returns `configured: true`.

Never paste the refresh token into the frontend, source repository, chat, or a report. The application continues to read secrets from Heroku Config Vars only.

## Seller Hub draft workflow

The Queue tab provides **Send Seller Hub Drafts to eBay**. This submits the reviewed queue through the eBay Sell Feed API as an `FX_LISTING` draft feed in production. It does not publish live listings. The returned task ID should be checked in Seller Hub Reports or through the feed-task status route.

The separate **Download Seller Hub Draft CSV** option remains available for manual upload through **Seller Hub → Reports → Uploads → Create new drafts**. The legacy File Exchange CSV is a separate format and should not be uploaded as a current Seller Hub Draft template. Local phone photos are not uploaded by the feed unless they are available as public HTTPS URLs; those photos must be added in eBay afterward.

The older `/api/ebay/drafts` route remains for compatibility and creates an unpublished Inventory API offer. It does not publish a live listing. Direct offer-publish exposure has been removed from the public application route surface.

## Review and safety controls

The Commerce Agent provides **Explain**, **Skip**, **Reject**, **Approve selected (no eBay write)**, and **Rollback safely** where the server reports that rollback is eligible. Approval and bulk approval only record seller decisions. Only the separate Apply action can send approved field changes to eBay, and rollback verifies that the live eBay values still match the prior applied action before restoring anything.

## Verification results

The current verification result is **145 backend tests passed, 0 failures, and 2 frontend subtests passed**. The production frontend build passed. Python compilation passed for the Flask app, worker, and backend modules. Live checks after deployment returned healthy application status and configured eBay OAuth status. The deployed JavaScript bundle contains `Reconnect eBay`, `Send Seller Hub Drafts to eBay`, `FX_LISTING`, and `EBAY_REFRESH_TOKEN` workflow text.

The latest deployed commit is `9515c1e`; Heroku deployment `6576036232` reported `success`. The local workspace still contains pre-existing pilot/report artifacts and unrelated documentation edits that were not used to determine production activation.

## What remains manual

To enable verified sold pricing, configure one approved source through Heroku Config Vars: either a compatible `SOLD_COMPS_API_URL` plus `SOLD_COMPS_API_TOKEN`, or a curated `SOLD_COMPS_CSV`/path containing sold records with honest source metadata. Do not use eBay Browse results as sold data. If using eBay Marketplace Insights, the eBay developer account must first receive access; that permission cannot be granted by this repository.

The seller must still confirm normal eBay production setup: the production keyset, assigned OAuth scopes, refresh token, merchant location, payment, fulfillment, and return policies. The safe operating sequence is: reconnect if needed, refresh active listings, run read-only enrichment, run the audit, review the paginated queue, approve only evidence-backed low-risk fields, and apply changes one at a time.

## Known limitations

The application cannot infer authenticity, manufacturer country, model, material, or style as confirmed facts when the source is absent. It records unknown or uncertain attributes for seller review. eBay seller-performance metrics such as defect rate, late shipment rate, cases, impressions, clicks, and conversion remain unavailable unless supplied by a supported import/API source; the recovery dashboard therefore reports operational catalog indicators rather than claiming official seller standing.

Rollback is intentionally conservative. It requires an Applied action with stored old and new values, official listing and offer identifiers, and a live eBay readback that still matches the applied values. If another edit has occurred, rollback stops rather than overwriting the newer seller change.

## First-run operating checklist

1. Open [hht.ebbiehq.me](https://hht.ebbiehq.me) and confirm `/health` reports the expected provider configuration.
2. Open **Settings → Reconnect eBay** only when OAuth permissions or the refresh token need renewal.
3. If reconnecting, copy the callback page's refresh token into Heroku Config Var `EBAY_REFRESH_TOKEN`, restart the app, and verify OAuth status.
4. Refresh the active listing catalog.
5. Run **Enrich active catalog (read-only)**; use **Resume failed enrichment** only for failed checkpoints.
6. Run the audit and review the approval queue page by page.
7. Use **Explain** for any recommendation whose evidence or price source is unclear.
8. Approve only low-risk, seller-confirmed field changes. Bulk approval is approval-only and does not contact eBay.
9. Use **Apply approved change** only when the exact field-level payload is accepted.
10. For drafts, use **Send Seller Hub Drafts to eBay** and then check the returned feed task in Seller Hub Reports. Do not expect live listings.
11. Confirm changes in Change History. Use **Rollback safely** only when the UI marks the action eligible and no later eBay edit has occurred.
12. Do not expand to the complete catalog until the 10–20 listing pilot has been reviewed and the sold-data source decision has been made.

## Final conclusion

The current code and deployment are active and ready for production use in **recommend-only mode**, with Seller Hub draft-feed submission available and direct API publishing exposure removed. The project is not blocked by Copilot, VS Code, Docker, Cloudflare, or NVIDIA. The remaining business-data decision is specifically the source of historical sold comparables. Until that source is supplied, the system is deliberately honest and safe rather than presenting unsupported sold prices.

See also: [`DEPLOYMENT.md`](DEPLOYMENT.md), [`ENRICHMENT_PIPELINE.md`](ENRICHMENT_PIPELINE.md), and [`EBAY_LISTING_WORKFLOWS.md`](EBAY_LISTING_WORKFLOWS.md).
