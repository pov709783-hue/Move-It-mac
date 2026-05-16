console.log("MOVE-IT: app.js loaded");

// Core App State
let isTracking = false;
let isMeetingMode = false;
let limitSeconds = 30 * 60; // 30 mins default

// YOLO/MP Global State
let yoloWs = null;
let cameraStream = null;
let yoloConfig = { imgsz: 320, skip: 3 };
let captureInterval = null;
let currentMode = 'yolo'; // track which mode is active

// --- CORE NAVIGATION & UI ---

function showView(view) {
    console.log("Switching to view:", view ? view.id : "null");
    if (!view) return;
    document.querySelectorAll('.view-container').forEach(v => v.classList.remove('active'));
    view.classList.add('active');
}

function adjustTime(type, delta) {
    const input = document.getElementById(`input-${type}`);
    if (!input) return;
    let val = parseInt(input.value) + delta;
    if (val < 0) val = 0;
    if (type === 'minutes' && val > 59) val = 59;
    input.value = val.toString().padStart(2, '0');
    updateLimitFromInputs();
}

function updateLimitFromInputs() {
    const h = parseInt(document.getElementById('input-hours').value) || 0;
    const m = parseInt(document.getElementById('input-minutes').value) || 0;
    limitSeconds = (h * 3600) + (m * 60);
}

async function toggleTracking() {
    try {
        isTracking = !isTracking;
        const btn = document.getElementById('btn-toggle-tracking');
        
        if (isTracking) {
            updateLimitFromInputs();
            btn.textContent = 'STOP TRACKING';
            btn.style.background = 'linear-gradient(135deg, #f43f5e 0%, #e11d48 100%)';
            await fetch('/api/start_tracking', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ limit: limitSeconds })
            });
        } else {
            btn.textContent = 'START TRACKING';
            btn.style.background = 'linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)';
            await fetch('/api/stop_tracking', { method: 'POST' });
        }
    } catch (e) { console.error("Toggle Tracking Error:", e); }
}

async function toggleMeetingMode() {
    isMeetingMode = !isMeetingMode;
    const btn = document.getElementById('btn-toggle-meeting');
    if (btn) {
        btn.textContent = `MEETING MODE: ${isMeetingMode ? 'ON' : 'OFF'}`;
        btn.style.borderColor = isMeetingMode ? '#10b981' : '#f59e0b';
        btn.style.color = isMeetingMode ? '#10b981' : '#f59e0b';
    }
    await fetch('/api/toggle_meeting', { method: 'POST' });
}

function quitApp() {
    if (confirm("Are you sure you want to shut down Move-It?")) {
        fetch('/api/quit', { method: 'POST' });
    }
}

// --- DANCE MODE (supports both YOLO and MediaPipe) ---

