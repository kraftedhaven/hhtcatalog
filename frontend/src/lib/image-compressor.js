/**
 * Client-side Image Compressor for HHT Workspace
 *
 * Requirements:
 * - Compress JPEG, PNG, WebP, and HEIC (where supported) in the browser before upload.
 * - Keep combined multipart request safely below Flask's 10MB MAX_CONTENT_LENGTH.
 * - Target no more than 8MB per individual compressed image.
 * - Preserve adequate eBay listing quality with long edge around 2000–3000px (default 2560px).
 * - Resize only when needed; apply bounded quality reduction and stop once target size is reached.
 * - Preserve the original filename on the returned File object.
 * - Surface clear actionable errors on compression failure.
 */

export const MAX_SINGLE_PHOTO_BYTES = 8 * 1024 * 1024; // 8MB
export const SAFE_REQUEST_LIMIT_BYTES = 9 * 1024 * 1024; // 9MB total multipart body limit
export const DEFAULT_MAX_DIMENSION = 2560; // 2000–3000px long edge for eBay listing quality
export const MIN_DIMENSION = 1200;
export const DEFAULT_QUALITY_STEPS = [0.90, 0.82, 0.75, 0.68];

/**
 * Format bytes into human-readable string (e.g. "2.4 MB", "850 KB")
 */
export function formatBytes(bytes) {
    if (typeof bytes !== 'number' || isNaN(bytes) || bytes < 0) return '0 B';
    if (bytes >= 1024 * 1024) {
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }
    if (bytes >= 1024) {
        return `${Math.round(bytes / 1024)} KB`;
    }
    return `${bytes} B`;
}

/**
 * Calculate the max target bytes per image for a batch so that the combined
 * request body stays strictly under SAFE_REQUEST_LIMIT_BYTES.
 */
export function calculateTargetBytesPerImage(totalFiles, options = {}) {
    const maxRequestBytes = options.maxRequestBytes || SAFE_REQUEST_LIMIT_BYTES;
    const maxSingleFileBytes = options.maxSingleFileBytes || MAX_SINGLE_PHOTO_BYTES;
    const count = Math.max(1, totalFiles || 1);
    const divided = Math.floor(maxRequestBytes / count);
    return Math.max(250 * 1024, Math.min(maxSingleFileBytes, divided));
}

/**
 * Decode an image file using browser APIs or custom decoder.
 */
export async function decodeImage(file, options = {}) {
    if (options.decoder) {
        return options.decoder(file);
    }

    // Modern browser: try createImageBitmap first
    if (typeof createImageBitmap === 'function') {
        try {
            const bitmap = await createImageBitmap(file);
            return {
                width: bitmap.width,
                height: bitmap.height,
                draw(ctx, w, h) {
                    ctx.drawImage(bitmap, 0, 0, w, h);
                },
                close() {
                    bitmap.close?.();
                },
            };
        } catch {
            // Fall back to Image element if createImageBitmap is not supported for this format
        }
    }

    // Standard HTMLImageElement fallback
    if (typeof Image !== 'undefined') {
        return new Promise((resolve, reject) => {
            const img = new Image();
            const url = URL.createObjectURL(file);
            img.onload = () => {
                URL.revokeObjectURL(url);
                resolve({
                    width: img.naturalWidth || img.width,
                    height: img.naturalHeight || img.height,
                    draw(ctx, w, h) {
                        ctx.drawImage(img, 0, 0, w, h);
                    },
                    close() { },
                });
            };
            img.onerror = () => {
                URL.revokeObjectURL(url);
                reject(
                    new Error(
                        `Failed to decode image "${file.name}". The file may be corrupt or an unsupported format.`
                    )
                );
            };
            img.src = url;
        });
    }

    throw new Error(`No image decoder available in this environment for "${file.name}".`);
}

/**
 * Render image source to a Blob of specified MIME type and quality.
 */
export async function renderToBlob(source, width, height, mimeType, quality, options = {}) {
    if (options.renderToBlob) {
        return options.renderToBlob(source, width, height, mimeType, quality);
    }

    let canvas;
    if (typeof OffscreenCanvas !== 'undefined') {
        canvas = new OffscreenCanvas(width, height);
    } else if (typeof document !== 'undefined') {
        canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
    } else {
        throw new Error('Canvas rendering is not supported in this environment.');
    }

    const ctx = canvas.getContext('2d');
    if (!ctx) {
        throw new Error('Could not obtain 2D canvas context.');
    }

    source.draw(ctx, width, height);

    if (canvas.convertToBlob) {
        return await canvas.convertToBlob({ type: mimeType, quality });
    }

    return new Promise((resolve, reject) => {
        canvas.toBlob((blob) => {
            if (blob) resolve(blob);
            else reject(new Error('Canvas toBlob returned null.'));
        }, mimeType, quality);
    });
}

/**
 * Compress an individual image file.
 *
 * - Targets no more than maxSizeBytes (default 8MB or batch-proportional limit).
 * - Scales long edge to maxDimension (default 2560px) only when needed.
 * - Converts oversized images (PNG, HEIC, oversized JPEG) to JPEG or WebP.
 * - Iteratively steps down quality and stops as soon as target size is reached.
 * - Preserves the original file name on the returned File object.
 */
