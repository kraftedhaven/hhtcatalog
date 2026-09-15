export const EMPTY_ITEM = {
    title: '', price: '', cid: '3000', cnote: '', cat: '', brand: '',
    size: '', color: '', dept: '', type: '', style: '', mat: '', pat: '',
    slv: '', nk: '', sea: 'All Seasons', occ: 'Casual', st: 'Regular',
    vin: 'No', desc: '', notes: '', madeIn: '', serialNumber: '',
    measurements: '', pic: ''
};

export const CATEGORY_OPTIONS = [
    { value: '', label: 'Needs seller review' },
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
    out.price = Number.parseFloat(out.price) || 0;
    return out;
}

function isBag(item) {
    return ['169291', '169284'].includes(String(item.cat || '')) || /handbag|crossbody|clutch|backpack|tote|purse/i.test(item.type || '');
}

function isShoe(item) {
    return String(item.cat || '') === '93427' || /shoe|sneaker|boot|loafer|sandal/i.test(item.type || '');
}
