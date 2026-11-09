import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(process.cwd(), 'src');
const component = fs.readFileSync(path.join(root, 'lib/components/InventoryWorkspace.svelte'), 'utf8');
const api = fs.readFileSync(path.join(root, 'lib/api.js'), 'utf8');

test('persisted inventory workspace exposes the required durable workflow controls', () => {
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
    ]) {
        assert.ok(component.includes(marker), `missing workspace marker: ${marker}`);
    }
});

test('inventory API contract includes seller-scoped persistence operations', () => {
    for (const marker of [
        '/api/inventory/batches',
        'registerInventoryPhotos',
        'createInventoryItem',
        'updateInventoryItemPhotos',
        'deleteInventoryItem',
        'inventoryListBatchPhotos',
        'approveInventoryItem',
        'revokeInventoryItemApproval',
        'proposeInventoryGroups',
        'startInventoryReanalysis',
    ]) {
        assert.ok(api.includes(marker), `missing API marker: ${marker}`);
    }
});

