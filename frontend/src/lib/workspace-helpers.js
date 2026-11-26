/**
 * Pure helper functions for the HHT Inventory Workspace.
 * Kept flat, small, and data-driven for high code health and direct unit testing.
 */

export const INSUFFICIENT_MARKET_MESSAGE =
    'Insufficient market evidence \u2014 seller confirmation required.';

export function toPositiveNumber(val) {
    if (val === null || val === undefined || val === '') return null;
    const n = Number(val);
    if (!Number.isFinite(n) || n <= 0) return null;
    return Number(n.toFixed(2));
}

function firstPositiveNumber(candidates) {
    for (const candidate of candidates) {
        const parsed = toPositiveNumber(candidate);
        if (parsed !== null) return parsed;
    }
    return null;
}

function firstPresentObject(...candidates) {
    for (const candidate of candidates) {
        if (candidate && typeof candidate === 'object') return candidate;
    }
    return null;
}

function firstNonEmptyString(...candidates) {
    for (const candidate of candidates) {
        if (typeof candidate === 'string' && candidate.trim()) return candidate;
    }
    return '';
}

export function getPriceTiers(item) {
    if (!item || typeof item !== 'object') {
        return { quickSale: null, balanced: null, premium: null, hasMarketTiers: false };
    }
    const opts = firstPresentObject(item.pricingOptions) || {};
    const legacy = firstPresentObject(item.pricingTiers) || {};
    const quickSale = firstPositiveNumber([
        opts.quick_sale?.price,
        item.quickSalePrice,
        legacy.quickSale?.price,
        opts.quickSale?.price,
    ]);
    const balanced = firstPositiveNumber([
        opts.balanced?.price,
        item.balancedPrice,
        legacy.balanced?.price,
    ]);
    const premium = firstPositiveNumber([
        opts.premium?.price,
        item.premiumPrice,
        legacy.premium?.price,
    ]);
    const hasTiers = [quickSale, balanced, premium].some((val) => val !== null);
    return {
        quickSale,
        balanced,
        premium,
        quickMeta: firstPresentObject(opts.quick_sale, legacy.quickSale),
        balancedMeta: firstPresentObject(opts.balanced, legacy.balanced),
        premiumMeta: firstPresentObject(opts.premium, legacy.premium),
        hasMarketTiers: hasTiers,
    };
}

function normalizeObjectCandidate(key, cand) {
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
        return normalizeObjectCandidate(key, cand);
    }
    return null;
}

export function getTitleCandidateEntries(item) {
    const raw = item?.titleCandidates;
    if (!raw || typeof raw !== 'object') return [];
    const pairs = Array.isArray(raw)
        ? raw.map((entry, idx) => [entry?.key || `option_${idx + 1}`, entry])
        : Object.entries(raw);
    return pairs.map(([key, cand]) => normalizeCandidateEntry(key, cand)).filter(Boolean);
}

function extractSoldEvidence(listing, sold) {
    const empty = { soldCount: 0, soldPrice: null, soldLow: null, soldHigh: null };
    if (!sold || sold.status !== 'ok') return empty;
    const count = Number(sold.sampleSize);
    if (!Number.isFinite(count) || count <= 0) return empty;
    const median = firstPositiveNumber([sold.medianSoldPrice, listing.soldPrice]);
    if (median === null) return empty;
    return {
        soldCount: count,
        soldPrice: median,
        soldLow: firstPositiveNumber([sold.lowSoldPrice]) ?? median,
        soldHigh: firstPositiveNumber([sold.highSoldPrice]) ?? median,
    };
}

function extractActiveEvidence(active) {
    const empty = { activeCount: 0, activePrice: null, activeLow: null, activeHigh: null };
    if (!active || typeof active !== 'object') return empty;
    const activeCount = Number(active.sampleSize);
    const activePrice = toPositiveNumber(active.medianActivePrice);
    if (!Number.isFinite(activeCount) || activeCount <= 0 || activePrice === null) {
        return empty;
    }
    return {
        activeCount,
        activePrice,
        activeLow: firstPositiveNumber([active.lowActivePrice]) ?? activePrice,
        activeHigh: firstPositiveNumber([active.highActivePrice]) ?? activePrice,
    };
}

function computeMarketStatus(listing, pricingRec, hasMarketEvidence) {
    if (listing.insufficientMarketEvidence || pricingRec.insufficientMarketEvidence) return true;
    return !hasMarketEvidence;
}

