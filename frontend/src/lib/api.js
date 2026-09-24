import { normalizeClientItem, normalizeClientPayloadItem } from './ebay.js';
import { retryWithBackoff, getCachedCategorySearch, setCategorySearchCache } from './utils.js';

const PUBLIC_API_URL = import.meta.env.DEV
    ? import.meta.env.VITE_PUBLIC_API_URL || import.meta.env.VITE_API_BASE_URL || ''
    : '';

function baseUrl() {
    return (PUBLIC_API_URL || '').replace(/\/+$/, '');
}

async function parseResponse(res) {
    const contentType = (res.headers?.get('content-type') || '').toLowerCase();
    const body = contentType.includes('application/json') ? await res.json() : await res.text();
    if (!res.ok) {
        const htmlError = typeof body === 'string' && /<!doctype html|<html[\s>]/i.test(body);
        const message = htmlError
            ? `The server returned a temporary application error (${res.status}). Please retry in a moment.`
            : typeof body === 'object' ? body.error : body;
        const error = new Error(message || `Request failed: ${res.status}`);
        error.status = res.status;
        if (body && typeof body === 'object') {
            error.providerFailures = body.provider_errors || body.providerFailures || [];
            error.retryAfterSeconds = body.retry_after_seconds || body.retryAfterSeconds || null;
            error.canTryAlternate = Boolean(body.can_try_alternate || body.canTryAlternate);
            error.alternateProvider = body.alternate_provider || body.alternateProvider || '';
            error.allProvidersUnavailable = Boolean(body.all_providers_unavailable || body.allProvidersUnavailable);
        }
        throw error;
    }
    return body;
}

export async function analyzeImages(files, sellerDefaults = {}, options = {}) {
    const form = new FormData();
    for (const file of files.slice(0, 3)) form.append('file', file);
    form.append('sellerDefaults', JSON.stringify({ ...sellerDefaults, analysisHints: options.analysisHints || {} }));
    if (options.tryAlternate) form.append('tryAlternate', '1');
    const res = await fetch(`${baseUrl()}/analyze`, { method: 'POST', body: form });
    const body = await parseResponse(res);
    return body.result || body;
}

export async function startNvidiaAnalysis(files, sellerDefaults = {}, analysisHints = {}) {
    const form = new FormData();
    for (const file of files.slice(0, 3)) form.append('file', file);
    form.append('sellerDefaults', JSON.stringify({ ...sellerDefaults, analysisHints }));
    const res = await fetch(`${baseUrl()}/api/nvidia/analyze/start`, { method: 'POST', body: form });
    const body = await parseResponse(res);
    return body.result || body;
}

export async function health() {
    const res = await fetch(`${baseUrl()}/health`);
    return parseResponse(res);
}

export async function downloadCSV(items, defaults = {}) {
    const res = await fetch(`${baseUrl()}/export/csv`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: items.map((item) => normalizeClientItem(item)), sellerDefaults: defaults })
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `CSV export failed: ${res.status}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `hht_ebay_listings_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

export async function downloadDraftCSV(items) {
    const res = await fetch(`${baseUrl()}/export/draft-csv`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: items.map((item) => normalizeClientItem(item)) })
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `Draft CSV export failed: ${res.status}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `hht_ebay_drafts_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// Retry-enabled eBay mutations
export async function createEbayDraft(item) {
    return retryWithBackoff(async () => {
        const res = await fetch(`${baseUrl()}/api/ebay/drafts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item: normalizeClientPayloadItem(item) })
        });
        const body = await parseResponse(res);
        return body.result || body;
    }, 3);
}

export async function updateEbayOffer(offerId, item) {
    return retryWithBackoff(async () => {
        const res = await fetch(`${baseUrl()}/api/ebay/offers/${encodeURIComponent(offerId)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item: normalizeClientPayloadItem(item) })
        });
        const body = await parseResponse(res);
        return body.result || body;
    }, 3);
}

export async function sendDraftFeed(items) {
    return retryWithBackoff(async () => {
        const res = await fetch(`${baseUrl()}/api/ebay/draft-feed`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ items: items.map((item) => normalizeClientItem(item)) })
        });
        const body = await parseResponse(res);
        return body.result || body;
    }, 3);
}

export function ebayFeedTask(taskId) {
    return commerceRequest(`/api/ebay/feed/tasks/${encodeURIComponent(taskId)}`);
}

export function ebayOAuthStatus() {
    return commerceRequest('/api/ebay/oauth/status');
}

export async function ebayOAuthStart() {
    const res = await fetch(`${baseUrl()}/api/ebay/oauth/start`);
    return parseResponse(res);
}