async function startYoloTest(videoId = 'macarena2022', mode = 'yolo') {
    currentMode = mode;
    console.log(`Starting ${mode.toUpperCase()} Test for:`, videoId);

    const viewYolo = document.getElementById('view-yolo-test');
    if (!viewYolo) {
        console.error("view-yolo-test not found!");
        return;
    }

    // Update the header label to show which mode is running
    const headerLabel = document.getElementById('yolo-mode-label');
    if (headerLabel) {
        headerLabel.textContent = mode === 'mediapipe'
            ? '🧘 MediaPipe Pose Tracking Test'
            : '🤖 YOLO26 Pose Tracking Test';
    }

    showView(viewYolo);
    const loadingEl = document.getElementById('yolo-loading');
    if (loadingEl) loadingEl.style.display = 'flex';

    try {
        // 1. Setup Camera
        console.log("Initializing camera...");
        cameraStream = await navigator.mediaDevices.getUserMedia({
            video: { width: 1280, height: 720, frameRate: 30 }
        });

        const videoEl = document.getElementById('camera-feed');
        if (videoEl) {
            videoEl.srcObject = cameraStream;
            videoEl.style.display = 'block';
            videoEl.onloadedmetadata = () => {
                videoEl.play();
                if (loadingEl) loadingEl.style.display = 'none';
            };
        }

        // 2. Setup Reference Video
        console.log("Setting up local video...");
        const localVideo = document.getElementById('local-yolo-video');
        if (localVideo) {
            localVideo.src = '/yolo_video/' + videoId + '.mp4';
            localVideo.style.display = 'block';
            localVideo.loop = true;
            localVideo.muted = false;
            localVideo.play().catch(err => {
                console.warn("Auto-play blocked, muting...");
                localVideo.muted = true;
                localVideo.play();
            });
        }

        // 3. Inform Backend — different endpoint per mode
        console.log("Informing backend...");
        const apiEndpoint = mode === 'mediapipe' ? '/api/start_mp_test' : '/api/start_yolo_test';
        const resp = await fetch(apiEndpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ video_id: videoId })
        });

        if (!resp.ok) throw new Error(`Backend failed to start ${mode} test`);

        // 4. WebSocket — different endpoint per mode
        console.log("Opening WebSocket...");
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsEndpoint = mode === 'mediapipe' ? '/ws/keypoints_mp' : '/ws/keypoints';
        yoloWs = new WebSocket(`${protocol}//${window.location.host}${wsEndpoint}`);

        yoloWs.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === "config") {
                    yoloConfig = msg;
                    startCaptureLoop();
                    return;
                }
                handleYoloWsMessage(msg);
            } catch (e) { console.error("WS Message Error:", e); }
        };

        yoloWs.onerror = (err) => console.error("WebSocket Error:", err);
        yoloWs.onclose = () => console.warn("WebSocket Closed");

    } catch (e) {
        console.error("startYoloTest Global Catch:", e);
        alert('Failed to start dance session. Check console.');
        if (loadingEl) loadingEl.style.display = 'none';
    }
}

function startCaptureLoop() {
    if (captureInterval) clearInterval(captureInterval);
    const intervalMs = Math.round(1000 / (30 / (yoloConfig.skip + 1)));
    console.log(`[WebSocket] Starting capture at ${intervalMs}ms interval`);
    captureInterval = setInterval(sendFrameToPython, intervalMs);
}

function sendFrameToPython() {
    if (!yoloWs || yoloWs.readyState !== WebSocket.OPEN) return;
    const video = document.getElementById('camera-feed');
    if (!video || video.videoWidth === 0) return;

    const canvas = document.createElement('canvas');
    canvas.width = 640; canvas.height = 360;
    const ctx = canvas.getContext('2d');
    ctx.translate(canvas.width, 0); ctx.scale(-1, 1);
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    yoloWs.send(JSON.stringify({ type: 'frame', image: canvas.toDataURL('image/jpeg', 0.8) }));
}

function handleYoloWsMessage(data) {
    const scoreEl = document.getElementById('yolo-live-score');
    if (scoreEl) scoreEl.textContent = Math.round(data.score || 0);

    const localVideo = document.getElementById('local-yolo-video');
    if (data.status === 'playing') {
        if (localVideo && localVideo.paused) localVideo.play();
    } else if (localVideo && !localVideo.paused) {
        localVideo.pause();
    }

    drawSkeleton(data);
}

