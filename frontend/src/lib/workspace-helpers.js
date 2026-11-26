/**
 * Pure helper functions for the HHT Inventory Workspace.
 * Keeps price tier extraction, title candidate normalization, polling merge
 * preservation of unsaved edits, provenance classification, and missing-field
 * targeting clean, shared, and directly unit-testable.
 */

export const INSUFFICIENT_MARKET_MESSAGE =
    'Insufficient market evidence \u2014 seller confirmation required.';

export function getPriceTiers(item) {
    if (!item || typeof item !== 'object') {
        return { quickSale: null, balanced: null, premium: null, hasMarketTiers: false };
    }
    const opts = item.pricingOptions || {};
    const legacy = item.pricingTiers || {};

    const toNum = (val) => {
        if (val === null || val === undefined || val === '') return null;
        const n = Number(val);
        return Number.isFinite(n) && n > 0 ? Number(n.toFixed(2)) : null;
    };

    const quickSale =
        toNum(opts.quick_sale?.price) ??
        toNum(item.quickSalePrice) ??
        toNum(legacy.quickSale?.price) ??
        toNum(opts.quickSale?.price);

    const balanced =
        toNum(opts.balanced?.price) ??
        toNum(item.balancedPrice) ??
        toNum(legacy.balanced?.price);

    const premium =
        toNum(opts.premium?.price) ??
        toNum(item.premiumPrice) ??
        toNum(legacy.premium?.price);

    return {
        quickSale,
        balanced,
        premium,
        quickMeta: opts.quick_sale || legacy.quickSale || null,
        balancedMeta: opts.balanced || legacy.balanced || null,
        premiumMeta: opts.premium || legacy.premium || null,
        hasMarketTiers: Boolean(quickSale || balanced || premium),
    };
}

export function getTitleCandidateEntries(item) {
    const raw = item?.titleCandidates;
    if (!raw) return [];
    if (Array.isArray(raw)) {
        return raw
            .map((entry, idx) => {
                if (typeof entry === 'string') {
                    return {
                        key: `option_${idx + 1}`,
                        title: entry,
                        length: entry.length,
                        score: null,
                        strategyLabel: '',
                        isFallback: false,
                        reason: '',
                    };
                }
                if (entry && typeof entry === 'object' && entry.title) {
                    return {
                        key: entry.key || `option_${idx + 1}`,
                        title: String(entry.title),
                        length: Number(entry.length || String(entry.title).length),
                        score: entry.score ?? null,
                        strategyLabel: entry.strategyLabel || '',
                        isFallback: Boolean(entry.isFallback),
                        reason: entry.reason || '',
                    };
                }
                return null;
            })
            .filter(Boolean);
    }
    if (typeof raw === 'object') {
        return Object.entries(raw)
            .map(([key, cand]) => {
                if (typeof cand === 'string' && cand.trim()) {
                    return {
                        key,
                        title: cand,
                        length: cand.length,
                        score: null,
                        strategyLabel: '',
                        isFallback: false,
                        reason: '',
                    };
                }
                if (cand && typeof cand === 'object' && cand.title) {
                    return {
                        key,
                        title: String(cand.title),
                        length: Number(cand.length || String(cand.title).length),
                        score: cand.score ?? null,
                        strategyLabel: cand.strategyLabel || '',
                        isFallback: Boolean(cand.isFallback),
                        reason: cand.reason || '',
                    };
                }
                return null;
            })
            .filter(Boolean);
    }
    return [];
}

export function getMarketPricingEvidence(item) {
    const listing = item?.listing || {};
    const pricingRec = listing.pricingRecommendation || {};
    const sold = listing.soldPricing || listing.soldComparableSummary || {};
    const active = listing.activeListingEstimate || {};

    const soldCount =
        sold.status === 'ok' && Number(sold.sampleSize) > 0 ? Number(sold.sampleSize) : 0;
    const activeCount =
        Number(active.sampleSize) > 0 && Number(active.medianActivePrice) > 0
            ? Number(active.sampleSize)
            : 0;

    const soldPrice =
        soldCount > 0 && Number(sold.medianSoldPrice) > 0
            ? Number(sold.medianSoldPrice)
            : Number(listing.soldPrice) > 0 && soldCount > 0
              ? Number(listing.soldPrice)
              : null;

    const activePrice =
        activeCount > 0
            ? Number(active.medianActivePrice)
            : Number(listing.activeComparablePrice) > 0 && activeCount > 0
              ? Number(listing.activeComparablePrice)
              : null;

    const rawAiEst = Number(listing.aiEstimatedPrice);
    const aiEstimatedPrice = Number.isFinite(rawAiEst) && rawAiEst > 0 ? rawAiEst : null;

    const hasMarketEvidence = soldCount > 0 || activeCount > 0;
    const insufficient =
        Boolean(listing.insufficientMarketEvidence) ||
        Boolean(pricingRec.insufficientMarketEvidence) ||
        !hasMarketEvidence;

    return {
        soldCount,
        soldPrice,
        soldLow: soldCount > 0 ? Number(sold.lowSoldPrice || soldPrice) : null,
        soldHigh: soldCount > 0 ? Number(sold.highSoldPrice || soldPrice) : null,
        activeCount,
        activePrice,
        activeLow: activeCount > 0 ? Number(active.lowActivePrice || activePrice) : null,
        activeHigh: activeCount > 0 ? Number(active.highActivePrice || activePrice) : null,
        aiEstimatedPrice,
        hasMarketEvidence,
        insufficient,
        marketMessage:
            listing.marketMessage ||
            pricingRec.marketMessage ||
            (insufficient ? INSUFFICIENT_MARKET_MESSAGE : ''),
        pricingSource: listing.pricingSource || pricingRec.pricingSource || '',
        pricingStrategy: listing.pricingStrategy || pricingRec.pricingStrategy || '',
        pricingConfidence: pricingRec.pricingConfidence || '',
        dataFreshness: pricingRec.dataFreshness || listing.commerceAgent?.evidenceTimestamp || '',
        demand: listing.demandMetrics || {},
    };
}