export async function compressImageFile(file, options = {}) {
    if (!file || typeof file.size !== 'number') {
        throw new Error('Invalid file provided for compression.');
    }

    const originalName = file.name || 'photo.jpg';
    const originalSize = file.size;
    const maxSizeBytes = options.maxSizeBytes || MAX_SINGLE_PHOTO_BYTES;
    const maxDimension = options.maxDimension || DEFAULT_MAX_DIMENSION;
    const qualitySteps = options.qualitySteps || DEFAULT_QUALITY_STEPS;

    // Determine target format
    let targetMime = 'image/jpeg';
    if (file.type === 'image/webp') {
        targetMime = 'image/webp';
    }

    let decoded;
    try {
        decoded = await decodeImage(file, options);
    } catch (err) {
        throw new Error(`Failed to process "${originalName}": ${err.message}`);
    }

    try {
        const origWidth = decoded.width;
        const origHeight = decoded.height;
        if (!origWidth || !origHeight) {
            throw new Error(`Invalid dimensions (${origWidth}x${origHeight}) for "${originalName}".`);
        }

        const isCompatibleFormat = file.type === 'image/jpeg' || file.type === 'image/webp';
        const isWithinDimensions = Math.max(origWidth, origHeight) <= maxDimension;

        // If file already fits requirements, bypass re-compression to preserve maximum fidelity
        if (originalSize <= maxSizeBytes && isCompatibleFormat && isWithinDimensions && !options.force) {
            return {
                file,
                originalSize,
                compressedSize: originalSize,
                originalName,
                name: originalName,
                width: origWidth,
                height: origHeight,
                compressed: false,
            };
        }

        // Calculate target dimensions: resize only when needed
        let scale = Math.min(1, maxDimension / Math.max(origWidth, origHeight));
        let width = Math.max(1, Math.round(origWidth * scale));
        let height = Math.max(1, Math.round(origHeight * scale));

        let bestBlob = null;
        let finalQuality = qualitySteps[0];

        // 1. Quality reduction loop
        for (const quality of qualitySteps) {
            finalQuality = quality;
            const blob = await renderToBlob(decoded, width, height, targetMime, quality, options);
            bestBlob = blob;
            if (blob.size <= maxSizeBytes) {
                break; // Target reached; stop quality reduction
            }
        }

        // 2. If still exceeding target at lowest quality, progressively scale down dimensions
        if (bestBlob && bestBlob.size > maxSizeBytes) {
            let currentScale = 0.85;
            for (let step = 0; step < 4; step++) {
                const stepW = Math.max(MIN_DIMENSION, Math.round(width * currentScale));
                const stepH = Math.max(MIN_DIMENSION, Math.round(height * currentScale));
                if (stepW === width && stepH === height) break;
                width = stepW;
                height = stepH;

                const blob = await renderToBlob(
                    decoded,
                    width,
                    height,
                    targetMime,
                    qualitySteps[qualitySteps.length - 1],
                    options
                );
                bestBlob = blob;
                if (blob.size <= maxSizeBytes) {
                    break;
                }
                currentScale *= 0.85;
            }
        }

        if (!bestBlob) {
            throw new Error(`Failed to generate compressed photo for "${originalName}".`);
        }

        // Preserve original filename on returned File object
        const compressedFile = new File([bestBlob], originalName, {
            type: targetMime,
            lastModified: file.lastModified || Date.now(),
        });

        return {
            file: compressedFile,
            originalSize,
            compressedSize: compressedFile.size,
            originalName,
            name: originalName,
            width,
            height,
            quality: finalQuality,
            compressed: true,
        };
    } finally {
        decoded.close?.();
    }
}

/**
 * Prepares a batch of files for upload.
 *
 * Compresses all files so that the combined multipart request body stays
 * safely below the backend MAX_CONTENT_LENGTH limit.
 *
 * If any file fails to compress, throws a clear error and rejects the batch
 * before any network upload can occur.
 */
export async function prepareUploadBatch(files, options = {}) {
    if (!files || !files.length) {
        return {
            items: [],
            files: [],
            totalOriginalSize: 0,
            totalCompressedSize: 0,
            targetPerFile: 0,
        };
    }

    const maxRequestBytes = options.maxRequestBytes || SAFE_REQUEST_LIMIT_BYTES;
    const targetPerFile = calculateTargetBytesPerImage(files.length, {
        maxRequestBytes,
        maxSingleFileBytes: options.maxSingleFileBytes || MAX_SINGLE_PHOTO_BYTES,
    });

    const items = [];
    let totalOriginalSize = 0;
    let totalCompressedSize = 0;

    for (let i = 0; i < files.length; i++) {
        const file = files[i];
        if (options.onProgress) {
            options.onProgress({
                phase: 'compressing',
                current: i + 1,
                total: files.length,
                fileName: file.name,
            });
        }

        const result = await compressImageFile(file, {
            ...options,
            maxSizeBytes: targetPerFile,
        });

        items.push(result);
        totalOriginalSize += result.originalSize;
        totalCompressedSize += result.compressedSize;
    }

    // Verify combined size
    if (totalCompressedSize > maxRequestBytes) {
        throw new Error(
            `Combined photos (${formatBytes(totalCompressedSize)}) exceed the request limit of ${formatBytes(
                maxRequestBytes
            )}. Please upload fewer photos per batch.`
        );
    }

    return {
        items,
        files: items.map((it) => it.file),
        totalOriginalSize,
        totalCompressedSize,
        targetPerFile,
    };
}

/**
 * Partition files into sequential upload chunks according to groupingMode
 * or size limits so that multi-photo groups are uploaded safely.
 */
export function partitionUploadChunks(files, groupingMode = '3') {
    if (!files || !files.length) return [];
    let step = 3;
    if (groupingMode === '1') step = 1;
    else if (groupingMode === '2') step = 2;
    else if (groupingMode === '3' || groupingMode === 'auto') step = 3;

    const chunks = [];
    for (let i = 0; i < files.length; i += step) {
        chunks.push(files.slice(i, i + step));
    }
    return chunks;
}
