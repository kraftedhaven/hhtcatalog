# NVIDIA Vision Route Validation Report

**Author:** Manus AI  
**Date:** 2026-09-23  
**Deployment:** `4f8d643` on `kraftedhaven/hhtcatalog` main branch

## Conclusion

The NVIDIA route is now **production-safe and passing through the worker workflow**. The browser no longer waits for NVIDIA inference. It receives an immediate `202 Accepted` response with a persisted job ID, while the Heroku worker performs image analysis in the background. This eliminates the prior browser-facing Cloudflare/Heroku gateway failures during slow NVIDIA responses.

The final live validation confirmed that the worker accepted a real image-analysis job, processed it without changing any eBay data, and returned a structured result. The route remains resilient when NVIDIA capacity is intermittent because it uses a layered fallback sequence instead of failing the listing workflow.

## What Changed

| Area | Current behavior | Reliability benefit |
|---|---|---|
| Browser request | The dashboard queues NVIDIA analysis through `POST /api/nvidia/analyze/start` and receives `202 Accepted`. | The browser is not held open while a vision model runs. |
| Durable job execution | The existing persisted Commerce Agent job table stores the image job, and the active worker claims it. | Work survives a web-process restart and can be polled through the standard job endpoint. |
| NVIDIA primary model | The configured NVIDIA model is attempted first. The current configured model is `z-ai/glm-5.3-flash`, with low reasoning enabled. | Preserves the existing NVIDIA configuration while preventing the model’s default maximum reasoning budget from consuming the web request. |
| NVIDIA model fallback | If the first NVIDIA model times out, is unavailable, or rejects the request, HHT tries `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` with thinking disabled. | A second NVIDIA-hosted vision route is attempted before leaving NVIDIA. |
| Cross-provider fallback | If both NVIDIA choices cannot complete, the worker uses OpenRouter; Groq remains the fast hosted route in the standard Analyze mode. | A provider-capacity problem does not strand a reseller’s photo workflow. |
| Diagnostics | Completed and failed jobs return sanitized provider details, model identity when NVIDIA succeeds, and fallback details when a provider is bypassed. | The dashboard can explain the reason for a delay or fallback without exposing secrets, raw upstream bodies, or uploaded image bytes. |
| eBay safety | NVIDIA jobs are marked read-only and do not create, revise, approve, or publish eBay listings. | AI extraction remains separated from seller-approved listing actions. |

## Live Validation Evidence

The production health response remained healthy throughout validation. It reported Groq, NVIDIA, and OpenRouter configured; demo mode disabled; and the corrected Seller Hub task type of `FX_LISTING`.

A live NVIDIA worker job with ID `49a7e6d7-5a9c-4e2d-8c55-7f8f655cb024` was accepted with HTTP `202`, claimed by the worker, and completed with `provider: "nvidia"`. That proves the strengthened worker path can return a real NVIDIA result rather than a gateway error.

A later live worker job with ID `497ed73a-c505-4cb1-ae8c-81bd6ad633d2` was also accepted with HTTP `202`. In that run, NVIDIA’s Nemotron fallback returned a sanitized `503` provider error, after which OpenRouter completed the analysis successfully. This demonstrates that a transient NVIDIA capacity failure no longer breaks the user workflow or the Heroku application.

The complete backend regression suite passed after the final changes: **160 tests passed**. The Svelte production build also completed successfully.

## How to Use It

In HHT Catalog, open **Analyze** and choose **NVIDIA vision worker (reliable background analysis)** from the analysis-engine menu. Select one to three product photos and choose **Analyze photos**. The interface will show that the job is queued or running, then load the returned listing result for seller review. The result remains a draft in HHT until the reseller explicitly exports it or chooses an approval-controlled eBay workflow.

The normal **Hosted vision with automatic fallback** option remains the quickest choice for ordinary photo analysis. Use the NVIDIA worker option when NVIDIA capability is specifically desired or when the hosted model is temporarily unavailable. No Heroku configuration change is required for this worker route; the live worker is already consuming queued jobs.

## Model and API Basis

NVIDIA documents GLM-5.3-Flash as a multimodal model that accepts image input and allows `reasoning_effort` control. Its documentation states that the model defaults to maximum reasoning and recommends explicitly controlling reasoning behavior for chat use cases. [1]

NVIDIA documents Nemotron 3 Nano Omni as an image-capable multimodal model. Its image example uses OpenAI-compatible chat completions and disables thinking for direct image descriptions, which matches HHT’s low-latency product-attribute extraction use case. [2]

> eBay Seller Hub draft creation is kept separate from this analysis route. HHT’s production health endpoint now reports `FX_LISTING`, the official Seller Hub Feed API task type used with a CSV `Draft` action. [3]

## References

[1]: https://docs.api.nvidia.com/nim/reference/z-ai-glm-5-3-flash "NVIDIA NIM: Z.ai GLM-5.3-Flash"
[2]: https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-nano-omni-30b-a3b-reasoning "NVIDIA NIM: Nemotron 3 Nano Omni 30B A3B Reasoning"
[3]: https://developer.ebay.com/api-docs/sell/static/feed/fx-feeds-overview.html "eBay Seller Hub feed flow"
