# HHT Catalog Enrichment Pipeline

## Purpose

This pipeline fills local HHT catalog evidence from the seller's **active eBay listings** while keeping eBay immutable. It reads active `ItemID` values from the imported catalog, retrieves official Trading API `GetItem` details, and stores the category, item specifics, description, pricing context, and evidence in the Commerce Agent database. It does **not** call `ReviseItem`, Inventory API update endpoints, offer publication endpoints, or any other eBay write operation.

The existing Heroku container worker is the deployment runtime. It shares the existing Supabase `DATABASE_URL` with the web process, so no additional provider, runtime, or credential is required.

## Data lifecycle

| Stage | Operation | eBay effect |
|---|---|---|
| Active import | `GetMyeBaySelling` identifies active listings and reconciles local lifecycle state | Read only |
| Checkpoint seed | One local checkpoint is stored for every active listing with an ItemID | None |
| Enrichment | `GetItem` runs in sequential chunks of at most 20 records | Read only |
| Evidence storage | Official category and item specifics are stored in Supabase/local development SQLite | None |
| Audit refresh | New recommendations are generated from enriched evidence | None |
| Seller review | Queue loads 25 recommendation cards per page | None |
| Approval/apply | Remains a separate, existing seller-controlled workflow | Only after explicit seller action |

## Running it

1. In **Commerce Agent**, select **Analyze Active Listings** if the catalog has not been refreshed recently.
2. Select **Enrich active catalog (read-only)**. The app creates or resumes local checkpoints and queues the job.
3. The job saves progress after each record and processes at most 20 records per chunk. The screen can be refreshed; progress remains in the database.
4. If a transient eBay request fails after bounded retries, that record is marked `failed`. Select **Resume failed enrichment** to retry only those records.
5. Once the job completes, the app runs the local audit and opens the review queue at page 1. Use **Previous** and **Next** to navigate 25 cards at a time.

For resilient catalog-scale operation, run one worker process alongside the web process:

```sh
heroku ps:scale worker=1 -a hht-catalog-b34ed1b32417
```

The web process also starts a safe fallback runner for an interactive request, but the worker is the recommended execution path for a full catalog because it can continue independently of the browser session.

## API contract

| Endpoint | Method | Description |
|---|---|---|
| `/api/commerce/enrich/full/start` | `POST` | Starts or resumes a full-catalog, read-only enrichment job. Send `{ "resumeFailed": true }` to retry only failed checkpoints. |
| `/api/commerce/jobs/<jobId>` | `GET` | Returns persisted job status, chunk result, progress, and checkpoint counts. |
| `/api/catalog/enriched?page=1&pageSize=25` | `GET` | Returns successfully enriched catalog records in fixed 25-item pages. `pageSize` is capped at 25. |
| `/api/commerce/recommendations/page?page=1&pageSize=25` | `GET` | Returns a fixed 25-card approval queue page. |

## Runtime controls

| Variable | Default | Purpose |
|---|---:|---|
| `ENRICHMENT_CHUNK_SIZE` | `20` | Maximum ItemIDs claimed per worker chunk; capped at 20. |
| `ENRICHMENT_RATE_LIMIT_SECONDS` | `0.35` | Delay after each `GetItem` request. |
| `ENRICHMENT_MAX_RETRIES` | `3` | Maximum attempts for transient read-only retrieval errors. |
| `ENRICHMENT_STALE_SECONDS` | `900` | Time after which abandoned `processing` checkpoints may return to `pending`. |
| `WORKER_POLL_SECONDS` | `10` | Idle worker polling interval. |

## Safety rules

- The pipeline makes **only** `GetItem` detail calls once active IDs are local.
- eBay listing changes require the existing explicit **Approve only** then **Apply approved change** workflow.
- Failed records remain visible as failures; they are not silently retried forever.
- Enriched details are evidence, not permission to change a listing. The seller remains responsible for reviewing title, category, price, condition, and item specifics.
- The paginated UI avoids transferring the entire approval queue to a phone browser.

## Recovery behavior

A sudden web or worker restart does not lose completed enrichment. Checkpoints and the job state are stored in Supabase. A checkpoint left in `processing` beyond the stale threshold is released when the next full-enrichment request starts. Concurrent web and worker claim attempts are guarded by a compare-and-set transition, so one checkpoint is processed by at most one claimant at a time.
