import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import {
    findFirstMissingFieldTarget,
    formatProvenanceClassification,
    getMarketPricingEvidence,
    getPriceTiers,
    getTitleCandidateEntries,
    INSUFFICIENT_MARKET_MESSAGE,
    mergePolledItemsWithPending,
} from './workspace-helpers.js';

const root = path.resolve(process.cwd(), 'src');
const component = fs.readFileSync(path.join(root, 'lib/components/InventoryWorkspace.svelte'), 'utf8');
const api = fs.readFileSync(path.join(root, 'lib/api.js'), 'utf8');

test('persisted inventory workspace exposes the required durable workflow controls and mobile responsive layout', () => {
    for (const marker of [
        'New inventory',
        'Upload photos',
        'Analyze &amp; create item from',
        'updateInventoryItem',
        'Approve',
        'Search',
        'Status',
        'Photo grouping saved.',
        'Suggest groups',
        'duplicate pair',
        'Re-analyze stored photos',
        'Photo Gallery & Groups',
        'Loose / Unassigned Photos',
        'Item Groups',
        'Observed Physical Facts',
        'AI Inferences (Advisory)',
        'Marketplace Pricing Evidence',
        'setItemCoverPhoto',
        'reorderItemPhoto',
        'removePhotoFromItem',
        'createItemFromSelectedPhotos',
        'moveSelectedPhotosToItem',
        'splitItemPhotos',
        'mergeItems',
        'pollBatchAnalysisResults',
        '@media (max-width: 768px)',
        'mobile-sticky-footer',
        'Review item',
        'inspector-title-input',
        'inspector-price-input',
        'inspector-category-input',
    ]) {
        assert.ok(component.includes(marker), `missing workspace marker: ${marker}`);
    }
});

test('workspace helpers extract Quick/Balanced/Premium tiers and object titleCandidates correctly', () => {
    const item = {
        pricingOptions: {
            quick_sale: { price: 16.0, label: 'Fast sale' },
            balanced: { price: 20.0, label: 'Balanced' },
            premium: { price: 29.99, label: 'Premium' },
        },
        titleCandidates: {
            search_first: { title: 'Chaps Ralph Lauren Color Block Polo Shirt M Cotton', length: 49, score: 95 },
            balanced: { title: 'Chaps Green Cotton Polo Shirt Medium', length: 36, score: 90 },
        },
    };
    const tiers = getPriceTiers(item);
    assert.equal(tiers.quickSale, 16.0);
    assert.equal(tiers.balanced, 20.0);
    assert.equal(tiers.premium, 29.99);

    const cands = getTitleCandidateEntries(item);
    assert.equal(cands.length, 2);
    assert.equal(cands[0].key, 'search_first');
    assert.equal(cands[0].length, 49);
});

test('market evidence separates AI estimate from comps and surfaces explicit insufficient-evidence state', () => {
    const fallbackItem = {
        listing: {
            price: '20.00',
            aiEstimatedPrice: 20.0,
            soldPricing: { status: 'not_configured', sampleSize: 0 },
            activeListingEstimate: { status: 'no_matches', sampleSize: 0 },
        },
    };
    const evFallback = getMarketPricingEvidence(fallbackItem);
    assert.equal(evFallback.insufficient, true);
    assert.equal(evFallback.marketMessage, INSUFFICIENT_MARKET_MESSAGE);
    assert.equal(evFallback.soldPrice, null);
    assert.equal(evFallback.activePrice, null);
    assert.equal(evFallback.aiEstimatedPrice, 20.0);

    const activeItem = {
        listing: {
            price: '20.00',
            aiEstimatedPrice: 24.0,
            soldPricing: { status: 'not_configured', sampleSize: 0 },
            activeListingEstimate: {
                status: 'ok',
                sampleSize: 25,
                lowActivePrice: 12.0,
                medianActivePrice: 20.0,
                highActivePrice: 77.88,
            },
        },
    };
    const evActive = getMarketPricingEvidence(activeItem);
    assert.equal(evActive.insufficient, false);
    assert.equal(evActive.activeCount, 25);
    assert.equal(evActive.activePrice, 20.0);
    assert.equal(evActive.activeLow, 12.0);
    assert.equal(evActive.activeHigh, 77.88);
    assert.equal(evActive.aiEstimatedPrice, 24.0);
    assert.equal(evActive.soldPrice, null);
});

test('provenance classification distinguishes observed, seller, AI inference, market evidence, and fallback', () => {
    assert.equal(formatProvenanceClassification({ source: 'observed_photo_fact' }).category, 'observed_photo_fact');
    assert.equal(formatProvenanceClassification({ source: 'seller_input', sellerConfirmed: true }).category, 'seller_input');
    assert.equal(formatProvenanceClassification({ source: 'ai_inference' }).category, 'ai_inference');
    assert.equal(formatProvenanceClassification({ source: 'market_active_comp' }).category, 'market_evidence');
    assert.equal(formatProvenanceClassification({ source: 'market_sold_comp' }).category, 'market_evidence');
    assert.equal(formatProvenanceClassification({ source: 'demand_signal' }).category, 'market_evidence');
    assert.equal(formatProvenanceClassification({ source: 'fallback_estimate' }).category, 'fallback_estimate');
});

test('mergePolledItemsWithPending preserves unsaved edits and findFirstMissingFieldTarget locates missing inputs', () => {
    const serverItems = [
        {
            id: 'item-1',
            workflowStatus: 'needs_review',
            listing: { title: 'AI Generated Title', price: '22.00', cat: '' },
            provenance: { title: { source: 'demand_signal', sellerConfirmed: false } },
        },
    ];
    const currentItems = [
        {
            id: 'item-1',
            listing: { title: 'My Unsaved Seller Title', price: '22.00', cat: '' },
        },
    ];
    const pending = new Map([['item-1', { title: 'My Unsaved Seller Title', confirmField: 'title' }]]);
    const merged = mergePolledItemsWithPending(serverItems, currentItems, pending);
    assert.equal(merged[0].listing.title, 'My Unsaved Seller Title');
    assert.equal(merged[0].provenance.title.sellerConfirmed, true);

    const target = findFirstMissingFieldTarget(merged);
    assert.equal(target.item.id, 'item-1');
    assert.equal(target.fieldId, 'inspector-category-input');

    const evtTarget = findFirstMissingFieldTarget(merged, { type: 'click', target: {} });
    assert.equal(evtTarget.item.id, 'item-1');
    assert.equal(evtTarget.fieldId, 'inspector-category-input');
});

test('inventory API contract includes seller-scoped persistence operations', () => {
    for (const marker of [
        '/api/inventory/batches',
        'registerInventoryPhotos',
        'createInventoryItem',
        'updateInventoryItemPhotos',
        'deleteInventoryItem',
        'inventoryMergeItems',
        'inventoryListBatchPhotos',
        'approveInventoryItem',
        'revokeInventoryItemApproval',
        'proposeInventoryGroups',
        'startInventoryReanalysis',
    ]) {
        assert.ok(api.includes(marker), `missing API marker: ${marker}`);
    }
});

