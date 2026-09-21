# HHT Commerce Agent — Production Readiness Report

**Status:** Deployed and verified on the HHT custom domain as of 2026-09-21.

## Executive summary

The HHT Commerce Agent is restored, deployed, and operating in **recommend-only mode**. The live application is connected to Supabase persistence and has imported **517 catalog records**. Its active provider health indicates Groq, OpenRouter, NVIDIA NIM, and Z.AI availability. No eBay listing has been approved, updated, or published as part of this recovery and hardening work.

The production failure was traced to unresolved Git merge markers committed into Python modules. Those markers prevented Gunicorn from importing the Flask application, producing Heroku’s Application Error page. The recovery release removed the conflict artifacts, rebuilt the pricing module from a clean implementation, and added container build-time source checks so unresolved Python merge markers or syntax errors fail during the Docker build instead of reaching a live dyno.

## Verified production state

| Check | Result |
|---|---|
| Live health endpoint | Healthy JSON response |
| Active catalog records | 517 |
| Stored recommendations | 517 |
| Read-only audit job | Completed successfully for 517 records |
| eBay mutations during validation | None |
| Backend regression suite | 129 tests passing |
| Frontend production build | Passing |
| Deployed code recovery commit | `141f2fa` |
| Catalog job hardening commit | `1b17f24` |
| Worker documentation/process commit | `5830242` |

## What is now production-ready

### Deployment resilience

The Docker image now checks every Python module for unresolved merge markers and compiles the application before release. This prevents the specific deployment failure that caused the Heroku outage. The public health and Commerce Agent dashboard endpoints were verified after the final deployment.

### Durable, read-only catalog jobs

Long-running catalog work is represented as persisted jobs in Supabase. The application supports queued jobs for active-listing import, selected-pilot GetItem enrichment, and recommendation audits. Each job transitions through `queued`, `running`, `completed`, or `failed` status and can be polled through `GET /api/commerce/jobs/<id>`.

The job runner contains no eBay mutation path. It can only import data, enrich stored records from the official eBay Trading API, and create recommendations. Approval and application remain separate endpoints and require explicit seller interaction.

### Evidence-backed enrichment

The **Enrich selected (read-only)** control supports a bounded set of 1–20 active eBay listing IDs. It retrieves official eBay detail data, preserves the official source title and description, maps returned item specifics to the normalized schema, and records the source as `ebay_get_item`. The enrichment operation never approves, updates, or publishes a listing.

### Better recommendation transparency

Pricing labels now distinguish actual used sold comparables, active-listing estimates, AI estimates, and seller-price fallbacks. A seller-price fallback is explicitly shown as **“Seller price retained — no market evidence”** rather than being presented as sold or active comparable data.

The UI no longer offers the default approval path for high-risk recommendations. Those cards remain visible for seller review but display **“High risk — seller review only.”** A low-risk recommendation must still contain a real non-noop field proposal before approval can be offered.

### Scalable Taxonomy checks

eBay Taxonomy required-aspect rules are cached by marketplace, category tree, and category for 24 hours. During a bulk audit, repeated listings in the same category no longer trigger duplicate Taxonomy aspect API requests.

### Optional worker process

The Heroku container manifest now defines an optional `worker` process that runs `python worker.py` from the same production image. It is intended for reliable background processing at catalog scale and can run only the read-only job types described above.

## Safe operating workflow

1. Open the **Commerce Agent** tab and use **Analyze Active Listings** to refresh active eBay records.
2. Filter the queue and select a balanced 10–20 listing pilot.
3. Use **Enrich selected (read-only)**. Wait for the background enrichment and audit jobs to complete.
4. Review the source title, official eBay evidence, Taxonomy result, pricing source, proposed changes, confidence, and risk.
5. Treat `seller_price_fallback`, `active_comparable`, and `ai_estimate` as advisory. They are not sold-comparable evidence.
6. Keep high-risk items in review-only status. Only consider a low-risk item when it has an evidence-backed, non-noop proposed field change.
7. Use **Approve only** first. This writes an internal approval record and does not call eBay.
8. Use **Apply approved change** only after the exact approved fields have been reviewed. That is the only step that can call the existing eBay offer update flow.

## Current data limitations

The system currently reports substantial evidence and Taxonomy gaps in the imported catalog. These are data-quality findings, not a reason to auto-fill listings. Missing category, missing required specifics, missing official detail fields, and absent sold-comparable evidence keep a recommendation at high risk or prevent a safe change proposal.

The system does not invent sold pricing. To produce evidence-backed sold-price recommendations, configure a verified `SOLD_COMPS_API_URL` or provide a seller-maintained sold-comparables CSV. Without that source, the current seller price is retained and truthfully labeled as a fallback.

## Optional capacity step

Small pilot jobs are executed immediately by the web process. For durable background throughput at larger catalog volumes, activate one Heroku worker process after confirming the account’s available dyno capacity:

```sh
heroku ps:scale worker=1 -a hht-catalog-b34ed1b32417
```

This starts an additional hosted process and should be performed only by the account owner after reviewing its platform implications. It is not required for the currently verified 10–20 listing pilot workflow.

## Safety contract

> The Commerce Agent remains approval-only. No catalog import, enrichment, Taxonomy validation, pricing evaluation, title recommendation, audit, or worker job may automatically modify or publish an eBay listing.

