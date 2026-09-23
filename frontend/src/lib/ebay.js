import { getSellerReviewWarnings } from './utils.js';

export const EMPTY_ITEM = {
    sku: '', quantity: '1', upc: '', title: '', price: '', cid: '3000', cnote: '', cat: '', categoryName: '', brand: '',
    size: '', color: '', dept: '', type: '', style: '', mat: '', pat: '',
    model: '', theme: '', slv: '', nk: '', sea: 'All Seasons', occ: 'Casual', st: 'Regular',
    vin: 'No', desc: '', notes: '', madeIn: '', serialNumber: '',
    measurements: '', pic: '', itemSpecifics: {}
};

export const CATEGORY_OPTIONS = [
    { value: '', label: 'Search live eBay categories (hats, kids, accessories, etc.)' },
    { value: '15724', label: "Women's Tops / Blouses / Sports Bras / Crop Tops" },
    { value: '63861', label: "Women's Dresses" },
    { value: '63867', label: "Women's Jeans / Pants" },
    { value: '11484', label: "Women's Sweaters / Cardigans / Men's Sweaters / Hoodies" },
    { value: '57988', label: "Women's / Men's Jackets / Coats" },
    { value: '63866', label: "Women's Skirts" },
    { value: '185100', label: "Women's Activewear Pants / Leggings" },
    { value: '15687', label: "Men's T-Shirts" },
    { value: '11483', label: "Men's Jeans" },
    { value: '57990', label: "Men's Casual Shirts / Polos" },
    { value: '155183', label: "Men's Sweatshirts / Hoodies" },
    { value: '93427', label: "Men's Casual Shoes / Boat Shoes" },
    { value: '169291', label: 'Handbags / Clutches / Crossbodies' },
    { value: '169284', label: 'Backpacks' }
];

export function applyClientItemRules(item = {}) {
    const next = { ...EMPTY_ITEM, ...item };
    if (isBag(next)) {
        next.slv = 'N/A - bag';
        next.nk = 'N/A - bag';
        next.size = 'N/A - bag';
        next.st = 'N/A - bag';
    } else if (isShoe(next)) {
        next.slv = 'N/A - footwear';
        next.nk = 'N/A - footwear';
    }
    if (next.vin !== 'Yes (pre-1999)') next.vin = 'No';
    if (next.vin === 'Yes (pre-1999)' && !/vintage/i.test(next.title || '')) {
        next.title = `Vintage ${next.title || ''}`.trim().slice(0, 80);
    }
    next.title = String(next.title || '').slice(0, 80);
    return next;
}

export function normalizeClientItem(item = {}) {
    const out = applyClientItemRules(item);
    const parsedPrice = Number.parseFloat(out.price);
    out.price = Number.isFinite(parsedPrice) && parsedPrice > 0 ? parsedPrice : String(out.price ?? '').trim();
    return out;
}

export function normalizeClientPayloadItem(item = {}) {
    const out = applyClientItemRules(item);
    const parsedPrice = Number.parseFloat(out.price);
    if (!Number.isFinite(parsedPrice) || parsedPrice <= 0) {
        throw new Error('Enter a positive fixed price.');
    }
    out.price = parsedPrice;
    return out;
}

export function listingReadiness(item = {}) {
    const listing = applyClientItemRules(item);
    const titleLength = String(listing.title || '').trim().length;
    const isApparel = /shirt|top|dress|jean|pant|skirt|sweater|jacket|coat|shoe|sneaker|boot|sandal/i.test(listing.type || '');
    const checks = [
        {
            label: 'Search-ready title',
            complete: titleLength >= 45 && titleLength <= 80,
            hint: titleLength < 45 ? 'Add confirmed brand, item type, size, color, and key material or style.' : 'Keep titles factual and within eBay’s 80-character limit.',
        },
        { label: 'Verified eBay category', complete: Boolean(listing.cat), hint: 'Choose a matching eBay leaf category.' },
        { label: 'Brand', complete: Boolean(listing.brand), hint: 'Use No Brand or Not visible only when accurate.' },
        { label: 'Price', complete: Number.parseFloat(listing.price) > 0, hint: 'Set a positive fixed price after reviewing the market evidence.' },
        { label: 'Condition detail', complete: Boolean(listing.cnote) || !['3000', '5000', '6000'].includes(String(listing.cid)), hint: 'Describe visible wear, flaws, or why the item is new.' },
        { label: 'Core item specifics', complete: Boolean(listing.type) && Boolean(listing.color), hint: 'Confirm the item type and color buyers will filter for.' },
        { label: 'Size or dimensions', complete: Boolean(listing.size) || Boolean(listing.measurements), hint: isApparel ? 'Add label size and measurements buyers can compare.' : 'Add confirmed dimensions when they affect fit or compatibility.' },
        { label: 'Description', complete: Boolean(String(listing.desc || '').trim()), hint: 'Generate, then verify, a factual description.' },
    ];
    const completeCount = checks.filter((check) => check.complete).length;
    return {
        score: Math.round((completeCount / checks.length) * 100),
        completeCount,
        totalCount: checks.length,
        checks,
    };
}

function isBag(item) {
    return ['169291', '169284'].includes(String(item.cat || '')) || /handbag|crossbody|clutch|backpack|tote|purse/i.test(item.type || '');
}

function isShoe(item) {
    return String(item.cat || '') === '93427' || /shoe|sneaker|boot|loafer|sandal/i.test(item.type || '');
}
