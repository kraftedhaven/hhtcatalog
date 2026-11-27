/**
 * Pure helper functions for the HHT Inventory Workspace.
 * Kept flat, small, and data-driven for high code health and direct unit testing.
 */

export const INSUFFICIENT_MARKET_MESSAGE =
    'Insufficient market evidence \u2014 seller confirmation required.';

const EMPTY_NUM_INPUTS = new Set([null, undefined, '']);

function asObject(val) {
    if (!val || typeof val !== 'object') return null;
    return val;
}

function readProp(obj, key) {
    const target = asObject(obj);
    return target ? target[key] : undefined;
}

function readNestedProp(obj, k1, k2) {
    return readProp(readProp(obj, k1), k2);
}

export function toPositiveNumber(val) {
    if (EMPTY_NUM_INPUTS.has(val)) return null;
    const n = Number(val);
    if (!Number.isFinite(n)) return null;
    if (n <= 0) return null;
    return Number(n.toFixed(2));
}

function firstPositiveNumber(candidates) {
    for (const candidate of candidates) {
        const parsed = toPositiveNumber(candidate);
        if (parsed !== null) return parsed;
    }
    return null;
}

function firstPresentObject(candidates) {
    for (const candidate of candidates) {
        const obj = asObject(candidate);
        if (obj) return obj;
    }
    return null;
}

function firstNonEmptyString(candidates) {
    for (const candidate of candidates) {
        if (typeof candidate === 'string' && candidate.trim()) return candidate;
    }
    return '';
}

export function getPriceTiers(item) {
    const obj = asObject(item);
    if (!obj) {
        return { quickSale: null, balanced: null, premium: null, hasMarketTiers: false };
    }
    const opts = asObject(obj.pricingOptions) || {};
    const legacy = asObject(obj.pricingTiers) || {};
    const quickSale = firstPositiveNumber([
        readNestedProp(opts, 'quick_sale', 'price'),
        obj.quickSalePrice,
        readNestedProp(legacy, 'quickSale', 'price'),
        readNestedProp(opts, 'quickSale', 'price'),
    ]);
    const balanced = firstPositiveNumber([
        readNestedProp(opts, 'balanced', 'price'),
        obj.balancedPrice,
        readNestedProp(legacy, 'balanced', 'price'),
    ]);
    const premium = firstPositiveNumber([
        readNestedProp(opts, 'premium', 'price'),
        obj.premiumPrice,
        readNestedProp(legacy, 'premium', 'price'),
    ]);
    return {
        quickSale,
        balanced,
        premium,
        quickMeta: firstPresentObject([opts.quick_sale, legacy.quickSale]),
        balancedMeta: firstPresentObject([opts.balanced, legacy.balanced]),
        premiumMeta: firstPresentObject([opts.premium, legacy.premium]),
        hasMarketTiers: firstPositiveNumber([quickSale, balanced, premium]) !== null,
    };
}

