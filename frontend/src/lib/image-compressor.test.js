import test from 'node:test';
import assert from 'node:assert/strict';

import {
    compressImageFile,
    prepareUploadBatch,
    calculateTargetBytesPerImage,
    formatBytes,
    partitionUploadChunks,
    MAX_SINGLE_PHOTO_BYTES,
    SAFE_REQUEST_LIMIT_BYTES,
    DEFAULT_MAX_DIMENSION,
} from './image-compressor.js';

// Helper to create a fake File-like object
function createMockFile({ name, size, type = 'image/jpeg', lastModified = 1700000000000 }) {
    const buffer = new Uint8Array(size);
    return new File([buffer], name, { type, lastModified });
}

// Mock decoder and renderToBlob that simulates image decoding, resizing, and quality compression
function createMockPipeline({ originalWidth = 4000, originalHeight = 3000, compressionRatioByQuality = {} }) {
    const defaultRatios = {
        0.90: 0.65,
        0.82: 0.40,
        0.75: 0.25,
        0.68: 0.18,
        ...compressionRatioByQuality,
    };

    const decoder = async (file) => {
        if (file.name.includes('corrupt')) {
            throw new Error('Image data corrupted');
        }
        return {
            width: originalWidth,
            height: originalHeight,
            draw(ctx, w, h) { },
            close() { },
        };
    };

    const renderToBlob = async (source, width, height, mimeType, quality) => {
        const ratio = defaultRatios[quality] ?? 0.3;
        // Estimated size based on pixel area and quality ratio
        const pixelScale = (width * height) / (originalWidth * originalHeight);
        const estimatedBytes = Math.max(1024, Math.round(10 * 1024 * 1024 * pixelScale * ratio));
        const blobData = new Uint8Array(estimatedBytes);
        return new Blob([blobData], { type: mimeType });
    };

    return { decoder, renderToBlob };
}

test('formatBytes formats bytes correctly', () => {
    assert.equal(formatBytes(0), '0 B');
    assert.equal(formatBytes(512), '512 B');
    assert.equal(formatBytes(1024), '1 KB');
    assert.equal(formatBytes(2.5 * 1024 * 1024), '2.5 MB');
    assert.equal(formatBytes(12 * 1024 * 1024), '12.0 MB');
});

test('calculateTargetBytesPerImage allocates request budget proportionally', () => {
    // 1 image gets up to single photo max (8MB)
    const single = calculateTargetBytesPerImage(1, { maxRequestBytes: 9 * 1024 * 1024 });
    assert.equal(single, 8 * 1024 * 1024);

    // 3 images get 3MB each (9MB / 3)
    const three = calculateTargetBytesPerImage(3, { maxRequestBytes: 9 * 1024 * 1024 });
    assert.equal(three, 3 * 1024 * 1024);

    // 4 images get 2.25MB each
    const four = calculateTargetBytesPerImage(4, { maxRequestBytes: 9 * 1024 * 1024 });
    assert.equal(four, Math.floor((9 * 1024 * 1024) / 4));
});

test('partitionUploadChunks partitions files according to grouping mode', () => {
    const files = [
        createMockFile({ name: 'f1.jpg', size: 100 }),
        createMockFile({ name: 'f2.jpg', size: 100 }),
        createMockFile({ name: 'f3.jpg', size: 100 }),
        createMockFile({ name: 'f4.jpg', size: 100 }),
        createMockFile({ name: 'f5.jpg', size: 100 }),
    ];

    const chunksMode3 = partitionUploadChunks(files, '3');
    assert.equal(chunksMode3.length, 2);
    assert.equal(chunksMode3[0].length, 3);
    assert.equal(chunksMode3[1].length, 2);

    const chunksMode2 = partitionUploadChunks(files, '2');
    assert.equal(chunksMode2.length, 3);
    assert.equal(chunksMode2[0].length, 2);
    assert.equal(chunksMode2[1].length, 2);
    assert.equal(chunksMode2[2].length, 1);

    const chunksMode1 = partitionUploadChunks(files, '1');
    assert.equal(chunksMode1.length, 5);
});

// ---------------------------------------------------------------------------
// REQUIREMENT 11 TEST SUITE
// ---------------------------------------------------------------------------