export function formatProvenanceClassification(prov, fieldVal = '') {
    if (!prov || typeof prov !== 'object' || prov.source === 'missing') {
        if (fieldVal && String(fieldVal).trim()) {
            return {
                category: 'ai_inference',
                label: 'AI Inference',
                shortLabel: 'AI Inference',
                badgeClass: 'prov-ai_inference',
            };
        }
        return {
            category: 'missing',
            label: 'Missing — Needs Input',
            shortLabel: 'Missing',
            badgeClass: 'prov-missing',
        };
    }

    if (prov.source === 'seller_input' || prov.sellerConfirmed === true) {
        return {
            category: 'seller_input',
            label: prov.sellerConfirmed ? 'Seller Confirmed' : 'Seller Input',
            shortLabel: 'Seller Input',
            badgeClass: 'prov-seller_input',
        };
    }

    switch (prov.source) {
        case 'observed_photo_fact':
        case 'photo_evidence':
            return {
                category: 'observed_photo_fact',
                label: 'Observed Photo Fact',
                shortLabel: 'Observed Fact',
                badgeClass: 'prov-observed_photo_fact',
            };
        case 'market_sold_comp':
        case 'exact_used_sold':
        case 'similar_used_sold':
            return {
                category: 'market_evidence',
                label: 'Market Evidence (Sold Comps)',
                shortLabel: 'Sold Comps',
                badgeClass: 'prov-market_sold_comp',
            };
        case 'market_active_comp':
        case 'active_comparable':
            return {
                category: 'market_evidence',
                label: 'Market Evidence (Active Comps)',
                shortLabel: 'Active Comps',
                badgeClass: 'prov-market_active_comp',
            };
        case 'demand_signal':
            return {
                category: 'market_evidence',
                label: 'Market Demand Signal',
                shortLabel: 'Demand Signal',
                badgeClass: 'prov-demand_signal',
            };
        case 'fallback_estimate':
        case 'ai_estimate':
            return {
                category: 'fallback_estimate',
                label: 'AI Fallback Estimate',
                shortLabel: 'AI Fallback',
                badgeClass: 'prov-fallback_estimate',
            };
        case 'ai_inference':
        default:
            return {
                category: 'ai_inference',
                label: 'AI Inference',
                shortLabel: 'AI Inference',
                badgeClass: 'prov-ai_inference',
            };
    }
}

export function mergePolledItemsWithPending(serverItems, currentItems, pendingPatches) {
    const patchMap = pendingPatches instanceof Map ? pendingPatches : new Map(Object.entries(pendingPatches || {}));
    const currentById = new Map((currentItems || []).map((it) => [it.id, it]));

    return (serverItems || []).map((incoming) => {
        const patch = patchMap.get(incoming.id);
        const existing = currentById.get(incoming.id);
        if (!patch || typeof patch !== 'object' || Object.keys(patch).length === 0) {
            return incoming;
        }

        const mergedListing = { ...(incoming.listing || {}) };
        const mergedProvenance = { ...(incoming.provenance || {}) };

        for (const [field, value] of Object.entries(patch)) {
            if (field === 'confirmField' && value) {
                const key = String(value);
                mergedProvenance[key] = {
                    ...(mergedProvenance[key] || { source: 'seller_input', confidence: 'high' }),
                    sellerConfirmed: true,
                };
            } else if (field === 'confirmAll' && value === true) {
                for (const pKey of Object.keys(mergedProvenance)) {
                    if (mergedProvenance[pKey] && typeof mergedProvenance[pKey] === 'object') {
                        mergedProvenance[pKey] = { ...mergedProvenance[pKey], sellerConfirmed: true };
                    }
                }
            } else if (field === 'selectedTitleCandidate' && incoming.titleCandidates?.[value]?.title) {
                mergedListing.title = incoming.titleCandidates[value].title;
            } else if (field === 'selectedPricingOption' && incoming.pricingOptions?.[value]?.price) {
                mergedListing.price = String(incoming.pricingOptions[value].price);
            } else {
                mergedListing[field] = value;
                if (existing?.listing && field in existing.listing) {
                    mergedListing[field] = existing.listing[field];
                }
            }
        }

        return {
            ...incoming,
            listing: mergedListing,
            provenance: mergedProvenance,
        };
    });
}

export function findFirstMissingFieldTarget(items, preferredItem = null) {
    const candidates = preferredItem
        ? [preferredItem, ...(items || []).filter((it) => it.id !== preferredItem.id)]
        : items || [];

    const firstMissingFor = (item) => {
        const l = item?.listing || {};
        if (!l.title || !String(l.title).trim()) return 'inspector-title-input';
        const numPrice = Number(l.price);
        if (!Number.isFinite(numPrice) || numPrice <= 0) return 'inspector-price-input';
        if (!l.cat || !String(l.cat).trim()) return 'inspector-category-input';
        if (!l.size || !String(l.size).trim() || l.size === 'Not visible') return 'inspector-size-input';
        if (item?.workflowStatus === 'needs_review') return 'inspector-title-input';
        return '';
    };

    for (const it of candidates) {
        const fieldId = firstMissingFor(it);
        if (fieldId) {
            return { item: it, fieldId };
        }
    }
    return candidates[0] ? { item: candidates[0], fieldId: 'inspector-title-input' } : null;
}