function normalizeStringCandidate(key, cand) {
    if (!cand.trim()) return null;
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

function normalizeObjectCandidate(key, cand) {
    if (!cand.title) return null;
    const title = String(cand.title);
    return {
        key,
        title,
        length: Number(cand.length || title.length),
        score: cand.score ?? null,
        strategyLabel: cand.strategyLabel || '',
        isFallback: Boolean(cand.isFallback),
        reason: cand.reason || '',
    };
}

function normalizeCandidateEntry(key, cand) {
    if (typeof cand === 'string') return normalizeStringCandidate(key, cand);
    const obj = asObject(cand);
    return obj ? normalizeObjectCandidate(key, obj) : null;
}

export function getTitleCandidateEntries(item) {
    const raw = readProp(item, 'titleCandidates');
    const obj = asObject(raw);
    if (!obj) return [];
    const pairs = Array.isArray(obj)
        ? obj.map((entry, idx) => [readProp(entry, 'key') || `option_${idx + 1}`, entry])
        : Object.entries(obj);
    return pairs.map(([key, cand]) => normalizeCandidateEntry(key, cand)).filter(Boolean);
}

function extractSoldEvidence(listing, sold) {
    const empty = { soldCount: 0, soldPrice: null, soldLow: null, soldHigh: null };
    if (readProp(sold, 'status') !== 'ok') return empty;
    const count = toPositiveNumber(sold.sampleSize);
    if (count === null) return empty;
    const median = firstPositiveNumber([sold.medianSoldPrice, listing.soldPrice]);
    if (median === null) return empty;
    return {
        soldCount: Math.trunc(count),
        soldPrice: median,
        soldLow: firstPositiveNumber([sold.lowSoldPrice]) ?? median,
        soldHigh: firstPositiveNumber([sold.highSoldPrice]) ?? median,
    };
}

function extractActiveEvidence(active) {
    const empty = { activeCount: 0, activePrice: null, activeLow: null, activeHigh: null };
    const obj = asObject(active);
    if (!obj) return empty;
    const activeCount = toPositiveNumber(obj.sampleSize);
    if (activeCount === null) return empty;
    const activePrice = toPositiveNumber(obj.medianActivePrice);
    if (activePrice === null) return empty;
    return {
        activeCount: Math.trunc(activeCount),
        activePrice,
        activeLow: firstPositiveNumber([obj.lowActivePrice]) ?? activePrice,
        activeHigh: firstPositiveNumber([obj.highActivePrice]) ?? activePrice,
    };
}

function isInsufficientMarket(listing, pricingRec, hasMarketEvidence) {
    if (listing.insufficientMarketEvidence) return true;
    if (pricingRec.insufficientMarketEvidence) return true;
    return !hasMarketEvidence;
}

export function getMarketPricingEvidence(item) {
    const listing = asObject(readProp(item, 'listing')) || {};
    const pricingRec = asObject(listing.pricingRecommendation) || {};
    const soldSource = firstPresentObject([listing.soldPricing, listing.soldComparableSummary]) || {};
    const soldEv = extractSoldEvidence(listing, soldSource);
    const activeEv = extractActiveEvidence(listing.activeListingEstimate);
    const hasMarketEvidence = soldEv.soldCount > 0 || activeEv.activeCount > 0;
    const insufficient = isInsufficientMarket(listing, pricingRec, hasMarketEvidence);
    const fallbackMessage = insufficient ? INSUFFICIENT_MARKET_MESSAGE : '';

    return {
        ...soldEv,
        ...activeEv,
        aiEstimatedPrice: toPositiveNumber(listing.aiEstimatedPrice),
        hasMarketEvidence,
        insufficient,
        marketMessage: firstNonEmptyString([listing.marketMessage, pricingRec.marketMessage, fallbackMessage]),
        pricingSource: firstNonEmptyString([listing.pricingSource, pricingRec.pricingSource]),
        pricingStrategy: firstNonEmptyString([listing.pricingStrategy, pricingRec.pricingStrategy]),
        pricingConfidence: firstNonEmptyString([pricingRec.pricingConfidence]),
        dataFreshness: firstNonEmptyString([
            pricingRec.dataFreshness,
            readNestedProp(listing, 'commerceAgent', 'evidenceTimestamp'),
        ]),
        demand: asObject(listing.demandMetrics) || {},
    };
}

const PROVENANCE_STYLES = {
    observed_photo_fact: {
        category: 'observed_photo_fact',
        label: 'Observed Photo Fact',
        shortLabel: 'Observed Fact',
        badgeClass: 'prov-observed_photo_fact',
    },
    photo_evidence: {
        category: 'observed_photo_fact',
        label: 'Observed Photo Fact',
        shortLabel: 'Observed Fact',
        badgeClass: 'prov-observed_photo_fact',
    },
    market_sold_comp: {
        category: 'market_evidence',
        label: 'Market Evidence (Sold Comps)',
        shortLabel: 'Sold Comps',
        badgeClass: 'prov-market_sold_comp',
    },
    exact_used_sold: {
        category: 'market_evidence',
        label: 'Market Evidence (Sold Comps)',
        shortLabel: 'Sold Comps',
        badgeClass: 'prov-market_sold_comp',
    },
    similar_used_sold: {
        category: 'market_evidence',
        label: 'Market Evidence (Sold Comps)',
        shortLabel: 'Sold Comps',
        badgeClass: 'prov-market_sold_comp',
    },
    market_active_comp: {
        category: 'market_evidence',
        label: 'Market Evidence (Active Comps)',
        shortLabel: 'Active Comps',
        badgeClass: 'prov-market_active_comp',
    },
    active_comparable: {
        category: 'market_evidence',
        label: 'Market Evidence (Active Comps)',
        shortLabel: 'Active Comps',
        badgeClass: 'prov-market_active_comp',
    },
    demand_signal: {
        category: 'market_evidence',
        label: 'Market Demand Signal',
        shortLabel: 'Demand Signal',
        badgeClass: 'prov-demand_signal',
    },
    fallback_estimate: {
        category: 'fallback_estimate',
        label: 'AI Fallback Estimate',
        shortLabel: 'AI Fallback',
        badgeClass: 'prov-fallback_estimate',
    },
    ai_estimate: {
        category: 'fallback_estimate',
        label: 'AI Fallback Estimate',
        shortLabel: 'AI Fallback',
        badgeClass: 'prov-fallback_estimate',
    },
};

const DEFAULT_AI_INFERENCE_STYLE = {
    category: 'ai_inference',
    label: 'AI Inference',
    shortLabel: 'AI Inference',
    badgeClass: 'prov-ai_inference',
};

const MISSING_PROV_STYLE = {
    category: 'missing',
    label: 'Missing — Needs Input',
    shortLabel: 'Missing',
    badgeClass: 'prov-missing',
};

function isUnclassifiedProv(prov) {
    const obj = asObject(prov);
    if (!obj) return true;
    return obj.source === 'missing';
}

function isSellerProv(prov) {
    if (prov.source === 'seller_input') return true;
    return prov.sellerConfirmed === true;
}

export function formatProvenanceClassification(prov, fieldVal = '') {
    if (isUnclassifiedProv(prov)) {
        const hasText = typeof fieldVal === 'string' ? Boolean(fieldVal.trim()) : Boolean(fieldVal);
        return hasText ? DEFAULT_AI_INFERENCE_STYLE : MISSING_PROV_STYLE;
    }
    if (isSellerProv(prov)) {
        return {
            category: 'seller_input',
            label: prov.sellerConfirmed ? 'Seller Confirmed' : 'Seller Input',
            shortLabel: 'Seller Input',
            badgeClass: 'prov-seller_input',
        };
    }
    return PROVENANCE_STYLES[prov.source] || DEFAULT_AI_INFERENCE_STYLE;
}

function confirmProvenanceKeys(mergedProvenance, keys) {
    for (const key of keys) {
        const base = asObject(mergedProvenance[key]) || { source: 'seller_input', confidence: 'high' };
        mergedProvenance[key] = { ...base, sellerConfirmed: true };
    }
    return true;
}

function applyTitleCandidatePatch(incoming, mergedListing, key) {
    const title = readNestedProp(incoming.titleCandidates, key, 'title');
    if (title) mergedListing.title = title;
}

function applyPricingOptionPatch(incoming, mergedListing, key) {
    const price = readNestedProp(incoming.pricingOptions, key, 'price');
    if (price) mergedListing.price = String(price);
}

function applyConfirmationPatch(mergedProvenance, field, value) {
    if (field === 'confirmField') {
        return confirmProvenanceKeys(mergedProvenance, value ? [String(value)] : []);
    }
    if (field === 'confirmAll') {
        return confirmProvenanceKeys(mergedProvenance, value === true ? Object.keys(mergedProvenance) : []);
    }
    return false;
}

function applySelectionPatch(incoming, mergedListing, field, value) {
    if (field === 'selectedTitleCandidate') {
        applyTitleCandidatePatch(incoming, mergedListing, value);
        return true;
    }
    if (field === 'selectedPricingOption') {
        applyPricingOptionPatch(incoming, mergedListing, value);
        return true;
    }
    return false;
}

function applySinglePatchField(ctx, field, value) {
    if (applyConfirmationPatch(ctx.mergedProvenance, field, value)) return;
    if (applySelectionPatch(ctx.incoming, ctx.mergedListing, field, value)) return;

    const existingListing = asObject(readProp(ctx.existing, 'listing'));
    const hasLocalValue = existingListing ? field in existingListing : false;
    ctx.mergedListing[field] = hasLocalValue ? existingListing[field] : value;
}

function mergeSinglePolledItem(incoming, existing, patch) {
    const patchObj = asObject(patch);
    if (!patchObj || Object.keys(patchObj).length === 0) return incoming;

    const mergedListing = { ...(incoming.listing || {}) };
    const mergedProvenance = { ...(incoming.provenance || {}) };
    const ctx = { incoming, existing, mergedListing, mergedProvenance };
    for (const [field, value] of Object.entries(patchObj)) {
        applySinglePatchField(ctx, field, value);
    }
    return { ...incoming, listing: mergedListing, provenance: mergedProvenance };
}

function toPatchMap(source) {
    if (source instanceof Map) return source;
    return new Map(Object.entries(source || {}));
}

export function combinePatchMaps(inflightPatches, pendingPatches) {
    const combined = new Map();
    for (const [itemId, patch] of toPatchMap(inflightPatches).entries()) {
        combined.set(itemId, { ...patch });
    }
    for (const [itemId, patch] of toPatchMap(pendingPatches).entries()) {
        combined.set(itemId, { ...(combined.get(itemId) || {}), ...patch });
    }
    return combined;
}

export function mergePolledItemsWithPending(serverItems, currentItems, pendingPatches) {
    const patchMap = toPatchMap(pendingPatches);
    const currentById = new Map((currentItems || []).map((it) => [it.id, it]));
    return (serverItems || []).map((incoming) =>
        mergeSinglePolledItem(incoming, currentById.get(incoming.id), patchMap.get(incoming.id)),
    );
}

function isValidWorkspaceItem(candidate) {
    const obj = asObject(candidate);
    return obj ? typeof obj.id === 'string' : false;
}

function isMissingText(val) {
    return !String(val || '').trim();
}

function isMissingSize(val) {
    const sizeText = String(val || '').trim();
    return !sizeText || sizeText === 'Not visible';
}

const MISSING_FIELD_CHECKS = [
    ['inspector-title-input', (l) => isMissingText(l.title)],
    ['inspector-price-input', (l) => toPositiveNumber(l.price) === null],
    ['inspector-category-input', (l) => isMissingText(l.cat)],
    ['inspector-size-input', (l) => isMissingSize(l.size)],
];

function firstMissingFieldId(item) {
    const l = asObject(readProp(item, 'listing')) || {};
    for (const [fieldId, isMissing] of MISSING_FIELD_CHECKS) {
        if (isMissing(l)) return fieldId;
    }
    return readProp(item, 'workflowStatus') === 'needs_review' ? 'inspector-title-input' : '';
}

export function findFirstMissingFieldTarget(items, preferredItem = null) {
    const itemList = items || [];
    const validPreferred = isValidWorkspaceItem(preferredItem) ? preferredItem : null;
    const candidates = validPreferred
        ? [validPreferred, ...itemList.filter((it) => it.id !== validPreferred.id)]
        : itemList;

    for (const it of candidates) {
        const fieldId = firstMissingFieldId(it);
        if (fieldId) return { item: it, fieldId };
    }
    return candidates[0] ? { item: candidates[0], fieldId: 'inspector-title-input' } : null;
}
