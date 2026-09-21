# HHT Commerce Agent — Production Reconciliation and Enrichment Report

**Date:** 2026-09-21  
**Deployment:** `da4fc06` — *Exclude stale local records from active review queue*  
**Mode:** Recommendation-only; no eBay listing update, offer publication, or price mutation was performed.

## Completed production work

The Copilot integration was audited against the current `main` branch. The earlier merge-marker regression had already been repaired before this pass; the current branch contains no unresolved Python merge markers. The application was then hardened around the active-list importer and approval queue.

The active eBay importer now parses the nested `PrimaryCategory` structure when eBay supplies it and explicitly requests the supported `ReturnAll` detail level. A rejected experiment using multiple `OutputSelector` paths was removed immediately; it made no changes to eBay or the local catalog. The stable active-list request is deployed.

The importer also now reconciles the local catalog after a completed active refresh. Records returned by the latest active-list import remain active; records not returned are retained for history but marked inactive, and their pending recommendations are removed from the live approval queue. Existing `GetItem` enrichment evidence and category data are retained across later summary imports.

The dashboard now distinguishes between a category that needs seller confirmation and a Taxonomy API outage. A missing category is shown as a confirmation task, not as an eBay validation failure.

## Live verification

| Verification | Result |
|---|---:|
| Public health endpoint | Healthy |
| Active listings returned by eBay | 504 |
| Local historical records retained | 520 |
| Locally stale records marked inactive in the final refresh | 9 |
| Fresh recommendations created for confirmed active records | 504 |
| Taxonomy API outage failures | 0 |
| Read-only `GetItem` pilot records enriched | 19 of 19 |
| Category confirmations needed before pilot enrichment | 504 |
| Category confirmations needed after pilot enrichment | 485 |

The 19-record pilot confirmed that `GetItem` returns the authoritative eBay category and category-specific fields, including details such as brand, model, material, country of origin, department, size, style, theme, and condition-specific information when those fields exist on the listing. The enrichment job wrote only to the HHT/Supabase catalog record and then regenerated recommendations; it did not revise, publish, or otherwise change an eBay listing.

## Validation

| Check | Result |
|---|---|
| Python regression suite | 139 tests passed |
| Frontend production build | Passed |
| Frontend listing-model tests | 6 passed |
| Python syntax compilation | Passed |
| Public health check after deployment | Passed |

## Operational workflow

1. Open the **Commerce Agent** tab and press **Analyze Active Listings** when you need a refreshed active catalog. This action is read-only.
2. Use the filters to make a 10–20 item pilot set, then select **Enrich selected (read-only)**. This retrieves official `GetItem` details for that bounded group and fills category/item-specific evidence locally.
3. Review the category recommendation, live Taxonomy message, evidence table, title candidate, and pricing source. The listing editor’s category search/dropdown and dynamic eBay fields are available for seller corrections.
4. Use **Approve only** for evidence-backed, low-risk changes. Approval remains local; it sends nothing to eBay.
5. Use **Apply approved change** only after review. That is the separate action that can call the eBay offer-update flow. Publishing remains separately confirmed.

## Current limitation and next scaling step

The bulk `GetMyeBaySelling` response returned the 504 active listing identities but did not provide `PrimaryCategory` for most records in this account, even with `DetailLevel=ReturnAll`. The reliable category source is the read-only `GetItem` enrichment call. The UI intentionally limits a pilot to 20 records so the seller can review evidence before applying anything.

For full-catalog enrichment, scale the existing worker process and process bounded read-only chunks of 20 listing IDs with rate-limit backoff and resume checkpoints. Do not convert this into automatic eBay updates: the approval-only safeguard should remain in place until pilot accuracy has been reviewed.

## eBay documentation consulted

- [GetMyeBaySelling reference](https://developer.ebay.com/devzone/xml/docs/reference/ebay/getmyebayselling.html)
- [Trading API request-field and OutputSelector guidance](https://developer.ebay.com/api-docs/user-guides/static/make-a-call/tapi-input-data.html)
- [Trading API field index](https://developer.ebay.com/devzone/xml/docs/reference/ebay/fieldindex.html)
