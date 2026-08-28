// Optional in production because Flask serves the built frontend and API together.
const PUBLIC_API_URL = import.meta.env.VITE_PUBLIC_API_URL || import.meta.env.VITE_API_BASE_URL || '';

function baseUrl() {
    const b = (PUBLIC_API_URL || '').replace(/\/+$/, '');
    return b;
}

async function parseResponse(res) {
    const contentType = res.headers.get('content-type') || '';
    const body = contentType.includes('application/json') ? await res.json() : await res.text();
    if (!res.ok) {
        const message = typeof body === 'object' ? body.error : body;
        throw new Error(message || `Request failed: ${res.status}`);
    }
    return body;
}

export async function analyzeImage(file) {
    const url = `${baseUrl()}/analyze`;
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(url, { method: 'POST', body: form });
    return parseResponse(res);
}

export async function bulkAnalyze(files, onProgress) {
    const url = `${baseUrl()}/bulk-analyze`;
    const form = new FormData();
    for (const f of files) form.append('files', f);
    const res = await fetch(url, { method: 'POST', body: form });
    return parseResponse(res);
}

export function downloadDraftJSON(draft) {
    const blob = new Blob([JSON.stringify({ draft, reviewed: true }, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "hht_listing_draft.json";
    a.click();
    URL.revokeObjectURL(a.href);
}

export function downloadCSV(results) {
    const cols = ["filename","title","sku_code","barcode","category","condition_id",
        "list_price","floor","auction_start","accept_offer","decline_offer",
        "ebay","depop","poshmark","etsy","mercari","platform_routing",
        "seo_title","meta_description","description","demo"];
    const rows = results.map(r => {
        const sku = r.sku || {}, pr = r.pricing || {}, seo = r.seo || {};
        return [r.filename, sku.title, sku.code, sku.barcode, sku.category, sku.condition_id,
            pr.list_price, pr.floor, pr.auction_start, pr.accept_offer, pr.decline_offer,
            pr.ebay, pr.depop, pr.poshmark, pr.etsy, pr.mercari,
            (seo.platform_routing||[]).join("/"), seo.title, seo.meta_description, sku.description, r.demo];
    });
    const esc = v => {
        if (v == null) return "";
        const s = String(v).replace(/"/g, '""');
        return /[",\n]/.test(s) ? `"${s}"` : s;
    };
    const csv = [cols, ...rows].map(r => r.map(esc).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "hht_listings.csv";
    a.click();
    URL.revokeObjectURL(a.href);
}

export function downloadJSON(results) {
    const blob = new Blob([JSON.stringify({ count: results.length, results }, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "hht_listings.json";
    a.click();
    URL.revokeObjectURL(a.href);
}