test('1. Oversized image (>10MB) is compressed before upload', async () => {
    const oversizedFile = createMockFile({
        name: 'huge_vintage_jacket.png',
        size: 11.5 * 1024 * 1024, // 11.5 MB (> 10MB Flask limit)
        type: 'image/png',
    });

    const pipeline = createMockPipeline({
        originalWidth: 4032,
        originalHeight: 3024,
    });

    const result = await compressImageFile(oversizedFile, {
        ...pipeline,
        maxSizeBytes: 8 * 1024 * 1024,
        maxDimension: DEFAULT_MAX_DIMENSION, // 2560px
    });

    assert.equal(result.compressed, true);
    assert.ok(result.compressedSize <= 8 * 1024 * 1024, `Size ${result.compressedSize} must be <= 8MB`);
    assert.ok(result.width <= 2560, `Width ${result.width} must be <= 2560`);
    assert.ok(result.height <= 2560, `Height ${result.height} must be <= 2560`);
    assert.equal(result.file.type, 'image/jpeg'); // PNG converted to JPEG
    assert.equal(result.file.size, result.compressedSize);
});

test('2. A three-photo upload stays strictly under the request limit (<=9MB < 10MB limit)', async () => {
    // 3 large files that together total 21 MB (would trigger HTTP 413 without compression)
    const threeFiles = [
        createMockFile({ name: 'front.jpg', size: 7 * 1024 * 1024 }),
        createMockFile({ name: 'back.jpg', size: 7 * 1024 * 1024 }),
        createMockFile({ name: 'tag.jpg', size: 7 * 1024 * 1024 }),
    ];

    const pipeline = createMockPipeline({
        originalWidth: 3800,
        originalHeight: 2600,
    });

    const batch = await prepareUploadBatch(threeFiles, {
        ...pipeline,
        maxRequestBytes: 9 * 1024 * 1024, // 9 MB safe multipart limit
    });

    assert.equal(batch.items.length, 3);
    assert.equal(batch.files.length, 3);

    // Each individual file must be <= targetPerFile (3MB)
    for (const item of batch.items) {
        assert.ok(
            item.compressedSize <= batch.targetPerFile,
            `File ${item.name} size ${item.compressedSize} must be <= target ${batch.targetPerFile}`
        );
    }

    // Combined total must be <= 9MB (and strictly below 10MB)
    assert.ok(
        batch.totalCompressedSize <= SAFE_REQUEST_LIMIT_BYTES,
        `Total combined size ${batch.totalCompressedSize} must be <= ${SAFE_REQUEST_LIMIT_BYTES}`
    );
    assert.ok(
        batch.totalCompressedSize < 10 * 1024 * 1024,
        `Total combined size ${batch.totalCompressedSize} must be < 10MB backend limit`
    );
});

test('3. The original filename is preserved after compression', async () => {
    const testCases = [
        'vintage_coach_handbag_1994.jpg',
        'Levi_Denim_Jacket_Front (1).PNG',
        'IMG_20261018_104428.HEIC',
        'nike-sneakers-sole-wear.webp',
    ];

    const pipeline = createMockPipeline({
        originalWidth: 3200,
        originalHeight: 2400,
    });

    for (const filename of testCases) {
        const file = createMockFile({
            name: filename,
            size: 9.2 * 1024 * 1024,
            type: filename.toLowerCase().endsWith('.webp') ? 'image/webp' : 'image/jpeg',
        });

        const result = await compressImageFile(file, {
            ...pipeline,
            maxSizeBytes: 8 * 1024 * 1024,
        });

        assert.equal(result.name, filename, 'result.name must match original filename');
        assert.equal(result.originalName, filename, 'result.originalName must match original filename');
        assert.equal(result.file.name, filename, 'result.file.name on returned File must match original filename');
    }
});

test('4. Compression failure is surfaced without making an upload request', async () => {
    const filesWithCorrupt = [
        createMockFile({ name: 'good1.jpg', size: 2 * 1024 * 1024 }),
        createMockFile({ name: 'corrupt_photo.jpg', size: 1024 * 1024 }),
        createMockFile({ name: 'good2.jpg', size: 2 * 1024 * 1024 }),
    ];

    const pipeline = createMockPipeline({
        originalWidth: 2000,
        originalHeight: 2000,
    });

    let uploadRequestMade = false;
    const fakeUploadApi = async () => {
        uploadRequestMade = true;
        return { success: true };
    };

    // Attempting to prepare the batch must throw because corrupt_photo fails decoding
    let capturedError = null;
    try {
        const prepared = await prepareUploadBatch(filesWithCorrupt, {
            ...pipeline,
        });
        await fakeUploadApi(prepared.files);
    } catch (err) {
        capturedError = err;
    }

    assert.ok(capturedError !== null, 'Compression must reject corrupt file');
    assert.match(
        capturedError.message,
        /Failed to process "corrupt_photo\.jpg"/,
        'Error message must identify the failing file'
    );
    assert.equal(uploadRequestMade, false, 'No upload request should be made when compression fails');
});