function drawSkeleton(data) {
    const canvas = document.getElementById('skeleton-canvas');
    if (!canvas || !data.keypoints) return;

    const ctx = canvas.getContext('2d');
    const rect = canvas.getBoundingClientRect();
    if (canvas.width !== rect.width || canvas.height !== rect.height) {
        canvas.width = rect.width; canvas.height = rect.height;
    }

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (data.status !== "playing" && data.message) {
        ctx.fillStyle = `rgb(${data.color[0]}, ${data.color[1]}, ${data.color[2]})`;
        ctx.font = "bold 24px Outfit";
        ctx.textAlign = "center";
        ctx.fillText(data.message, canvas.width / 2, canvas.height / 2);
    }

    const kps = data.keypoints;
    const pairs = [[5,6],[5,7],[7,9],[6,8],[8,10],[5,11],[6,12],[11,12],[11,13],[13,15],[12,14],[14,16]];

    ctx.lineWidth = 5;
    ctx.strokeStyle = data.status === "playing" ? '#22c55e' : '#ef4444';
    pairs.forEach(([i,j]) => {
        if (kps[i] && kps[j] && kps[i][2] > 0.3 && kps[j][2] > 0.3) {
            ctx.beginPath();
            ctx.moveTo(kps[i][0]*canvas.width, kps[i][1]*canvas.height);
            ctx.lineTo(kps[j][0]*canvas.width, kps[j][1]*canvas.height);
            ctx.stroke();
        }
    });

    // Draw keypoint dots
    ctx.fillStyle = 'white';
    kps.forEach(kp => {
        if (kp && kp[2] > 0.3) {
            ctx.beginPath();
            ctx.arc(kp[0]*canvas.width, kp[1]*canvas.height, 5, 0, 2*Math.PI);
            ctx.fill();
        }
    });
}

