// Use Vite env vars in dev/build: `VITE_PUBLIC_API_URL` or `VITE_API_BASE_URL`
const PUBLIC_API_URL = import.meta.env.VITE_PUBLIC_API_URL || import.meta.env.VITE_API_BASE_URL || '';

function baseUrl() {
    const b = (PUBLIC_API_URL || '').replace(/\/+$/, '');
    if (!b) throw new Error('PUBLIC_API_URL (VITE_PUBLIC_API_URL) is not defined. Copy .env.example to .env and set it.');
    return b;
}

export async function analyzeImage(file) {
    const url = `${baseUrl()}/analyze`;
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(url, { method: 'POST', body: form });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Request failed: ${res.status}`);
    }
    return res.json();
}

export async function bulkAnalyze(files, onProgress) {
    const url = `${baseUrl()}/bulk-analyze`;
    const form = new FormData();
    for (const f of files) form.append('files', f);
    const res = await fetch(url, { method: 'POST', body: form });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Request failed: ${res.status}`);
    }
    return res.json();
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
