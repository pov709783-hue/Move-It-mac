// mp_wasm_init.js — ES module bootstrap for MediaPipe Tasks Vision API
// Loaded as <script type="module"> so it can use top-level import.
// Exposes { PoseLandmarker, FilesetResolver } on window._mpPoseLandmarkerClass
// so non-module app.js can access it.

import {
    PoseLandmarker,
    FilesetResolver,
} from 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@latest/+esm';

window._mpPoseLandmarkerClass = { PoseLandmarker, FilesetResolver };
console.log('[mp_wasm_init] MediaPipe Tasks Vision loaded ✅');