function stopYoloTest() {
    if (captureInterval) clearInterval(captureInterval);
    if (cameraStream) cameraStream.getTracks().forEach(t => t.stop());
    if (yoloWs) yoloWs.close();

    const localVideo = document.getElementById('local-yolo-video');
    if (localVideo) { localVideo.pause(); localVideo.src = ""; }

    const canvas = document.getElementById('skeleton-canvas');
    if (canvas) {
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    // Call correct finish endpoint based on mode
    const finishEndpoint = currentMode === 'mediapipe' ? '/api/mp_dance_finished' : '/api/yolo_dance_finished';
    fetch(finishEndpoint, { method: 'POST' });

    showView(document.getElementById('view-settings'));
}

// --- OTHER WORKOUT FUNCTIONS ---

function startWorkout() {
    startYoloTest('macarena2022', 'yolo');
}

function startStretching() {
    showView(document.getElementById('view-stretch'));
}

function skipWorkout() {
    fetch('/api/skip_workout', { method: 'POST' });
    showView(document.getElementById('view-settings'));
}

function repeatDance() {
    startYoloTest('macarena2022', currentMode);
}

function chooseAnotherDance() {
    showView(document.getElementById('view-settings'));
}

function backToWork() {
    showView(document.getElementById('view-settings'));
}

function showDashboard() {
    const viewDashboard = document.getElementById('view-dashboard');
    if (viewDashboard) showView(viewDashboard);
}

// --- MEDIAPIPE TASKS API — CLIENT-SIDE POSE TRACKING ---
// Uses the new @mediapipe/tasks-vision package (faster WASM runtime than legacy @mediapipe/pose)

let mpWasmStream = null;
let mpWasmRunning = false;
let mpWasmAnimFrame = null;
let mpWasmDetector = null;  // PoseLandmarker instance
let mpWasmFpsCounter = { frames: 0, last: performance.now() };
let mpWasmLastVideoTime = -1;

// Skeleton connections — MediaPipe Pose 33-landmark topology
const MP_SKELETON = {
    face:  [[0,1],[1,2],[2,3],[3,7],[0,4],[4,5],[5,6],[6,8],[9,10]],
    upper: [[11,12],[11,13],[13,15],[12,14],[14,16],[15,17],[15,19],[15,21],[16,18],[16,20],[16,22]],
    core:  [[11,23],[12,24],[23,24]],
    lower: [[23,25],[25,27],[27,29],[27,31],[24,26],[26,28],[28,30],[28,32]],
};

async function startMediaPipeWasm() {
    const viewMp = document.getElementById('view-mp-wasm');
    if (!viewMp) { console.error('view-mp-wasm not found'); return; }

    showView(viewMp);
    mpWasmRunning = true;
    mpWasmFpsCounter = { frames: 0, last: performance.now() };
    mpWasmLastVideoTime = -1;

    const statusEl  = document.getElementById('mp-wasm-status');
    const loadingEl = document.getElementById('mp-wasm-loading');
    const videoEl   = document.getElementById('mp-wasm-video');
    const canvasEl  = document.getElementById('mp-wasm-canvas');

    function syncCanvasSize() {
        const rect = canvasEl.getBoundingClientRect();
        if (canvasEl.width !== rect.width || canvasEl.height !== rect.height) {
            canvasEl.width  = rect.width;
            canvasEl.height = rect.height;
        }
    }

    try {
        // 1. Camera — use 640x480 for faster inference without noticeable quality loss
        if (statusEl) statusEl.textContent = 'Accessing camera…';
        mpWasmStream = await navigator.mediaDevices.getUserMedia({
            video: { width: 640, height: 480, frameRate: 30 }
        });
        videoEl.srcObject = mpWasmStream;
        await new Promise(res => { videoEl.onloadedmetadata = () => { videoEl.play(); res(); }; });

        // 2. Load PoseLandmarker from Tasks Vision API
        if (statusEl) statusEl.textContent = 'Loading Tasks API model…';

        // PoseLandmarker is exposed globally by mp_wasm_init.js (ES module)
        // Wait up to 10s for the module to finish loading
        let waited = 0;
        while (!window._mpPoseLandmarkerClass && waited < 10000) {
            await new Promise(r => setTimeout(r, 100));
            waited += 100;
        }
        if (!window._mpPoseLandmarkerClass) throw new Error('PoseLandmarker failed to load from Tasks API');

        const { PoseLandmarker, FilesetResolver } = window._mpPoseLandmarkerClass;

        const vision = await FilesetResolver.forVisionTasks(
            'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@latest/wasm'
        );

        mpWasmDetector = await PoseLandmarker.createFromOptions(vision, {
            baseOptions: {
                modelAssetPath: 'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task',
                delegate: 'GPU',  // GPU delegate — falls back to CPU automatically
            },
            runningMode: 'VIDEO',
            numPoses: 1,
            minPoseDetectionConfidence: 0.5,
            minPosePresenceConfidence: 0.5,
            minTrackingConfidence: 0.5,
        });

        if (loadingEl) loadingEl.style.display = 'none';
        if (statusEl) statusEl.textContent = 'Running…';

        // 3. Render loop — requestAnimationFrame for max throughput
        function renderLoop() {
            if (!mpWasmRunning) return;
            syncCanvasSize();

            if (videoEl.currentTime !== mpWasmLastVideoTime && videoEl.readyState >= 2) {
                mpWasmLastVideoTime = videoEl.currentTime;
                const results = mpWasmDetector.detectForVideo(videoEl, performance.now());
                renderMpWasmSkeleton(canvasEl, results);
                updateMpWasmFps();
            }

            mpWasmAnimFrame = requestAnimationFrame(renderLoop);
        }

        mpWasmAnimFrame = requestAnimationFrame(renderLoop);

    } catch (err) {
        console.error('MediaPipe Tasks error:', err);
        if (statusEl) { statusEl.textContent = `Error: ${err.message}`; statusEl.style.color = '#ef4444'; }
        if (loadingEl) loadingEl.style.borderColor = 'rgba(239,68,68,0.5)';
    }
}

function renderMpWasmSkeleton(canvas, results) {
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Tasks API returns results.landmarks — array of poses, each pose = array of {x,y,z,visibility}
    if (!results.landmarks || results.landmarks.length === 0) return;
    const lms = results.landmarks[0];

    function lmPx(idx) {
        // flip x because video is CSS-mirrored
        return [(1 - lms[idx].x) * canvas.width, lms[idx].y * canvas.height];
    }

    function drawConnections(pairs, color) {
        ctx.strokeStyle = color;
        ctx.lineWidth = 4;
        ctx.lineJoin = 'round';
        ctx.lineCap  = 'round';
        pairs.forEach(([i, j]) => {
            if (!lms[i] || !lms[j]) return;
            if ((lms[i].visibility ?? 1) < 0.3 || (lms[j].visibility ?? 1) < 0.3) return;
            const [x1, y1] = lmPx(i);
            const [x2, y2] = lmPx(j);
            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.stroke();
        });
    }

    drawConnections(MP_SKELETON.face,  'rgba(148,163,184,0.5)');
    drawConnections(MP_SKELETON.upper, '#22c55e');
    drawConnections(MP_SKELETON.core,  '#a855f7');
    drawConnections(MP_SKELETON.lower, '#f59e0b');

    lms.forEach((lm, idx) => {
        if ((lm.visibility ?? 1) < 0.3) return;
        const [x, y] = lmPx(idx);
        ctx.beginPath();
        ctx.arc(x, y, idx < 11 ? 4 : 6, 0, Math.PI * 2);
        ctx.fillStyle = '#06b6d4';
        ctx.fill();
        ctx.strokeStyle = '#0e7490';
        ctx.lineWidth = 1.5;
        ctx.stroke();
    });
}

function updateMpWasmFps() {
    mpWasmFpsCounter.frames++;
    const now = performance.now();
    const elapsed = now - mpWasmFpsCounter.last;
    if (elapsed >= 500) {
        const fps = Math.round((mpWasmFpsCounter.frames / elapsed) * 1000);
        const el = document.getElementById('mp-wasm-fps');
        if (el) el.textContent = fps;
        mpWasmFpsCounter.frames = 0;
        mpWasmFpsCounter.last = now;
    }
}

function stopMediaPipeWasm() {
    mpWasmRunning = false;
    if (mpWasmAnimFrame) { cancelAnimationFrame(mpWasmAnimFrame); mpWasmAnimFrame = null; }
    if (mpWasmStream)    { mpWasmStream.getTracks().forEach(t => t.stop()); mpWasmStream = null; }
    // Keep detector alive for reuse — closing/recreating it is expensive
    // if (mpWasmDetector) { mpWasmDetector.close(); mpWasmDetector = null; }

    const canvas = document.getElementById('mp-wasm-canvas');
    if (canvas) canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);

    const loadingEl = document.getElementById('mp-wasm-loading');
    if (loadingEl) { loadingEl.style.display = 'flex'; loadingEl.style.borderColor = 'rgba(6,182,212,0.3)'; }
    const statusEl = document.getElementById('mp-wasm-status');
    if (statusEl) { statusEl.textContent = 'Loading Tasks API model…'; statusEl.style.color = '#06b6d4'; }
    const fpsEl = document.getElementById('mp-wasm-fps');
    if (fpsEl) fpsEl.textContent = '0';

    showView(document.getElementById('view-settings'));
}

function activateLicense() {
    const keyInput = document.getElementById('license-input');
    const btn = document.getElementById('btn-activate');
    const errorMsg = document.getElementById('license-error');

    const key = keyInput ? keyInput.value.trim() : '';
    if (!key) {
        if (errorMsg) { errorMsg.textContent = "Please enter a valid key."; errorMsg.style.display = 'block'; }
        return;
    }

    if (btn) { btn.textContent = "ACTIVATING..."; btn.disabled = true; }
    if (errorMsg) errorMsg.style.display = 'none';

    fetch('/api/activate_license', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            if (btn) { btn.textContent = "ACTIVATED! ✅"; btn.style.background = "#10b981"; }
            setTimeout(() => showView(document.getElementById('view-settings')), 1000);
        } else {
            if (btn) { btn.textContent = "ACTIVATE"; btn.disabled = false; }
            if (errorMsg) { errorMsg.textContent = data.message || "Invalid or inactive key."; errorMsg.style.display = 'block'; }
        }
    })
    .catch(e => {
        if (btn) { btn.textContent = "ACTIVATE"; btn.disabled = false; }
        if (errorMsg) { errorMsg.textContent = "Could not connect to server."; errorMsg.style.display = 'block'; }
    });
}