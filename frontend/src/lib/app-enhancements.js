/**
 * Enhanced App.svelte Utilities
 * Import these into App.svelte to replace corresponding functions
 */

import { debounce, validateQueuePrices, validateQueueItem, getSellerReviewWarnings, isOnline, onOnlineStatusChange } from '$lib/utils.js';
import { ebayCategorySuggestions } from '$lib/api.js';

/**
 * In App.svelte <script>, add:
 * 
 * let isConnected = navigator.onLine;
 * 
 * onMount(() => {
 *   const unsubscribe = onOnlineStatusChange(online => {
 *     isConnected = online;
 *     if (online) status = '🔌 Back online.';
 *     else error = '🌐 You are offline. Reconnect to upload or export.';
 *   });
 *   return unsubscribe;
 * });
 */

// Category search with debounce
export const debouncedFindCategories = debounce(
    async function findCategories(
        categoryQuery,
        item,
        updateSuggestions,
        updateNotice,
        updateLoading,
        defaultQuery = ""
    ) {
        const search = String(categoryQuery || item.title || item.brand + " " + item.type || defaultQuery).trim();
        if (search.length < 2) {
            updateSuggestions([]);
            updateNotice("Enter at least two words or characters to search eBay categories.");
            return;
        }
        updateLoading(true);
        updateNotice("Searching live eBay category suggestions...");
        try {
            const result = await ebayCategorySuggestions(search);
            updateSuggestions(result.suggestions || []);
            updateNotice(
                result.message ||
                (result.suggestions?.length
                    ? "Choose the best matching eBay leaf category."
                    : "No category suggestions found. Try a more specific item type.")
            );
        } catch (err) {
            updateSuggestions([]);
            updateNotice(err.message || "eBay category suggestions are unavailable.");
        } finally {
            updateLoading(false);
        }
    },
    300
);

// Enhanced export queue with validation
export function validateAndExport(queue, validation) {
    const priceValidation = validateQueuePrices(queue);
    if (!priceValidation.valid) {
        return {
            error: priceValidation.message,
            index: priceValidation.index
        };
    }
    
    // Find first invalid item
    for (let index = 0; index < queue.length; index++) {
        const validationError = validateQueueItem(queue[index]);
        if (validationError) {
            return {
                error: `Queue item ${index + 1}: ${validationError}`,
                index
            };
        }
    }
    
    return { valid: true };
}

// Enhanced seller review notes using centralized luxury brands list
export function getReviewWarnings(item) {
    return getSellerReviewWarnings(item);
}

/**
 * In App.svelte, replace the exportQueue function with:
 *
 * async function exportQueue() {
 *     if (!isConnected) {
 *         error = "🌐 Offline. Reconnect before exporting.";
 *         return;
 *     }
 *     
 *     if (!queue.length) {
 *         error = "Queue is empty.";
 *         return;
 *     }
 *     
 *     const validation = validateAndExport(queue);
 *     if (!validation.valid) {
 *         error = validation.error;
 *         if (validation.index !== undefined) {
 *             item = { ...queue[validation.index] };
 *             tab = "edit";
 *         }
 *         return;
 *     }
 *     
 *     try {
 *         await downloadCSV(queue, seller);
 *     } catch (err) {
 *         error = err.message || String(err);
 *     }
 * }
 */

/**
 * In App.svelte, replace findCategories keyboard event handler with:
 * 
 * on:keydown={(event) => {
 *     if (event.key === "Enter") {
 *         event.preventDefault();
 *         debouncedFindCategories(
 *             categoryQuery,
 *             item,
 *             (sug) => (categorySuggestions = sug),
 *             (msg) => (categoryNotice = msg),
 *             (load) => (categoryLoading = load)
 *         );
 *     }
 * }}
 */

/**
 * In App.svelte, replace the sellerReviewNotes computation with:
 * 
 * $: reviewNotes = getReviewWarnings(item);
 */
