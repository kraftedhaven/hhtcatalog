import { PUBLIC_API_URL } from '$env/static/public';

export async function analyzeImage(file) {
    if (!PUBLIC_API_URL) throw new Error('PUBLIC_API_URL is not defined');
    const base = PUBLIC_API_URL.replace(/\/+$/, '');
    const url = `${base}/analyze`;
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(url, { method: 'POST', body: form });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Request failed: ${res.status}`);
    }
    return res.json();
}
