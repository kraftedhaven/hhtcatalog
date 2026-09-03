const PUBLIC_API_URL = import.meta.env.DEV
    ? import.meta.env.VITE_PUBLIC_API_URL || import.meta.env.VITE_API_BASE_URL || ''
    : '';

function baseUrl() {
    return (PUBLIC_API_URL || '').replace(/\/+$/, '');
}

async function parseResponse(res) {
    const contentType = res.headers.get('content-type') || '';
    const body = contentType.includes('application/json') ? await res.json() : await res.text();
    if (!res.ok) {
        const message = typeof body === 'object' ? body.error : body;
        const error = new Error(message || `Request failed: ${res.status}`);
        if (body && typeof body === 'object') {
            error.providerFailures = body.provider_errors || body.providerFailures || [];
            error.status = res.status;
        }
        throw error;
    }
    return body;
}

export async function analyzeImages(files, sellerDefaults = {}) {
    const form = new FormData();
    for (const file of files.slice(0, 3)) form.append('file', file);
    form.append('sellerDefaults', JSON.stringify(sellerDefaults));
    const res = await fetch(`${baseUrl()}/analyze`, { method: 'POST', body: form });
    const body = await parseResponse(res);
    return body.result || body;
}

export async function health() {
    const res = await fetch(`${baseUrl()}/health`);
    return parseResponse(res);
}

export function normalizeClientItem(item) {
    const out = { ...item };
    out.price = Number.parseFloat(out.price) || 0;
    out.title = String(out.title || '').slice(0, 80);
    if (isBag(out)) {
        out.slv = 'N/A - bag';
        out.nk = 'N/A - bag';
        out.size = 'N/A - bag';
        out.st = 'N/A - bag';
    } else if (isShoe(out)) {
        out.slv = 'N/A - footwear';
        out.nk = 'N/A - footwear';
    }
    if (out.vin !== 'Yes (pre-1999)') out.vin = 'No';
    if (out.vin === 'Yes (pre-1999)' && !/vintage/i.test(out.title)) {
        out.title = `Vintage ${out.title}`.slice(0, 80).trim();
    }
    return out;
}

export async function downloadCSV(items, defaults = {}) {
    const res = await fetch(`${baseUrl()}/export/csv`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items, sellerDefaults: defaults })
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `CSV export failed: ${res.status}`);
    }
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `hht_ebay_listings_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

export function downloadJSON(data, filename = 'hht-listings-backup.json') {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function isBag(item) {
    return ['169291', '169284'].includes(String(item.cat || '')) || /handbag|crossbody|clutch|backpack|tote|purse/i.test(item.type || '');
}

function isShoe(item) {
    return String(item.cat || '') === '93427' || /shoe|sneaker|boot|loafer|sandal/i.test(item.type || '');
}
