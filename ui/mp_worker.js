// mp_worker.js — MediaPipe inference worker (off main thread)
//
// KEY ARCHITECTURE: pywebview blocks importScripts() for external CDN URLs inside workers.
// Solution: main thread fetches the vision bundle, creates a local Blob URL,
// and passes it here via postMessage. Worker calls importScripts(blobUrl) — always allowed.

const WASM_BASE = 'http://127.0.0.1:5000/mp_wasm'; // proxied through Flask — pywebview blocks CDN

let detector = null;
let busy     = false;
const T0     = Date.now();

function _ts()             { return ((Date.now() - T0) / 1000).toFixed(2) + 's'; }
function progress(msg, stage) { self.postMessage({ type: 'progress', message: msg, stage: stage || null }); }
function log(msg)         { self.postMessage({ type: 'log', message: msg }); console.log('[Worker ' + _ts() + '] ' + msg); }

function resolveGlobals() {
    if (globalThis.PoseLandmarker && globalThis.FilesetResolver)
        return { PoseLandmarker: globalThis.PoseLandmarker, FilesetResolver: globalThis.FilesetResolver };
    if (globalThis.vision?.PoseLandmarker)
        return { PoseLandmarker: globalThis.vision.PoseLandmarker, FilesetResolver: globalThis.vision.FilesetResolver };
    if (globalThis.mpTasksVision?.PoseLandmarker)
        return { PoseLandmarker: globalThis.mpTasksVision.PoseLandmarker, FilesetResolver: globalThis.mpTasksVision.FilesetResolver };
    // CJS bundle exports directly via module.exports
    const exp = globalThis.module?.exports;
    if (exp?.PoseLandmarker && exp?.FilesetResolver)
        return { PoseLandmarker: exp.PoseLandmarker, FilesetResolver: exp.FilesetResolver };
    return null;
}

async function init(modelBuffer, bundleBlobUrl) {
    try {
        // ── Stage 1: importScripts from Blob URL (always allowed, same-origin) ──
        progress('Loading MediaPipe bundle (blob URL)…', 'bundle');
        log('importScripts blob URL: ' + bundleBlobUrl.slice(0, 40) + '…');
        const t1 = Date.now();

        try {
            // CJS shim: vision_bundle.cjs uses `exports`/`module` which don't exist in workers.
            // Define them before importScripts so the bundle can populate them.
            globalThis.exports = {};
            globalThis.module  = { exports: globalThis.exports };
            importScripts(bundleBlobUrl);
            // After load, copy whatever the CJS bundle exported onto globalThis
            if (globalThis.module.exports && Object.keys(globalThis.module.exports).length > 0) {
                Object.assign(globalThis, globalThis.module.exports);
            }
            log('importScripts OK in ' + ((Date.now()-t1)/1000).toFixed(2) + 's');
        } catch (e) {
            throw new Error('importScripts(blobUrl) failed: ' + e.message);
        }

        // ── Stage 2: Resolve globals ──────────────────────────────────────────
        progress('Resolving MediaPipe globals…', 'bundle');
        const globals = resolveGlobals();

        if (!globals) {
            const candidates = Object.keys(globalThis)
                .filter(k => /pose|vision|mediapipe|fileset/i.test(k))
                .slice(0, 20);
            const expKeys = Object.keys(globalThis.module?.exports || {}).slice(0, 20);
            throw new Error('Globals not found. globalThis keys: [' + candidates.join(', ') + '] module.exports keys: [' + expKeys.join(', ') + ']');
        }
        log('Globals OK: PoseLandmarker=' + typeof globals.PoseLandmarker + ', FilesetResolver=' + typeof globals.FilesetResolver);

        const { PoseLandmarker, FilesetResolver } = globals;

        // ── Stage 3: Compile WASM ─────────────────────────────────────────────
        progress('Compiling WASM runtime… (first run: 30-90s)', 'wasm');
        log('FilesetResolver.forVisionTasks: ' + WASM_BASE);
        const t3 = Date.now();

        const visionWasm = await FilesetResolver.forVisionTasks(WASM_BASE);
        log('WASM compiled in ' + ((Date.now()-t3)/1000).toFixed(2) + 's');

        // ── Stage 4: Create detector ──────────────────────────────────────────
        progress('Creating PoseLandmarker from model buffer…', 'engine');
        log('createFromOptions (model: ' + (modelBuffer.byteLength/1024/1024).toFixed(2) + ' MB)');
        const t4 = Date.now();

        detector = await PoseLandmarker.createFromOptions(visionWasm, {
            baseOptions: {
                modelAssetBuffer: new Uint8Array(modelBuffer),
                delegate: 'CPU',
            },
            runningMode: 'VIDEO',
            numPoses: 1,
            minPoseDetectionConfidence: 0.3,
            minPosePresenceConfidence: 0.3,
            minTrackingConfidence: 0.6,
        });

        log('Detector ready in ' + ((Date.now()-t4)/1000).toFixed(2) + 's | total: ' + _ts());
        self.postMessage({ type: 'ready' });

    } catch (err) {
        console.error('[Worker] init error:', err);
        self.postMessage({ type: 'error', message: err.message });
    }
}

self.onmessage = async (e) => {
    const msg = e.data;

    if (msg.type === 'init') {
        await init(msg.modelBuffer, msg.bundleBlobUrl);
        return;
    }

    if (msg.type === 'frame') {
        if (!detector || busy) { msg.bitmap.close(); return; }
        busy = true;
        try {
            const offscreen = new OffscreenCanvas(msg.bitmap.width, msg.bitmap.height);
            offscreen.getContext('2d').drawImage(msg.bitmap, 0, 0);
            msg.bitmap.close();

            const results = detector.detectForVideo(offscreen, msg.timestamp);
            if (results.landmarks?.length > 0) {
                const lms = results.landmarks[0].map(lm => ({
                    x: lm.x, y: lm.y, z: lm.z, visibility: lm.visibility ?? 1,
                }));
                self.postMessage({ type: 'landmarks', lms, t: msg.timestamp });
            }
        } catch (err) {
            console.error('[Worker] frame error:', err);
            self.postMessage({ type: 'error', message: err.message });
        } finally {
            busy = false;
        }
        return;
    }

    if (msg.type === 'stop') {
        if (detector) { detector.close(); detector = null; }
    }
};
