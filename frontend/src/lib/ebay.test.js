import test from 'node:test';
import assert from 'node:assert/strict';

import { applyClientItemRules, CATEGORY_OPTIONS, EMPTY_ITEM, normalizeClientItem, normalizeClientPayloadItem } from './ebay.js';

test('normalizeClientItem restores ebay field defaults for outgoing payloads', () => {
    const result = normalizeClientItem({ title: 'Coach Tote', price: '49.99', cat: '169291' });
    assert.equal(result.price, 49.99);
    assert.equal(result.cid, '3000');
    assert.equal(result.cnote, '');
    assert.equal(result.notes, '');
    assert.equal(result.madeIn, '');
    assert.equal(result.serialNumber, '');
    assert.equal(result.measurements, '');
    assert.equal(result.pic, '');
    assert.equal(result.size, 'N/A - bag');
    assert.equal(result.slv, 'N/A - bag');
    assert.equal(result.nk, 'N/A - bag');
    assert.equal(result.st, 'N/A - bag');
    assert.equal(result.sea, 'All Seasons');
    assert.equal(result.occ, 'Casual');
});

test('normalizeClientItem preserves invalid price input for validation', () => {
    const result = normalizeClientItem({ title: 'Coach Tote', price: 'not-a-price', cat: '169291' });
    assert.equal(result.price, 'not-a-price');
});

test('normalizeClientPayloadItem rejects invalid price before request serialization', () => {
    assert.throws(
        () => normalizeClientPayloadItem({ title: 'Coach Tote', price: 'not-a-price', cat: '169291' }),
        /Enter a positive fixed price\./
    );
});

test('applyClientItemRules keeps explicit ebay specifics while trimming title', () => {
    const result = applyClientItemRules({
        ...EMPTY_ITEM,
        title: 'x'.repeat(90),
        cid: '4000',
        cat: '93427',
        brand: 'Nike',
        type: 'Shoes',
        style: 'Sneaker',
        vin: 'Yes (pre-1999)',
    });
    assert.equal(result.title.length, 80);
    assert.equal(result.cid, '4000');
    assert.equal(result.brand, 'Nike');
    assert.equal(result.style, 'Sneaker');
    assert.equal(result.slv, 'N/A - footwear');
    assert.equal(result.nk, 'N/A - footwear');
    assert.equal(result.vin, 'Yes (pre-1999)');
});

test('category options expose restored ebay category coverage in the form', () => {
    const labelsById = new Map(CATEGORY_OPTIONS.map((option) => [option.value, option.label]));
    assert.equal(labelsById.get('15724'), "Women's Tops / Blouses / Sports Bras / Crop Tops");
    assert.equal(labelsById.get('11484'), "Women's Sweaters / Cardigans / Men's Sweaters / Hoodies");
    assert.equal(labelsById.get('57988'), "Women's / Men's Jackets / Coats");
    assert.equal(labelsById.get('155183'), "Men's Sweatshirts / Hoodies");
});
