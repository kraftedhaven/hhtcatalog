# Enhancement: GPU-Accelerated Market Pricing Intelligence + Photo Quality Analysis

**Status:** Ready for Implementation  
**Linked PR:** #5 (Restore eBay category and item-specific field coverage)  
**User Resources:** NVIDIA Inception, NVIDIA Registered Developer, 6G, GitHub Student Dev Pack, Codex in VS Code

---

## Problem Statement

The Commerce Agent currently recommends price changes but lacks real market data, causing:
- ❌ Slow sales (inventory not priced competitively)
- ❌ Manual lookups (time-consuming for 287+ listings)
- ❌ Lost revenue (underpriced or overpriced listings)

**Goal:** Automate accurate market-based pricing + photo quality scoring so listings are optimized for visibility AND conversion.

---

## Builds On
- ✅ PR #5: Restored eBay category/field coverage
- ✅ Existing Commerce Agent: Dashboard, audit, recommendations, approval queue
- ✅ Existing eBay OAuth: Seller authentication
- ✅ Existing eBay Inventory API: Listing retrieval & updates

---

## Feature 1: eBay Market Data Integration

### What it does
- Fetch **active listing estimates** from eBay Browse API using the same brand, size, material, style, condition, and category signals.
- Optionally use seller-owned order history or approved eBay Marketplace Insights access for sold-history data when available.
- Compare current price vs. clearly labeled market data.

### Implementation
- Use **eBay Browse API** (`GET /browse/v1/item_summary/search`) for active listings only.
- Do not label Browse results as sold comps. Store the label as `active_listing_estimate`.
- If eBay grants Marketplace Insights API access, add a separate `sold_marketplace_insights` source.
- If seller order/report APIs are available, add a separate `seller_order_history` source for your own sold listings.
- Requires existing app credentials for Browse; seller-owned data requires seller OAuth scopes.
- Cache results with a TTL; active listing data changes frequently.
- Store in existing `recommendations` table with `pricing_data_json` field

### Expected Output
```json
{
  "recommendationId": "rec-123",
  "current_price": 89.99,
  "pricing_source": "active_listing_estimate",
  "active_median_price": 69.50,
  "active_low_price": 55.00,
  "active_high_price": 84.99,
  "active_listings_count": 23,
  "sold_data_available": false,
  "confidence": "medium",
  "recommendation": {
    "price": 74.99,
    "reasoning": "Similar active listings are priced from $55-85. This is not sold-comps data. Recommend $74.99 within safety bounds and seller review."
  }
}
```

### Files to Modify
- `hht_app/commerce_agent.py` — add `_fetch_market_comps()` function
- `hht_app/schema.py` — add `pricing_data_json` column to recommendations
- `app.py` — add `GET /api/commerce/market/<listing_id>` endpoint for UI detail view

---

## Feature 2: GPU-Accelerated Price Recommendation Engine

### What it does
- **Real-time similarity matching** between your item and active listings, seller-owned sold history, or approved Marketplace Insights records
- Uses vector embeddings (GPU acceleration via NVIDIA TensorRT)
- Sub-100ms inference per item
- Runs locally or on NVIDIA Inception endpoints

### Implementation
- **Model:** Small embedding model (e.g., `sentence-transformers/all-MiniLM-L6-v2`)
- **Input:** Item features (title, category, brand, size, condition, description excerpt)
- **Output:** Similarity score to each market comp
- **Infrastructure:** NVIDIA Inception GPU credits or local CPU fallback
- Cache embeddings for your owned listings (minimize recomputation)

### Expected Output
```json
{
  "item_sku": "LEVIS-123",
  "embedding_inference_ms": 45,
  "top_3_comps": [
    {
      "comparison_id": "ebay-L1",
      "comparison_price": 69.99,
      "comparison_source": "active_listing_estimate",
      "similarity": 0.92,
      "days_to_sell": null
    },
    {
      "comparison_id": "ebay-L2",
      "comparison_price": 72.49,
      "comparison_source": "seller_order_history",
      "similarity": 0.88,
      "days_to_sell": 8
    }
  ],
  "recommended_price": 70.99
}
```

### Files to Modify
- `hht_app/pricing.py` — add `_vector_embeddings()` + `_gpu_price_recommendations()`
- `hht_app/commerce_agent.py` — integrate GPU results into audit flow
- `.env.example` — add `NVIDIA_INCEPTION_API_KEY` (optional), `ENABLE_GPU_PRICING=true`

---

## Feature 3: Photo Quality Analysis

### What it does
- Analyzes existing listing photos
- Scores: blur, lighting, framing, completeness
- Flags weak photo coverage (missing angles, low contrast, etc.)
- Recommends photo improvements

### Implementation
- Reuse existing image analysis pipeline
- Add metrics: `blur_score`, `lighting_score`, `composition_score`, `detail_score` (0-100 each)
- Store results in `listings` table (`photo_analysis_json`)
- Flag listings with <60 overall score as "Needs better photos"

### Expected Output
```json
{
  "listing_id": "L123",
  "photos": [
    {
      "url": "https://...",
      "blur_score": 85,
      "lighting_score": 72,
      "composition_score": 88,
      "detail_score": 79,
      "overall": 81,
      "feedback": "Good shot, slightly dim lighting. Add brighter angle."
    }
  ],
  "photo_coverage_score": 68,
  "recommendation": "Add close-up of label/tags and alternate color/condition view"
}
```

