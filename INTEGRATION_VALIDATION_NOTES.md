# Integration Validation Notes

## eBay Seller Hub Draft Feed

Official eBay documentation states that Seller Hub feed uploads use only `FX_LISTING` and `FX_FULFILLMENT` as Sell Feed API task types. Creating drafts is performed by using `FX_LISTING` together with the CSV Draft action; `FX_DRAFT` is not a supported Feed API task type. The documented flow is: create task, upload CSV, poll task until `COMPLETED` or `COMPLETED_WITH_ERROR`, then download the result file for row-level errors and `uploadSummary` counts.

> Source: [eBay Seller Hub feed flow](https://developer.ebay.com/api-docs/sell/static/feed/fx-feeds-overview.html), accessed 2026-09-23.

The official quick reference confirms that `FX_LISTING` supports creating new listings, creating new drafts, editing price and quantity, relisting unsold items, and ending listings. It identifies `VerifyAddItem` as the associated Trading API call for the Create new drafts template.

> Source: [eBay Seller Hub Feed API quick reference](https://developer.ebay.com/api-docs/sell/static/feed/fx-feeds-quick-reference.html), accessed 2026-09-23.

The Seller Hub template guide recommends downloading the account/category-specific **Create new drafts** template from Seller Hub Reports. It documents category ID, title, photo URL, description, format, quantity, UPC, and price fields, and notes that local photos can be added after the draft template is uploaded.

> Source: [eBay Inventory Onboarding Guide](https://pages.ebay.com/sh/reports/help/create-listings-bulk/), accessed 2026-09-23.

## NVIDIA Vision Route

NVIDIA documents `z-ai/glm-5.3-flash` as a current native multimodal model that accepts text and images. The model defaults to a maximum reasoning budget; `reasoning_effort` accepts `low`, `high`, or `max`, and the chat template should explicitly pass `clear_thinking=true` for chat scenarios. The endpoint supports up to eight images per request.

> Source: [NVIDIA NIM GLM-5.3-Flash reference](https://docs.api.nvidia.com/nim/reference/z-ai-glm-5-3-flash), accessed 2026-09-23.

NVIDIA also documents `meta/llama-3.2-90b-vision-instruct` as an image-capable model available through `https://integrate.api.nvidia.com/v1/chat/completions`.

> Source: [NVIDIA Llama 3.2 90B Vision model page](https://build.nvidia.com/meta/llama-3.2-90b-vision-instruct), accessed 2026-09-23.

## Live Observations

On 2026-09-23, the production HHT health endpoint reported Groq, NVIDIA, and OpenRouter configured, and eBay OAuth token exchange plus eBay category search and Taxonomy field retrieval succeeded. A real Groq image-analysis request succeeded. Direct NVIDIA alternate requests reached Cloudflare/Heroku gateway errors even though the app health endpoint recovered. The production application now queues NVIDIA work to its persisted worker, returns HTTP 202 immediately, and polls the existing job endpoint. A live job was claimed by the worker and completed with a structured OpenRouter fallback result when NVIDIA did not return inside its initial inference window. The worker now has a dedicated, longer NVIDIA inference budget while the web route remains short and protected. Any sanitized NVIDIA failure is retained in `providerFailures` when the alternate succeeds.

The NVIDIA worker uses a model cascade: it tries the configured NVIDIA vision model first (currently GLM-5.3-Flash), then tries NVIDIA Nemotron 3 Nano Omni with thinking disabled for low-latency image extraction before it falls through to the existing OpenRouter alternate. This cascade uses the same NVIDIA credential and never creates, revises, or publishes an eBay listing.

Heroku log-session access from the current environment returned HTTP 401 because no Heroku API credential is available to this runtime.
