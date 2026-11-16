import test from 'node:test';
import assert from 'node:assert/strict';

import { uploadBatchChunks, resolveUploadFailureState } from './workspace-uploader.js';

function mockFile(name) {
    return new File([new Uint8Array([1])], name, { type: 'image/jpeg' });
}

test('partial upload refreshes persisted items when a middle chunk fails', async () => {
    const files = [mockFile('one.jpg'), mockFile('two.jpg'), mockFile('three.jpg'), mockFile('four.jpg')];
    const uploaded = [];
    const refreshes = [];

    await assert.rejects(
        uploadBatchChunks({
            batchId: 'batch-1',
            files,
            groupingMode: '1',
            prepareBatch: async (chunk) => ({ files: chunk }),
            uploadChunk: async (chunk, index) => {
                if (index === 1) throw new Error('temporary upload failure');
                uploaded.push(...chunk.map((file) => file.name));
            },
            listBatchItems: async (batchId) => {
                refreshes.push(batchId);
                return uploaded.map((name, index) => ({ id: `item-${index}`, name }));
            },
        }),
        (error) => {
            assert.equal(error.message, 'temporary upload failure');
            assert.deepEqual(error.uploadRecovery.completedChunkIndexes, [0]);
            assert.equal(error.uploadRecovery.failedChunkIndex, 1);
            assert.deepEqual(error.uploadRecovery.remainingFiles.map((file) => file.name), ['two.jpg', 'three.jpg', 'four.jpg']);
            return true;
        },
    );

    assert.deepEqual(uploaded, ['one.jpg']);
    assert.deepEqual(refreshes, ['batch-1']);
});

test('successful upload returns refreshed persisted items and completed chunks', async () => {
    const files = [mockFile('one.jpg'), mockFile('two.jpg'), mockFile('three.jpg')];
    const refreshed = [{ id: 'item-1' }, { id: 'item-2' }];

    const result = await uploadBatchChunks({
        batchId: 'batch-2',
        files,
        groupingMode: '2',
        prepareBatch: async (chunk) => ({ files: chunk }),
        uploadChunk: async () => {},
        listBatchItems: async () => refreshed,
    });

    assert.deepEqual(result.items, refreshed);
    assert.deepEqual(result.completedChunkIndexes, [0, 1]);
    assert.equal(result.totalChunks, 2);
});

test('refresh-only failure clears remaining upload files and blocks duplicate retry', async () => {
    const files = [mockFile('one.jpg'), mockFile('two.jpg')];
    const previews = files.map((file) => ({ file, name: file.name }));
    let capturedError = null;

    await assert.rejects(
        uploadBatchChunks({
            batchId: 'batch-4',
            files,
            groupingMode: '1',
            prepareBatch: async (chunk) => ({ files: chunk }),
            uploadChunk: async () => {},
            listBatchItems: async () => {
                throw new Error('listBatchItems failed');
            },
        }),
        (error) => {
            capturedError = error;
            assert.deepEqual(error.uploadRecovery.completedChunkIndexes, [0, 1]);
            assert.deepEqual(error.uploadRecovery.remainingFiles, []);
            assert.equal(error.uploadRecovery.refreshFailed, true);
            return true;
        },
    );

    const next = resolveUploadFailureState({
        error: capturedError,
        uploadFiles: files,
        uploadPreviews: previews,
    });
    assert.deepEqual(next.uploadFiles, []);
    assert.deepEqual(next.uploadPreviews, []);
    assert.match(next.uploadError, /Reload the batch instead of re-uploading/i);
});

test('partial upload preserves recovery metadata when follow-up refresh fails', async () => {
    const files = [mockFile('one.jpg'), mockFile('two.jpg'), mockFile('three.jpg')];

    await assert.rejects(
        uploadBatchChunks({
            batchId: 'batch-3',
            files,
            groupingMode: '1',
            prepareBatch: async (chunk) => ({ files: chunk }),
            uploadChunk: async (_chunk, index) => {
                if (index === 1) throw new Error('chunk 1 timeout');
            },
            listBatchItems: async () => {
                throw new Error('network down during refresh');
            },
        }),
        (error) => {
            assert.equal(error.message, 'chunk 1 timeout');
            assert.deepEqual(error.uploadRecovery.completedChunkIndexes, [0]);
            assert.equal(error.uploadRecovery.failedChunkIndex, 1);
            assert.equal(error.uploadRecovery.refreshError, 'network down during refresh');
            assert.deepEqual(error.uploadRecovery.remainingFiles.map((file) => file.name), ['two.jpg', 'three.jpg']);
            return true;
        },
    );
});
