import { partitionUploadChunks } from './image-compressor.js';

function validateUploadHandlers({ prepareBatch, uploadChunk, listBatchItems }) {
    if (typeof prepareBatch !== 'function') throw new TypeError('prepareBatch is required');
    if (typeof uploadChunk !== 'function') throw new TypeError('uploadChunk is required');
    if (typeof listBatchItems !== 'function') throw new TypeError('listBatchItems is required');
}

function buildUploadRecovery({ batchId, completedChunkIndexes, preparedChunkCount, chunks }) {
    const done = completedChunkIndexes.length;
    return {
        batchId,
        completedChunkIndexes: [...completedChunkIndexes],
        failedChunkIndex: done < chunks.length ? done : null,
        preparedChunkCount,
        totalChunks: chunks.length,
        remainingFiles: chunks.slice(done).flat(),
    };
}

async function uploadSinglePreparedChunk({
    chunk,
    index,
    totalChunks,
    prepareBatch,
    uploadChunk,
    onProgress,
    onPrepared,
    state,
}) {
    const prepared = await prepareBatch(chunk, {
        chunkIndex: index,
        chunkCount: totalChunks,
        onProgress,
    });
    state.preparedChunkCount += 1;
    if (onPrepared) await onPrepared(prepared, index, totalChunks);
    await uploadChunk(prepared.files, index, totalChunks, prepared);
}

async function refreshItemsSafely({
    batchId,
    listBatchItems,
    completedChunkIndexes,
    state,
    chunks,
    priorError,
}) {
    const recovery = buildUploadRecovery({
        batchId,
        completedChunkIndexes,
        preparedChunkCount: state.preparedChunkCount,
        chunks,
    });
    try {
        return await listBatchItems(batchId);
    } catch (refreshError) {
        if (priorError) {
            priorError.uploadRecovery.refreshError = refreshError?.message || 'Batch refresh failed';
            throw priorError;
        }
        refreshError.uploadRecovery = { ...recovery, refreshFailed: true };
        throw refreshError;
    }
}

/**
 * Upload photo chunks sequentially and always refresh persisted items after
 * success or partial failure. Preserves uploadRecovery metadata even if the
 * follow-up refresh request also fails.
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
    validateUploadHandlers({ prepareBatch, uploadChunk, listBatchItems });
    const chunks = partitionUploadChunks(files, groupingMode);
    const completedChunkIndexes = [];
    const state = { preparedChunkCount: 0 };
    let priorError = null;

    try {
        for (let index = 0; index < chunks.length; index += 1) {
            await uploadSinglePreparedChunk({
                chunk: chunks[index],
                index,
                totalChunks: chunks.length,
                prepareBatch,
                uploadChunk,
                onProgress,
                onPrepared,
                state,
            });
            completedChunkIndexes.push(index);
        }
    } catch (error) {
        error.uploadRecovery = buildUploadRecovery({
            batchId,
            completedChunkIndexes,
            preparedChunkCount: state.preparedChunkCount,
            chunks,
        });
        priorError = error;
    }

    const refreshedItems = await refreshItemsSafely({
        batchId,
        listBatchItems,
        completedChunkIndexes,
        state,
        chunks,
        priorError,
    });
    if (priorError) throw priorError;

    return {
        items: refreshedItems,
        completedChunkIndexes,
        totalChunks: chunks.length,
    };
}

/**
 * Derive next upload modal files, previews, and user message from an upload or refresh failure.
 * Clears pending files whenever recovery indicates all chunks were already persisted.
 */
export function resolveUploadFailureState({ error, uploadFiles = [], uploadPreviews = [] }) {
    const recovery = error?.uploadRecovery;
    if (!recovery || !Array.isArray(recovery.remainingFiles)) {
        return {
            uploadFiles,
            uploadPreviews,
            uploadError: error?.message || 'Failed to upload and group photos.',
        };
    }
    const remainingSet = new Set(recovery.remainingFiles);
    const nextPreviews = uploadPreviews.filter((preview) => remainingSet.has(preview.file));
    if (recovery.remainingFiles.length === 0) {
        return {
            uploadFiles: [],
            uploadPreviews: [],
            uploadError: `All ${recovery.totalChunks} photo batches were uploaded and saved, but refreshing the workspace failed (${error?.message || 'refresh error'}). Reload the batch instead of re-uploading these photos.`,
        };
    }
    return {
        uploadFiles: recovery.remainingFiles,
        uploadPreviews: nextPreviews,
        uploadError: `${error?.message || 'Upload interrupted.'} ${recovery.completedChunkIndexes.length} of ${recovery.totalChunks} photo batches were saved. The saved items remain visible; retry the remaining photos.`,
    };
}