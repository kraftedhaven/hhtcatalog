// Use Vite env vars in dev/build: `VITE_PUBLIC_API_URL` or `VITE_API_BASE_URL`
const PUBLIC_API_URL = import.meta.env.VITE_PUBLIC_API_URL || import.meta.env.VITE_API_BASE_URL || '';

export async function analyzeImage(file) {
    const baseUrl = (PUBLIC_API_URL || '').replace(/\/+$/, '');
    if (!baseUrl) throw new Error('PUBLIC_API_URL (VITE_PUBLIC_API_URL) is not defined. Copy .env.example to .env and set it.');
    const url = `${baseUrl}/analyze`;
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(url, { method: 'POST', body: form });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Request failed: ${res.status}`);
    }
    return res.json();
}
