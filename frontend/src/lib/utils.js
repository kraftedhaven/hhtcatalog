/**
 * Queue & Export Utilities
 * - Price validation
 * - Error handling
 * - Retry logic
 */

const MAX_ITEMS_PER_EXPORT = 1000;
const LUXURY_BRANDS = [
    'gucci', 'louis vuitton', 'chanel', 'prada', 'fendi', 'hermes', 'versace',
    'burberry', 'coach', 'michael kors', 'tory burch', 'balenciaga', 'dior', 'saint laurent', 'ysl',
    'armani', 'valentino', 'givenchy', 'balmain', 'celine', 'mcm', 'bottega veneta'
];

/**
 * Validate all queue items have valid prices
 */
export function validateQueuePrices(queue) {
    const invalidIndex = queue.findIndex(item => {
        const price = Number(item.price || 0);
        return !Number.isFinite(price) || price <= 0;
    });
    
    if (invalidIndex >= 0) {
        return {
            valid: false,
            index: invalidIndex,
            message: `Item ${invalidIndex + 1}: Price must be a positive number.`
        };
    }
    
    if (queue.length > MAX_ITEMS_PER_EXPORT) {
        return {
            valid: false,
            message: `Maximum ${MAX_ITEMS_PER_EXPORT} items per export.`
        };
    }
    
    return { valid: true };
}

/**
 * Validate queue item for all required fields
 */
export function validateQueueItem(item) {
    const title = String(item.title || '').trim();
    if (!title || title.length > 80) {
        return "Title is required and must be 80 characters or fewer.";
    }
    
    const price = Number(item.price || 0);
    if (!Number.isFinite(price) || price <= 0) {
        return "Price must be a positive number.";
    }
    
    if (!item.cat) {
        return "Category is required.";
    }
    
    if (!item.brand) {
        return "Brand is required (use 'No Brand' if needed).";
    }
    
    return null;
}

/**
 * Get seller review warnings (luxury brands, etc.)
 */
export function getSellerReviewWarnings(item) {
    const warnings = [];
    
    if (item.notes) {
        warnings.push(item.notes);
    }
    
    const brand = String(item.brand || '').toLowerCase();
    const isLuxury = LUXURY_BRANDS.some(b => brand.includes(b));
    
    if (isLuxury) {
        if (item.madeIn && !/italy|italia|france|usa|japan|switzerland|uk|england|germany|spain|netherlands/i.test(item.madeIn)) {
            warnings.push("Luxury origin conflict: Made In label doesn't match expected origin. Verify independently before claiming authenticity.");
        }
        warnings.push("Luxury-brand item requires seller verification. Preserve exact Made In and serial number wording.");
    }
    
    return [...new Set(warnings.filter(Boolean))];
}

/**
 * Retry async function with exponential backoff
 */
export async function retryWithBackoff(fn, maxRetries = 3, baseDelayMs = 1000) {
    let lastError;
    
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
        try {
            return await fn();
        } catch (err) {
            lastError = err;
            
            // Don't retry client errors (400, 401, 403, 404, 413)
            const status = err.status || 0;
            if (status >= 400 && status < 500 && status !== 429) {
                throw err;
            }
            
            // On last attempt, throw
            if (attempt === maxRetries) {
                throw err;
            }
            
            // Exponential backoff: 1s, 2s, 4s...
            const delayMs = baseDelayMs * Math.pow(2, attempt - 1);
            await new Promise(resolve => setTimeout(resolve, Math.min(delayMs, 30000)));
        }
    }
    
    throw lastError;
}

/**
 * Category cache with localStorage
 */
const CATEGORY_CACHE_KEY = 'hht_category_cache';
const CATEGORY_CACHE_TTL = 24 * 60 * 60 * 1000; // 24 hours

export function getCachedCategorySearch(query) {
    try {
        const cache = JSON.parse(localStorage.getItem(CATEGORY_CACHE_KEY) || '{}');
        const entry = cache[query];
        
        if (entry && Date.now() - entry.timestamp < CATEGORY_CACHE_TTL) {
            return entry.data;
        }
        
        // Clean up expired entry
        if (entry) {
            delete cache[query];
            localStorage.setItem(CATEGORY_CACHE_KEY, JSON.stringify(cache));
        }
    } catch (err) {
        console.warn('Category cache read failed:', err);
    }
    
    return null;
}

export function setCategorySearchCache(query, data) {
    try {
        const cache = JSON.parse(localStorage.getItem(CATEGORY_CACHE_KEY) || '{}');
        cache[query] = {
            data,
            timestamp: Date.now()
        };
        localStorage.setItem(CATEGORY_CACHE_KEY, JSON.stringify(cache));
    } catch (err) {
        console.warn('Category cache write failed:', err);
    }
}

export function clearCategoryCache() {
    try {
        localStorage.removeItem(CATEGORY_CACHE_KEY);
    } catch (err) {
        console.warn('Category cache clear failed:', err);
    }
}

/**
 * Debounce utility
 */
export function debounce(fn, delayMs = 300) {
    let timeoutId;
    
    return function debounced(...args) {
        clearTimeout(timeoutId);
        timeoutId = setTimeout(() => fn(...args), delayMs);
    };
}

/**
 * Offline detection
 */
export function isOnline() {
    return navigator.onLine;
}

export function onOnlineStatusChange(callback) {
    const handleOnline = () => callback(true);
    const handleOffline = () => callback(false);
    
    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);
    
    return () => {
        window.removeEventListener('online', handleOnline);
        window.removeEventListener('offline', handleOffline);
    };
}
