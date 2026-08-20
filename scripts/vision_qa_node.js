#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const { URL } = require('url');
const http = require('http');
const https = require('https');

const API_BASE = process.env.VITE_PUBLIC_API_URL || process.env.PUBLIC_API_URL || '';
const API_KEY = process.env.VISION_API_KEY || '';
const TEST_IMAGE = process.env.TEST_IMAGE || path.resolve(__dirname, '../tests/images/test1.jpg');
const LOG_PATH = process.env.VISION_LOG_PATH || path.resolve(__dirname, '../logs/vision.json');
const FALLBACK_LATENCY_MS = Number(process.env.FALLBACK_LATENCY_MS || 3000);

if (!API_BASE) {
    console.error('VITE_PUBLIC_API_URL (or PUBLIC_API_URL) not set. Export it and retry.');
    process.exit(2);
}

if (!fs.existsSync(TEST_IMAGE)) {
    console.error(`Test image not found at ${TEST_IMAGE}. Please provide a test image.`);
    process.exit(2);
}

fs.mkdirSync(path.dirname(LOG_PATH), { recursive: true });

async function run() {
    const start = Date.now();
    try {
        const res = await postFile(`${API_BASE.replace(/\/+$/, '')}/analyze`, TEST_IMAGE);
        const latency_ms = Date.now() - start;
        const record = buildRecord(res.statusCode, latency_ms, res.body);
        appendLog(record);
        console.log('Vision QA:', record.ts, 'status=', record.http_status, 'latency_ms=', record.latency_ms, 'fallback=', record.fallback_detected);
    } catch (err) {
        const timestamp = new Date().toISOString();
        appendLog({ ts: timestamp, latency_ms: null, http_status: null, fallback_detected: true, body_sample: null, errors: [String(err)] });
        console.error('Request failed', err);
        process.exitCode = 1;
    }
}

function buildRecord(statusCode, latency_ms, body) {
    const timestamp = new Date().toISOString();
    return {
        ts: timestamp,
        latency_ms,
        http_status: statusCode,
        fallback_detected: detectFallback(body, latency_ms),
        body_sample: snapshot(body),
        errors: []
    };
}

function appendLog(rec) {
    try {
        fs.appendFileSync(LOG_PATH, JSON.stringify(rec) + '\n');
    } catch (e) {
        console.error('Failed to write log:', e.message || e);
    }
}

function snapshot(obj) {
    if (!obj) return null;
    try {
        if (typeof obj === 'string') return obj.slice(0, 2000);
        return JSON.parse(JSON.stringify(obj, (k, v) => (typeof v === 'string' && v.length > 200 ? v.slice(0, 200) + '...' : v)));
    } catch {
        return String(obj).slice(0, 2000);
    }
}

function detectFallback(body, latency_ms) {
    if (!body) return true;
    if (latency_ms > FALLBACK_LATENCY_MS) return true;
    if (body.fallback === true) return true;
    if (body.provider && String(body.provider).toLowerCase().includes('fallback')) return true;
    if (body.vision) {
        const v = body.vision;
        if ((!v.labels || v.labels.length === 0) && !v.text) return true;
    }
    if (body.sku) {
        const s = body.sku;
        if ((s.title && /unknown|n\/a|none/i.test(s.title)) || !s.title) return true;
    }
    return false;
}

function postFile(urlString, filePath) {
    return new Promise((resolve, reject) => {
        try {
            const url = new URL(urlString);
            const lib = url.protocol === 'https:' ? https : http;

            const boundary = '----visionqa' + Math.random().toString(16).slice(2);
            const filename = path.basename(filePath);
            const preamble = `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${filename}"\r\nContent-Type: application/octet-stream\r\n\r\n`;
            const postamble = `\r\n--${boundary}--\r\n`;

            const fileSize = fs.statSync(filePath).size;
            const contentLength = Buffer.byteLength(preamble) + fileSize + Buffer.byteLength(postamble);

            const headers = {
                'Content-Type': `multipart/form-data; boundary=${boundary}`,
                'Content-Length': contentLength
            };
            if (API_KEY) headers['Authorization'] = `Bearer ${API_KEY}`;

            const options = {
                method: 'POST',
                hostname: url.hostname,
                port: url.port || (url.protocol === 'https:' ? 443 : 80),
                path: url.pathname + url.search,
                headers
            };

            const req = lib.request(options, (res) => {
                const chunks = [];
                res.on('data', (c) => chunks.push(c));
                res.on('end', () => {
                    const raw = Buffer.concat(chunks).toString('utf8');
                    let body = null;
                    try { body = raw ? JSON.parse(raw) : null; } catch (e) { body = raw; }
                    resolve({ statusCode: res.statusCode, body });
                });
            });

            req.on('error', (err) => reject(err));

            req.write(preamble);
            const stream = fs.createReadStream(filePath);
            stream.on('end', () => {
                req.write(postamble);
                req.end();
            });
            stream.on('error', (err) => reject(err));
            stream.pipe(req, { end: false });
        } catch (err) {
            reject(err);
        }
    });
}

run();
