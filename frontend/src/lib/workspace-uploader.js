import { partitionUploadChunks } from './image-compressor.js';

/**
 * Upload photo chunks sequentially and always refresh persisted items after
 * success or partial failure. The caller can use the returned items to expose
 * already-uploaded work and offer retry without duplicating prior chunks.
 */
export async function uploadBatchChunks({
    batchId,
    files,
    groupingMode = '3',
    prepareBatch,
    uploadChunk,
    listBatchItems,
    onProgress,
    onPrepared,
}) {
    if (typeof prepareBatch !== 'function') throw new TypeError('prepareBatch is required');
    if (typeof uploadChunk !== 'function') throw new TypeError('uploadChunk is required');
    if (typeof listBatchItems !== 'function') throw new TypeError('listBatchItems is required');

    const chunks = partitionUploadChunks(files, groupingMode);
    const completedChunkIndexes = [];
    let preparedChunkCount = 0;
    let refreshedItems = [];

    try {
        for (let index = 0; index < chunks.length; index += 1) {
            const chunk = chunks[index];
            const prepared = await prepareBatch(chunk, {
                chunkIndex: index,
                chunkCount: chunks.length,
                onProgress,
            });
            preparedChunkCount += 1;
            if (onPrepared) await onPrepared(prepared, index, chunks.length);
            await uploadChunk(prepared.files, index, chunks.length, prepared);
            completedChunkIndexes.push(index);
        }
    } catch (error) {
        error.uploadRecovery = {
            batchId,
            completedChunkIndexes: [...completedChunkIndexes],
            failedChunkIndex: completedChunkIndexes.length,
            preparedChunkCount,
            totalChunks: chunks.length,
            remainingFiles: chunks.slice(completedChunkIndexes.length).flat(),
        };
        throw error;
    } finally {
        // This runs even when compression or a later upload fails. Any items
        // created by earlier chunks remain visible and retryable.
        refreshedItems = await listBatchItems(batchId);
    }

    return {
        items: refreshedItems,
        completedChunkIndexes,
        totalChunks: chunks.length,
    };
}