### Files to Modify
- `hht_app/commerce_agent.py` — add `_analyze_photos()` function
- `hht_app/schema.py` — add `photo_analysis_json` column to listings
- `frontend/src/lib/components/CommerceAgent.svelte` — add photo quality widget to recommendation card

---

## Feature 4: Parallel Bulk Import with GPU Workers

### What it does
- Import 100+ listings concurrently (not sequentially)
- Use process pool for CPU-bound tasks (embeddings, price calc)
- Distribute GPU inference across available accelerators

### Implementation
- `ProcessPoolExecutor` or `asyncio` for parallelism
- Batch eBay API calls and respect the configured rate limits for each API.
- Monitor NVIDIA GPU utilization
- Report progress: "Imported 45/287 listings, 12 GPU inferences pending..."

### Files to Modify
- `hht_app/commerce_agent.py` — refactor `import_listings()` to use `concurrent.futures`
- `app.py` — add `GET /api/commerce/import/status` for real-time progress
- `frontend/src/lib/components/CommerceAgent.svelte` — add progress bar during import

---

## Implementation Phases

### Phase 1: Market Data (Week 1)
- [ ] Keep eBay Browse API data labeled as active-listing estimates
- [ ] Add optional seller-owned sold/order-history integration if permitted by seller OAuth scopes
- [ ] Add Marketplace Insights integration only after eBay grants restricted access
- [ ] Store market comparison records in DB with explicit source labels
- [ ] Update recommendation UI with price reasoning
- [ ] Tests: price safety rules still hold, comps are fetched correctly

### Phase 2: GPU Pricing (Week 2)
- [ ] Add embedding model (local or NVIDIA Inception)
- [ ] Implement similarity matching
- [ ] Cache embeddings for owned listings
- [ ] Tests: GPU inference accuracy, fallback to CPU

### Phase 3: Photo Analysis (Week 3)
- [ ] Reuse existing vision pipeline for photo scoring
- [ ] Add quality metrics to DB
- [ ] Update recommendation UI with photo feedback
- [ ] Tests: photo scores are consistent

### Phase 4: Parallel Bulk Import (Week 4)
- [ ] Refactor import to use workers
- [ ] Add progress API endpoint
- [ ] Add progress bar to UI
- [ ] Load testing: 287+ listings in <2 min

---

## Safety & Testing

### Price Safety (Still Applied)
- Max 10% reduction per recommendation (configurable)
- Minimum price floor respected
- Vintage/designer/collectible items require approval
- If market data unavailable → conservative estimate or "Needs Review"
- Never present active listings as sold comps.

### Tests to Add
- `test_fetch_active_listing_estimates()` — eBay Browse API integration
- `test_marketplace_insights_requires_access()` — restricted API remains disabled without approval
- `test_gpu_pricing_inference()` — embedding + similarity matching
- `test_photo_quality_scoring()` — photo analysis metrics
- `test_parallel_import_rate_limiting()` — concurrent import respects eBay limits
- `test_price_safety_with_market_data()` — safety rules hold with market input

### Deployment Checklist
- [ ] `DATABASE_URL` configured (Supabase PostgreSQL)
- [ ] eBay OAuth scopes include `sell.inventory`, `sell.account`
- [ ] `NVIDIA_INCEPTION_API_KEY` set (optional; CPU fallback enabled)
- [ ] `.env` includes new variables
- [ ] Heroku dyno size: Standard-2x or higher (GPU memory)
- [ ] Tests pass: `python -m unittest tests.test_commerce_agent -v`
- [ ] Existing image analysis + draft/update flows still work

---

## Acceptance Criteria

✅ **Market pricing works:**
- Active listing estimates fetched from eBay Browse API
- Sold-comps data only appears from approved Marketplace Insights access or seller-owned order history
- Price recommendations include market reasoning
- Safety rules prevent unsafe price changes

✅ **GPU acceleration works:**
- Embeddings computed <100ms per item
- Fallback to CPU if NVIDIA unavailable
- Cache prevents recomputation

✅ **Photo analysis works:**
- Each photo scored on blur/lighting/composition
- Overall listing photo coverage scored
- Recommendations shown in UI

✅ **Bulk import parallelized:**
- 287 listings imported in <2 min (vs ~10 min sequential)
- Progress updates in real-time
- Rate limits respected

✅ **Existing features still work:**
- Image upload + analyze
- CSV export
- eBay draft creation
- eBay OAuth
- Commerce Agent recommend-only mode

✅ **No breaking changes:**
- All existing tests pass
- Recommendation approval flow unchanged
- eBay update route reused (no second implementation)

---

## Rewards

When complete:
- 🎉 **Faster sales** — competitively priced listings with market reasoning
- 💰 **Higher margins** — identify underpriced inventory
- ⚡ **Bulk optimization** — 287 listings analyzed in minutes, not hours
- 📸 **Better photos** — auto-flagged weak photo coverage
- 🤖 **Smarter AI** — GPU-powered similarity matching replaces guessing