// Category search with caching
export async function ebayCategorySuggestions(query) {
    const cached = getCachedCategorySearch(query);
    if (cached) return cached;
    
    const result = await commerceRequest(`/api/ebay/categories?q=${encodeURIComponent(query)}`);
    setCategorySearchCache(query, result);
    return result;
}

export function ebayCategoryAspects(categoryId) {
    return commerceRequest(`/api/ebay/categories/${encodeURIComponent(categoryId)}/aspects`);
}

async function commerceRequest(path, options = {}) {
    const res = await fetch(`${baseUrl()}${path}`, options);
    const body = await parseResponse(res);
    return body.result || body;
}

export function commerceDashboard() {
    return commerceRequest('/api/commerce/dashboard');
}

export function commerceListings() {
    return commerceRequest('/api/commerce/listings');
}

export function commerceImport() {
    return commerceRequest('/api/commerce/import', { method: 'POST' });
}

export function commerceImportActive() {
    return commerceRequest('/api/commerce/import-active', { method: 'POST' });
}

export function commerceStartActiveImport() {
    return commerceRequest('/api/commerce/import-active/start', { method: 'POST' });
}

export function commerceStartPerformanceSync(days = 30, listingIds = []) {
    return commerceRequest('/api/commerce/performance/sync/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ days, listingIds }),
    });
}

export function commercePerformance() {
    return commerceRequest('/api/commerce/performance');
}

export function commerceStartFulfillmentSync(days = 90) {
    return commerceRequest('/api/commerce/performance/orders/sync/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ days }),
    });
}

export function commerceApproveRotation(actionIds) {
    return commerceRequest('/api/commerce/rotation/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ actionIds }),
    });
}

export function commerceJob(jobId) {
    return commerceRequest(`/api/commerce/jobs/${encodeURIComponent(jobId)}`);
}

export function commerceStartEnrichment(listingIds) {
    return commerceRequest('/api/commerce/enrich/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ listingIds }),
    });
}

export function commerceStartFullEnrichment(resumeFailed = false) {
    return commerceRequest('/api/commerce/enrich/full/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resumeFailed }),
    });
}

export function enrichedCatalogPage(page = 1, pageSize = 25) {
    const safePage = Math.max(1, Number(page) || 1);
    const safePageSize = Math.min(25, Math.max(1, Number(pageSize) || 25));
    return commerceRequest(`/api/catalog/enriched?page=${safePage}&pageSize=${safePageSize}`);
}

export function commerceAudit() {
    return commerceRequest('/api/commerce/audit', { method: 'POST' });
}

export function commerceStartAudit() {
    return commerceRequest('/api/commerce/audit/start', { method: 'POST' });
}

export function commerceRecommendations(status = '') {
    const query = status ? `?status=${encodeURIComponent(status)}` : '';
    return commerceRequest(`/api/commerce/recommendations${query}`);
}

export function commerceRecommendationsPage(status = '', page = 1, pageSize = 25) {
    const query = new URLSearchParams({
        page: String(Math.max(1, Number(page) || 1)),
        pageSize: String(Math.min(25, Math.max(1, Number(pageSize) || 25))),
    });
    if (status) query.set('status', status);
    return commerceRequest(`/api/commerce/recommendations/page?${query.toString()}`);
}

export function commerceApprove(recommendationId, approved) {
    return commerceRequest(`/api/commerce/recommendations/${encodeURIComponent(recommendationId)}/approve`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ approved })
    });
}

export function commerceExplain(recommendationId) {
    return commerceRequest(`/api/commerce/recommendations/${encodeURIComponent(recommendationId)}/explain`);
}

export function commerceDecision(recommendationId, decision) {
    return commerceRequest(`/api/commerce/recommendations/${encodeURIComponent(recommendationId)}/${encodeURIComponent(decision)}`, { method: 'POST' });
}

export function commerceBulkApprove(recommendationIds) {
    return commerceRequest('/api/commerce/recommendations/bulk-approve', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ recommendationIds })
    });
}

export function commerceApply(actionId) {
    return commerceRequest(`/api/commerce/actions/${encodeURIComponent(actionId)}/apply`, { method: 'POST' });
}

export function commerceRollback(actionId) {
    return commerceRequest(`/api/commerce/actions/${encodeURIComponent(actionId)}/rollback`, { method: 'POST' });
}

export function commerceHistory() {
    return commerceRequest('/api/commerce/history');
}

export function downloadJSON(data, filename = 'hht-listings-backup.json') {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}