export function getMarketPricingEvidence(item) {
    const listing = firstPresentObject(item?.listing) || {};
    const pricingRec = firstPresentObject(listing.pricingRecommendation) || {};
    const soldSource = firstPresentObject(listing.soldPricing, listing.soldComparableSummary) || {};
    const soldEv = extractSoldEvidence(listing, soldSource);
    const activeEv = extractActiveEvidence(listing.activeListingEstimate);
    const hasMarketEvidence = soldEv.soldCount > 0 || activeEv.activeCount > 0;
    const insufficient = computeMarketStatus(listing, pricingRec, hasMarketEvidence);
    const fallbackMessage = insufficient ? INSUFFICIENT_MARKET_MESSAGE : '';

    return {
        ...soldEv,
        ...activeEv,
        aiEstimatedPrice: toPositiveNumber(listing.aiEstimatedPrice),
        hasMarketEvidence,
        insufficient,
        marketMessage: firstNonEmptyString(listing.marketMessage, pricingRec.marketMessage, fallbackMessage),
        pricingSource: firstNonEmptyString(listing.pricingSource, pricingRec.pricingSource),
        pricingStrategy: firstNonEmptyString(listing.pricingStrategy, pricingRec.pricingStrategy),
        pricingConfidence: firstNonEmptyString(pricingRec.pricingConfidence),
        dataFreshness: firstNonEmptyString(pricingRec.dataFreshness, listing.commerceAgent?.evidenceTimestamp),
        demand: firstPresentObject(listing.demandMetrics) || {},
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
    if (!prov || typeof prov !== 'object') return true;
    return prov.source === 'missing';
}

export function formatProvenanceClassification(prov, fieldVal = '') {
    if (isUnclassifiedProv(prov)) {
        const hasText = Boolean(fieldVal && String(fieldVal).trim());
        return hasText ? DEFAULT_AI_INFERENCE_STYLE : MISSING_PROV_STYLE;
    }
    if (prov.source === 'seller_input' || prov.sellerConfirmed === true) {
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
        const base = firstPresentObject(mergedProvenance[key]) || { source: 'seller_input', confidence: 'high' };
        mergedProvenance[key] = { ...base, sellerConfirmed: true };
    }
}

function applySelectionPatch(incoming, mergedListing, field, value) {
    if (field === 'selectedTitleCandidate') {
        const title = incoming.titleCandidates?.[value]?.title;
        if (title) mergedListing.title = title;
        return true;
    }
    if (field === 'selectedPricingOption') {
        const price = incoming.pricingOptions?.[value]?.price;
        if (price) mergedListing.price = String(price);
        return true;
    }
    return false;
}

function applySinglePatchField(incoming, existing, mergedListing, mergedProvenance, field, value) {
    if (field === 'confirmField' && value) {
        confirmProvenanceKeys(mergedProvenance, [String(value)]);
        return;
    }
    if (field === 'confirmAll' && value === true) {
        confirmProvenanceKeys(mergedProvenance, Object.keys(mergedProvenance));
        return;
    }
    if (applySelectionPatch(incoming, mergedListing, field, value)) return;

    const existingListing = existing?.listing;
    const hasLocalValue = Boolean(existingListing && field in existingListing);
    mergedListing[field] = hasLocalValue ? existingListing[field] : value;
}

function mergeSinglePolledItem(incoming, existing, patch) {
    const isNonEmptyPatch = Boolean(patch && typeof patch === 'object' && Object.keys(patch).length > 0);
    if (!isNonEmptyPatch) return incoming;

    const mergedListing = { ...(incoming.listing || {}) };
    const mergedProvenance = { ...(incoming.provenance || {}) };
    for (const [field, value] of Object.entries(patch)) {
        applySinglePatchField(incoming, existing, mergedListing, mergedProvenance, field, value);
    }
    return { ...incoming, listing: mergedListing, provenance: mergedProvenance };
}

export function mergePolledItemsWithPending(serverItems, currentItems, pendingPatches) {
    const patchMap = pendingPatches instanceof Map ? pendingPatches : new Map(Object.entries(pendingPatches || {}));
    const currentById = new Map((currentItems || []).map((it) => [it.id, it]));
    return (serverItems || []).map((incoming) =>
        mergeSinglePolledItem(incoming, currentById.get(incoming.id), patchMap.get(incoming.id)),
    );
}

function isValidWorkspaceItem(candidate) {
    if (!candidate || typeof candidate !== 'object') return false;
    return typeof candidate.id === 'string';
}

const MISSING_FIELD_CHECKS = [
    ['inspector-title-input', (l) => !String(l.title || '').trim()],
    ['inspector-price-input', (l) => toPositiveNumber(l.price) === null],
    ['inspector-category-input', (l) => !String(l.cat || '').trim()],
    ['inspector-size-input', (l) => {
        const sizeText = String(l.size || '').trim();
        return !sizeText || sizeText === 'Not visible';
    }],
];

function firstMissingFieldId(item) {
    const l = firstPresentObject(item?.listing) || {};
    for (const [fieldId, isMissing] of MISSING_FIELD_CHECKS) {
        if (isMissing(l)) return fieldId;
    }
    return item?.workflowStatus === 'needs_review' ? 'inspector-title-input' : '';
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
