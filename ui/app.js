console.log("MOVE-IT: app.js loaded");

// Core App State
let isTracking = false;
let isMeetingMode = false;
let limitSeconds = 30 * 60; // 30 mins default
let _workoutCooldown = false; // Guard against race condition after workout finishes

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

    // Resume break countdown when returning to dashboard while tracking
    if (view.id === 'view-settings' && isTracking) {
        startBreakCountdownPoll();
    }

    // Toggle fullscreen to hide native title bar during activities
    const isFullscreen = (view.id === 'view-prompt' || view.id === 'view-dance-wasm' || view.id === 'view-stretch' || view.id === 'view-yolo-test');
    fetch('/api/set_fullscreen', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fullscreen: isFullscreen })
    }).catch(e => console.error("Fullscreen toggle failed:", e));
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
    
    // Send to backend immediately so they stay in sync
    fetch('/api/update_limit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ limit: limitSeconds })
    }).catch(e => console.error(e));
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
            startBreakCountdownPoll();
        } else {
            btn.textContent = 'START TRACKING';
            btn.style.background = 'linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)';
            await fetch('/api/stop_tracking', { method: 'POST' });
            stopBreakCountdownPoll();
        }
    } catch (e) { console.error("Toggle Tracking Error:", e); }
}

// --- BREAK COUNTDOWN POLL ---
let _breakPollInterval = null;

function startBreakCountdownPoll() {
    const container = document.getElementById('break-countdown-container');
    if (container) container.style.display = 'block';
    // Poll immediately, then every 2 seconds
    updateBreakCountdown();
    if (_breakPollInterval) clearInterval(_breakPollInterval);
    _breakPollInterval = setInterval(updateBreakCountdown, 2000);
}

function stopBreakCountdownPoll() {
    if (_breakPollInterval) { clearInterval(_breakPollInterval); _breakPollInterval = null; }
    const container = document.getElementById('break-countdown-container');
    if (container) container.style.display = 'none';
}

function updateBreakCountdown() {
    fetch('/api/app_state')
        .then(r => r.json())
        .then(state => {
            // CHECK FIRST: If backend says is_locked, switch to prompt view immediately
            // But NOT if they are already browsing the gallery, dancing, or stretching!
            // And NOT during the cooldown period right after a workout finishes (race condition guard)
            const promptActive = document.getElementById('view-prompt').classList.contains('active');
            const galleryActive = document.getElementById('view-gallery').classList.contains('active');
            const danceActive = document.getElementById('view-dance-wasm').classList.contains('active');
            const stretchActive = document.getElementById('view-stretch').classList.contains('active');
            
            if (state.is_locked && !promptActive && !galleryActive && !danceActive && !stretchActive && !_workoutCooldown) {
                console.log("LIMIT REACHED — switching to prompt view from frontend");
                showView(document.getElementById('view-prompt'));
                return;
            }
            
            // If backend confirms lock is cleared, we can safely end the cooldown early
            if (_workoutCooldown && !state.is_locked) {
                _workoutCooldown = false;
            }

            const textEl = document.getElementById('break-countdown-text');
            const barEl  = document.getElementById('break-countdown-bar');
            const container = document.getElementById('break-countdown-container');
            if (!textEl || !barEl) return;

            if (!state.is_tracking) {
                if (container) container.style.display = 'none';
                stopBreakCountdownPoll();
                return;
            }
            if (container) container.style.display = 'block';

            const remaining = Math.max(0, state.limit - state.active_time);
            const pct = state.limit > 0 ? Math.min(100, (state.active_time / state.limit) * 100) : 0;

            // Format mm:ss for countdown text
            const mins = Math.floor(remaining / 60);
            const secs = Math.floor(remaining % 60);
            textEl.textContent = `${mins.toString().padStart(2,'0')}:${secs.toString().padStart(2,'0')}`;

            // Also keep the input fields in sync if the backend limit changed (like halving after a skip)
            if (state.limit !== limitSeconds) {
                limitSeconds = state.limit;
                const limitHours = Math.floor(state.limit / 3600);
                const limitMins = Math.floor((state.limit % 3600) / 60);
                const hInput = document.getElementById('input-hours');
                const mInput = document.getElementById('input-minutes');
                if (hInput && mInput) {
                    hInput.value = limitHours.toString().padStart(2, '0');
                    mInput.value = limitMins.toString().padStart(2, '0');
                }
            }

            barEl.style.width = pct + '%';

            // Color: green → orange → red as time runs out
            if (pct < 60) {
                barEl.style.background = 'linear-gradient(90deg, #22c55e, #10b981)';
                textEl.style.color = '#22c55e';
            } else if (pct < 85) {
                barEl.style.background = 'linear-gradient(90deg, #f59e0b, #f97316)';
                textEl.style.color = '#f59e0b';
            } else {
                barEl.style.background = 'linear-gradient(90deg, #f43f5e, #e11d48)';
                textEl.style.color = '#f43f5e';
            }
        })
        .catch(e => {});
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
    const overlay = document.getElementById('quit-overlay');
    if (overlay) overlay.classList.remove('hidden');
}

function confirmQuitApp() {
    const overlay = document.getElementById('quit-overlay');
    if (overlay) overlay.classList.add('hidden');
    fetch('/api/quit', { method: 'POST' });
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
    openGallery();
}

let stretchInterval = null;
let stretchStream = null;
let currentStretchData = null;
let stretchScoreSamples = [];

async function startStretching() {
    showView(document.getElementById('view-stretch'));
    
    const videoEl = document.getElementById('stretch-video-feed');
    const canvasEl = document.getElementById('stretch-canvas');
    const targetImg = document.getElementById('current-stretch-img');
    const scoreVal = document.getElementById('stretch-current-score');
    const scoreBar = document.getElementById('stretch-score-bar');
    const timerText = document.getElementById('stretch-timer-text');
    const timerCircle = document.getElementById('stretch-timer-circle');
    const resultsOverlay = document.getElementById('stretch-results-overlay');
    
    resultsOverlay.classList.add('hidden');
    scoreVal.textContent = "0";
    scoreBar.style.width = "0%";
    timerText.textContent = "10";
    timerCircle.style.strokeDashoffset = "0";
    stretchScoreSamples = [];
    
    // Fetch stretch data
    if (!currentStretchData) {
        try {
            const res = await fetch('/api/stretches');
            currentStretchData = await res.json();
        } catch (e) {
            console.error("Failed to load stretches data", e);
            currentStretchData = {};
        }
    }

    // Setup stretch queue
    let stretchQueue = [];
    let stretchQueueIndex = 0;
    const stretchKeys = Object.keys(currentStretchData);
    if (stretchKeys.length > 0) {
        // Shuffle the stretches
        stretchQueue = stretchKeys.sort(() => 0.5 - Math.random());
    }

    let currentStretchKey = null;
    let timeLeft = 10;
    let totalTime = 10;
    let lastTimerUpdate = performance.now();
    let isMatched = false;
    let frameCounter = 0;

    function loadNextStretch() {
        if (stretchQueueIndex >= stretchQueue.length) {
            finishStretch();
            return false;
        }
        currentStretchKey = stretchQueue[stretchQueueIndex];
        if (currentStretchKey) {
            targetImg.src = `/Images/${currentStretchKey}`;
            fetch('/api/stretch_started', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ target: currentStretchKey })
            }).catch(e=>{});
        }
        timeLeft = 10;
        totalTime = 10;
        isMatched = false;
        timerText.textContent = "10";
        timerCircle.style.strokeDashoffset = "0";
        scoreVal.textContent = "0";
        scoreBar.style.width = "0%";
        lastTimerUpdate = performance.now();
        stretchQueueIndex++;
        // Update progress counter
        const progressEl = document.getElementById('stretch-progress');
        if (progressEl) progressEl.textContent = `${stretchQueueIndex}/${stretchQueue.length}`;
        return true;
    }

    if (!loadNextStretch()) return;

    // Start Webcam
    try {
        stretchStream = await navigator.mediaDevices.getUserMedia({ video: { width: 320, height: 240, frameRate: 15 } });
        videoEl.srcObject = stretchStream;
    } catch (err) {
        console.error("Stretch webcam error:", err);
    }
    
    // Check if mpWasmDetector is loaded (from dance module). If not, we load it now.
    if (!mpWasmDetector) {
        scoreVal.textContent = "Loading AI...";
        try {
            await ensureMpWasmDetector();
            scoreVal.textContent = "0";
        } catch (e) {
            console.error("Failed to load AI for stretching:", e);
            scoreVal.textContent = "AI Error";
        }
    }

    function stretchRenderLoop() {
        if (!stretchStream) return; // Exit if stream is stopped

        // Run inference and drawing at 30fps
        if (mpWasmDetector && videoEl.readyState >= 2) {
            frameCounter++;
            if (frameCounter % 2 === 0) { // Infer every 2 frames for performance
                const results = mpWasmDetector.detectForVideo(videoEl, performance.now());
                if (results && results.landmarks && results.landmarks.length > 0) {
                    // Draw skeleton
                    const ctx = canvasEl.getContext('2d');
                    canvasEl.width = videoEl.clientWidth;
                    canvasEl.height = videoEl.clientHeight;
                    ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);
                    renderMpWasmSkeleton(canvasEl, results);
                    
                    // Compare with target if we have it
                    if (currentStretchKey && currentStretchData[currentStretchKey]) {
                        const refPose = currentStretchData[currentStretchKey];
                        const liveLms = results.landmarks[0];
                        const score = calculateCosineSimilarityScore(liveLms, refPose);
                        let displayScore = Math.max(0, Math.min(100, Math.round(score)));
                        
                        // Check if hips are visible (requires standing back)
                        const hipsVisible = (liveLms[23] && liveLms[23].visibility > 0.4) || 
                                            (liveLms[24] && liveLms[24].visibility > 0.4);
                        if (!hipsVisible) {
                            displayScore = Math.min(displayScore, 30); // Cap score very low
                            ctx.fillStyle = "#ef4444";
                            ctx.font = "bold 16px Arial";
                            ctx.fillText("Please stand up! (Hips must be visible)", 10, 25);
                        } else {
                            // Check if reference requires knees
                            const refKneesRequired = (refPose[25] && refPose[25].visibility > 0.5) || 
                                                     (refPose[26] && refPose[26].visibility > 0.5);
                            if (refKneesRequired) {
                                const kneesVisible = (liveLms[25] && liveLms[25].visibility > 0.4) || 
                                                     (liveLms[26] && liveLms[26].visibility > 0.4);
                                if (!kneesVisible) {
                                    displayScore = Math.min(displayScore, 55); // Cap score below passing
                                    ctx.fillStyle = "#f59e0b";
                                    ctx.font = "bold 16px Arial";
                                    ctx.fillText("Stand back! (Knees must be visible)", 10, 25);
                                }
                            }
                        }
                        
                        // User specifically requested im5 and im6 to be smoother and have a 55 baseline
                        if (currentStretchKey === 'im5.png' || currentStretchKey === 'im6.png') {
                            displayScore = Math.round(55 + (displayScore * 0.45));
                        }
                        
                        isMatched = displayScore >= 50;
                        
                        // Only save score samples occasionally to avoid massive arrays
                        if (frameCounter % 10 === 0) stretchScoreSamples.push(displayScore);
                        
                        scoreVal.textContent = displayScore;
                        scoreBar.style.width = displayScore + "%";
                        scoreBar.style.background = displayScore > 70 ? "#10b981" : (displayScore > 40 ? "#f59e0b" : "#ef4444");
                        scoreVal.className = "score-value " + (displayScore > 70 ? "green" : (displayScore > 40 ? "orange" : "red"));
                    }
                } else {
                    isMatched = false;
                    scoreVal.textContent = "0";
                    scoreBar.style.width = "0%";
                }
            }
        }

        // Manage timer independently based on real time
        const now = performance.now();
        if (now - lastTimerUpdate >= 100) { // Check every 100ms
            if (isMatched) {
                timeLeft -= (now - lastTimerUpdate) / 1000;
            }
            lastTimerUpdate = now;
            
            if (timeLeft > 0) {
                timerText.textContent = Math.ceil(timeLeft);
                const dashoffset = 283 * (1 - (timeLeft / totalTime));
                timerCircle.style.strokeDashoffset = Math.max(0, dashoffset);
            } else if (timeLeft <= 0) {
                // Move to next stretch in the queue
                if (stretchQueueIndex >= stretchQueue.length) {
                    // All stretches done!
                    finishStretch();
                    return;
                }
                // Load next stretch (reset timer, image, scores)
                currentStretchKey = stretchQueue[stretchQueueIndex];
                targetImg.src = `/Images/${currentStretchKey}`;
                timeLeft = 10;
                totalTime = 10;
                isMatched = false;
                timerText.textContent = "10";
                timerCircle.style.strokeDashoffset = "0";
                scoreVal.textContent = "0";
                scoreBar.style.width = "0%";
                lastTimerUpdate = performance.now();
                stretchQueueIndex++;
                // Update progress counter
                const progressEl = document.getElementById('stretch-progress');
                if (progressEl) progressEl.textContent = `${stretchQueueIndex}/${stretchQueue.length}`;
            }
        }
        
        stretchInterval = requestAnimationFrame(stretchRenderLoop);
    }

    // Start the render loop
    stretchInterval = requestAnimationFrame(stretchRenderLoop);
}

function calculateCosineSimilarityScore(liveLms, refLms) {
    let dot = 0, normLive = 0, normRef = 0;
    
    // Map left/right anatomical sides so the user can act like a mirror
    const mirrorMap = {
        11: 12, 12: 11, // shoulders
        13: 14, 14: 13, // elbows
        15: 16, 16: 15, // wrists
        23: 24, 24: 23, // hips
        25: 26, 26: 25, // knees
        27: 28, 28: 27  // ankles
    };
    
    let liveHipX = 0, liveHipY = 0, refHipX = 0, refHipY = 0;
    if (liveLms[23] && liveLms[24] && refLms[23] && refLms[24]) {
        // We invert the live X coordinate because the video is horizontally flipped in CSS
        liveHipX = ((1 - liveLms[23].x) + (1 - liveLms[24].x)) / 2;
        liveHipY = (liveLms[23].y + liveLms[24].y) / 2;
        refHipX = (refLms[23].x + refLms[24].x) / 2;
        refHipY = (refLms[23].y + refLms[24].y) / 2;
    }
    
    for (let i = 11; i <= 28; i++) {
        const userIdx = mirrorMap[i] || i;
        
        // Only score points mutually visible
        if (!liveLms[userIdx] || !refLms[i] || refLms[i].visibility < 0.3 || liveLms[userIdx].visibility < 0.3) continue;
        
        // Invert user X to match the CSS mirror transform
        const lx = (1 - liveLms[userIdx].x) - liveHipX;
        const ly = liveLms[userIdx].y - liveHipY;
        const rx = refLms[i].x - refHipX;
        const ry = refLms[i].y - refHipY;
        
        dot += (lx * rx + ly * ry);
        normLive += (lx * lx + ly * ly);
        normRef += (rx * rx + ry * ry);
    }
    
    if (normLive === 0 || normRef === 0) return 0;
    const similarity = dot / (Math.sqrt(normLive) * Math.sqrt(normRef));
    
    // Map similarity (0.20 to 1.0) to score (0 to 100) - very forgiving curve
    let score = (similarity - 0.20) * (100 / 0.80);
    return Math.max(0, Math.min(100, score));
}

function finishStretch() {
    if (stretchInterval) cancelAnimationFrame(stretchInterval);
    stretchInterval = null;
    
    if (stretchStream) {
        stretchStream.getTracks().forEach(t => t.stop());
        stretchStream = null;
    }
    
    const avg = stretchScoreSamples.length > 0 ? Math.round(stretchScoreSamples.reduce((a,b)=>a+b,0)/stretchScoreSamples.length) : 0;
    
    fetch('/api/stretch_finished', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ score: avg, samples: stretchScoreSamples.length })
    });
    
    const titleEl = document.getElementById('stretch-results-title');
    const scoreEl = document.getElementById('stretch-results-score');
    if (titleEl) titleEl.textContent = avg >= 70 ? 'AMAZING!' : avg >= 45 ? 'WELL DONE!' : 'GOOD JOB!';
    if (scoreEl) scoreEl.textContent = stretchScoreSamples.length + '/8';
    
    renderStars('stretch-stars', avg);
    
    document.getElementById('stretch-results-overlay').classList.remove('hidden');
}

function backFromStretch() {
    if (stretchInterval) clearInterval(stretchInterval);
    if (stretchStream) {
        stretchStream.getTracks().forEach(t => t.stop());
        stretchStream = null;
    }
    document.getElementById('stretch-results-overlay').classList.add('hidden');
    showView(document.getElementById('view-settings'));
    fetch('/api/hide', { method: 'POST' }).catch(e => {});
    startBreakCountdownPoll();
}

window.hideResultsAndMinimize = function(type) {
    if (type === 'dance-wasm') {
        document.getElementById('dance-wasm-results-overlay').classList.add('hidden');
    } else if (type === 'dance') {
        document.getElementById('results-overlay').classList.add('hidden');
    } else {
        document.getElementById('stretch-results-overlay').classList.add('hidden');
    }
    
    // Activate cooldown guard: the backend finish endpoint may not have completed yet,
    // so the poll could see is_locked=true and instantly re-show the prompt (race condition).
    _workoutCooldown = true;
    setTimeout(() => { _workoutCooldown = false; }, 5000); // 5s safety window
    
    showView(document.getElementById('view-settings'));
    fetch('/api/hide', { method: 'POST' }).catch(e => {});
    startBreakCountdownPoll();
}

function renderStars(containerId, score) {
    const container = document.getElementById(containerId);
    if (!container) return;
    
    let starCount = Math.floor(score / 20) + 1;
    if (starCount > 5) starCount = 5;
    if (starCount < 1) starCount = 1;
    
    container.innerHTML = '';
    for(let i=0; i<5; i++) {
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.innerHTML = '<path d="M12 .587l3.668 7.568 8.332 1.151-6.064 5.828 1.48 8.279-7.416-3.967-7.417 3.967 1.481-8.279-6.064-5.828 8.332-1.151z"/>';
        if (i < starCount) {
            setTimeout(() => {
                svg.classList.add('filled');
            }, i * 150);
        }
        container.appendChild(svg);
    }
}

function skipWorkout() {
    fetch('/api/skip_workout', { method: 'POST' }).catch(e=>{});
    showView(document.getElementById('view-settings'));
    startBreakCountdownPoll();
}

function repeatDance() {
    if (ytCurrentVideoId) {
        startDanceWasm(ytCurrentVideoId, ytCurrentVideoId);
    } else {
        openGallery();
    }
}

function chooseAnotherDance() {
    openGallery();
}

function backToWork() {
    showView(document.getElementById('view-settings'));
}

async function showDashboard() {
    const viewDashboard = document.getElementById('view-dashboard');
    if (viewDashboard) showView(viewDashboard);
    
    try {
        const resp = await fetch('/api/dashboard_data');
        if (!resp.ok) throw new Error('Dashboard fetch failed');
        const data = await resp.json();
        
        // Populate the fields based on what app.py returns
        const el = (id) => document.getElementById(id);
        const today = data.today || {};
        if (el('stat-sitting')) el('stat-sitting').textContent = `${today.sitting_minutes || 0}m`;
        if (el('stat-sitting-longest')) el('stat-sitting-longest').textContent = `Longest session: ${today.longest_session_min || 0}m`;
        if (el('stat-dances')) el('stat-dances').textContent = today.dances || 0;
        if (el('stat-avg-score')) el('stat-avg-score').textContent = `Avg Score: ${today.avg_score || 0}`;
        if (el('stat-stretches')) el('stat-stretches').textContent = today.stretches || 0;
        if (el('stat-avg-stretch-score')) el('stat-avg-stretch-score').textContent = `Avg Score: ${today.avg_stretch_score || 0}`;
        if (el('stat-skips')) el('stat-skips').textContent = today.skips || 0;
        if (el('stat-calories')) el('stat-calories').textContent = today.calories || 0;
        if (el('stat-streak')) el('stat-streak').textContent = data.streak || 0;
        
        // Render Charts if Chart.js is loaded
        if (window.Chart) {
            renderCharts(data);
        }
        
        // Render Heatmap
        renderHeatmap(data.heatmap);
        
    } catch (e) {
        console.error("Dashboard error:", e);
    }
}

let charts = {};

function renderCharts(data) {
    const weekData = data.week || [];
    const labels = weekData.map(d => d.label);
    
    // 1. Weekly Activity Chart — rounded gradient bars
    const ctxWeekly = document.getElementById('chart-weekly');
    if (ctxWeekly) {
        if (charts.weekly) charts.weekly.destroy();
        charts.weekly = new Chart(ctxWeekly, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    { label: 'Sitting (min)', data: weekData.map(d => d.sitting_minutes), backgroundColor: 'rgba(100, 116, 139, 0.6)', borderRadius: 6, borderSkipped: false },
                    { label: 'Dances', data: weekData.map(d => d.dances * 5), backgroundColor: 'rgba(34, 197, 94, 0.8)', borderRadius: 6, borderSkipped: false },
                    { label: 'Stretches', data: weekData.map(d => d.stretches * 3), backgroundColor: 'rgba(59, 130, 246, 0.8)', borderRadius: 6, borderSkipped: false }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                scales: {
                    x: { stacked: true, grid: { display: false }, ticks: { color: '#94a3b8', font: { size: 12 } } },
                    y: { stacked: true, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8', font: { size: 11 } } }
                },
                plugins: {
                    legend: { labels: { color: '#cbd5e1', usePointStyle: true, pointStyle: 'circle', padding: 16, font: { size: 12 } } },
                    tooltip: { backgroundColor: 'rgba(15,23,42,0.95)', titleColor: '#e2e8f0', bodyColor: '#94a3b8', borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1, cornerRadius: 8, padding: 12 }
                }
            }
        });
    }

    // 2. Activity Breakdown Doughnut — today's split
    const ctxBreakdown = document.getElementById('chart-breakdown');
    if (ctxBreakdown) {
        if (charts.breakdown) charts.breakdown.destroy();
        const todaySitting = data.today?.sitting_minutes || 0;
        const todayDances = data.today?.dances || 0;
        const todayStretches = data.today?.stretches || 0;
        const todaySkips = data.today?.skips || 0;
        const hasData = todaySitting > 0 || todayDances > 0 || todayStretches > 0 || todaySkips > 0;
        
        charts.breakdown = new Chart(ctxBreakdown, {
            type: 'doughnut',
            data: {
                labels: hasData ? ['Sitting (min)', 'Dances', 'Stretches', 'Skips'] : ['No activity yet'],
                datasets: [{
                    data: hasData ? [todaySitting, todayDances, todayStretches, todaySkips] : [1],
                    backgroundColor: hasData
                        ? ['rgba(100, 116, 139, 0.7)', 'rgba(34, 197, 94, 0.8)', 'rgba(59, 130, 246, 0.8)', 'rgba(249, 115, 22, 0.8)']
                        : ['rgba(255,255,255,0.05)'],
                    borderColor: hasData
                        ? ['rgba(100, 116, 139, 0.3)', 'rgba(34, 197, 94, 0.3)', 'rgba(59, 130, 246, 0.3)', 'rgba(249, 115, 22, 0.3)']
                        : ['rgba(255,255,255,0.08)'],
                    borderWidth: 2,
                    hoverOffset: 8
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '65%',
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: { color: '#cbd5e1', usePointStyle: true, pointStyle: 'circle', padding: 14, font: { size: 12 } }
                    },
                    tooltip: { backgroundColor: 'rgba(15,23,42,0.95)', titleColor: '#e2e8f0', bodyColor: '#94a3b8', cornerRadius: 8, padding: 12 }
                }
            }
        });
    }

    // 3. Recent Dance Scores — gradient area chart
    const ctxScores = document.getElementById('chart-scores');
    const scoreData = data.score_history || [];
    if (ctxScores) {
        if (charts.scores) charts.scores.destroy();
        
        const hasScores = scoreData.length > 0;
        const scoreLabels = hasScores ? scoreData.map(d => {
            const dateObj = new Date(d.timestamp);
            if (!isNaN(dateObj)) {
                // Returns e.g. "2:30 PM" or "14:30"
                return dateObj.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            }
            return d.timestamp;
        }) : ['No dances yet'];
        const scoreValues = hasScores ? scoreData.map(d => d.score) : [0];
        
        charts.scores = new Chart(ctxScores, {
            type: 'line',
            data: {
                labels: scoreLabels,
                datasets: [{
                    label: 'Score',
                    data: scoreValues,
                    borderColor: '#22c55e',
                    borderWidth: 2.5,
                    backgroundColor: (ctx) => {
                        const chart = ctx.chart;
                        const { ctx: c, chartArea } = chart;
                        if (!chartArea) return 'rgba(34,197,94,0.1)';
                        const gradient = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                        gradient.addColorStop(0, 'rgba(34, 197, 94, 0.35)');
                        gradient.addColorStop(1, 'rgba(34, 197, 94, 0.02)');
                        return gradient;
                    },
                    fill: true,
                    tension: 0.4,
                    pointBackgroundColor: '#22c55e',
                    pointBorderColor: '#0f172a',
                    pointBorderWidth: 2,
                    pointRadius: hasScores ? 4 : 0,
                    pointHoverRadius: 7
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                scales: {
                    y: { min: 0, max: 100, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8', font: { size: 11 }, stepSize: 20 } },
                    x: { grid: { display: false }, ticks: { color: '#94a3b8', font: { size: 10 }, maxRotation: 0, maxTicksLimit: 8 } }
                },
                plugins: {
                    legend: { display: false },
                    tooltip: { backgroundColor: 'rgba(15,23,42,0.95)', titleColor: '#e2e8f0', bodyColor: '#94a3b8', cornerRadius: 8, padding: 12,
                        callbacks: { label: (ctx) => 'Score: ' + ctx.parsed.y + '%' }
                    }
                }
            }
        });
    }

    // 4. Calories Chart — gradient pink area chart
    const ctxCalories = document.getElementById('chart-calories');
    if (ctxCalories) {
        if (charts.calories) charts.calories.destroy();
        const calData = weekData.map(d => d.calories);
        const hasCalories = calData.some(v => v > 0);
        
        charts.calories = new Chart(ctxCalories, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Calories',
                    data: calData,
                    borderColor: '#ec4899',
                    borderWidth: 2.5,
                    backgroundColor: (ctx) => {
                        const chart = ctx.chart;
                        const { ctx: c, chartArea } = chart;
                        if (!chartArea) return 'rgba(236,72,153,0.1)';
                        const gradient = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                        gradient.addColorStop(0, 'rgba(236, 72, 153, 0.35)');
                        gradient.addColorStop(1, 'rgba(236, 72, 153, 0.02)');
                        return gradient;
                    },
                    fill: true,
                    tension: 0.4,
                    pointBackgroundColor: '#ec4899',
                    pointBorderColor: '#0f172a',
                    pointBorderWidth: 2,
                    pointRadius: 4,
                    pointHoverRadius: 7
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                scales: {
                    y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8', font: { size: 11 } } },
                    x: { grid: { display: false }, ticks: { color: '#94a3b8', font: { size: 12 } } }
                },
                plugins: {
                    legend: { display: false },
                    tooltip: { backgroundColor: 'rgba(15,23,42,0.95)', titleColor: '#e2e8f0', bodyColor: '#94a3b8', cornerRadius: 8, padding: 12,
                        callbacks: { label: (ctx) => ctx.parsed.y + ' kcal' }
                    }
                }
            }
        });
    }
}

function renderHeatmap(heatmapData) {
    const container = document.getElementById('heatmap-container');
    if (!container || !heatmapData) return;
    container.innerHTML = '';
    
    heatmapData.forEach(day => {
        const block = document.createElement('div');
        block.className = `heat-box level-${day.level}`;
        block.textContent = day.day;
        block.title = `${day.date}: ${day.dances} dances, ${day.stretches} stretches, ${day.sitting_min}m sitting`;
        container.appendChild(block);
    });
}

// --- GALLERY FUNCTIONS ---
let songsData = [];

async function populateGallery() {
    try {
        const response = await fetch('/api/songs');
        if (!response.ok) throw new Error('Failed to load songs');
        songsData = await response.json();
        
        const grid = document.getElementById('gallery-grid');
        if (!grid) return;
        
        grid.innerHTML = '';
        songsData.forEach(song => {
            const card = document.createElement('div');
            card.className = 'gallery-card glass-panel';
            card.style.cursor = 'pointer';
            card.innerHTML = `
                <img src="${song.thumbnail}" alt="${song.title}" style="width:100%; border-radius:8px;">
                <div class="card-info" style="padding:10px; text-align:center;">
                    <h3 style="margin:0; font-size:1.1rem;">${song.title}</h3>
                </div>
            `;
            card.onclick = () => {
                window._selectedSong = song;
                startDanceWasm(song.id, song.youtubeId);
            };
            grid.appendChild(card);
        });
    } catch (e) {
        console.error("Error loading gallery:", e);
    }
}

function openGallery() {
    populateGallery();
    showView(document.getElementById('view-gallery'));
}

let _selectedSong = null;

function filterGallery() {
    const query = (document.getElementById('search-input')?.value || '').toLowerCase();
    const cards = document.querySelectorAll('.gallery-card');
    cards.forEach((card, i) => {
        const song = songsData[i];
        if (!song) return;
        card.style.display = song.title.toLowerCase().includes(query) ? '' : 'none';
    });
}

function selectRandomSong() {
    if (!songsData.length) return;
    const song = songsData[Math.floor(Math.random() * songsData.length)];
    window._selectedSong = song;
    startDanceWasm(song.id, song.youtubeId);
}

function confirmSong() {
    if (_selectedSong) {
        closeSongModal();
        startDanceWasm(_selectedSong.id, _selectedSong.youtubeId);
    }
}

function closeSongModal() {
    const modal = document.getElementById('song-modal');
    if (modal) modal.classList.add('hidden');
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

async function ensureMpWasmDetector() {
    if (mpWasmDetector) return;
    
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
}

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
            video: { width: 320, height: 240, frameRate: 30 }
        });
        videoEl.srcObject = mpWasmStream;
        await new Promise(res => { videoEl.onloadedmetadata = () => { videoEl.play(); res(); }; });

        // 2. Load PoseLandmarker from Tasks Vision API
        if (statusEl) statusEl.textContent = 'Loading Tasks API model…';

        await ensureMpWasmDetector();

        if (loadingEl) loadingEl.style.display = 'none';
        if (statusEl) statusEl.textContent = 'Running…';

        // 3. Render loop — inference every 2nd frame, draw every frame
        let _frameCount = 0;
        let _lastLms = null;
        function renderLoop() {
            if (!mpWasmRunning) return;
            syncCanvasSize();

            if (videoEl.currentTime !== mpWasmLastVideoTime && videoEl.readyState >= 2) {
                mpWasmLastVideoTime = videoEl.currentTime;
                _frameCount++;
                if (_frameCount % 2 === 0) {
                    // Run inference on every 2nd frame
                    const results = mpWasmDetector.detectForVideo(videoEl, performance.now());
                    _lastLms = results;
                }
                // Always draw — uses latest result even on skipped frames
                if (_lastLms) {
                    renderMpWasmSkeleton(canvasEl, _lastLms);
                    updateMpWasmFps(); // count every draw, not every inference
                }
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
    
    // Explicitly tell backend to finish stretch and resume timer
    fetch('/api/stretch_finished', { method: 'POST' }).catch(console.error);
    startBreakCountdownPoll();
}

// ═══════════════════════════════════════════════════════════════════════════════
// DANCE WASM MODE — side-by-side YouTube + camera with full JS-side scoring
// ═══════════════════════════════════════════════════════════════════════════════

// ─── YouTube IFrame Player ───────────────────────────────────────────────────
let ytPlayer       = null;   // YT.Player instance
let ytPlayerReady  = false;  // true once onReady fires
let ytCurrentVideoId = null; // currently loaded YouTube video ID

// Extract YouTube video ID from various URL formats
function extractYouTubeId(input) {
    if (!input) return null;
    input = input.trim();
    // Already a bare ID (11 chars, alphanumeric + dash/underscore)
    if (/^[\w-]{11}$/.test(input)) return input;
    // Standard URL: youtube.com/watch?v=ID
    let m = input.match(/[?&]v=([\w-]{11})/);
    if (m) return m[1];
    // Short URL: youtu.be/ID
    m = input.match(/youtu\.be\/([\w-]{11})/);
    if (m) return m[1];
    // Embed URL: youtube.com/embed/ID
    m = input.match(/embed\/([\w-]{11})/);
    if (m) return m[1];
    return null;
}

// Create or reconfigure the YouTube player in the dance-yt-player div
function createYouTubePlayer(videoId, onReadyCb) {
    ytCurrentVideoId = videoId;
    ytPlayerReady = false;

    // Destroy existing player cleanly
    if (ytPlayer) {
        try { ytPlayer.destroy(); } catch(e) {}
        ytPlayer = null;
    }

    // Ensure the target div exists (it may have been removed by destroy())
    const container = document.getElementById('dance-yt-container');
    if (container && !document.getElementById('dance-yt-player')) {
        const div = document.createElement('div');
        div.id = 'dance-yt-player';
        container.appendChild(div);
    }

    const pVars = {
        autoplay: 0,
        controls: 1,
        modestbranding: 1,
        rel: 0,
        playsinline: 1,
        fs: 0,
        origin: window.location.origin
    };
    if (typeof window._selectedSong !== 'undefined' && window._selectedSong) {
        if (window._selectedSong.startSeconds) pVars.start = window._selectedSong.startSeconds;
        if (window._selectedSong.endSeconds) pVars.end = window._selectedSong.endSeconds;
    }

    ytPlayer = new YT.Player('dance-yt-player', {
        videoId: videoId,
        width: '100%',
        height: '100%',
        playerVars: pVars,
        events: {
            onReady: function(evt) {
                ytPlayerReady = true;
                console.log('[YT] Player ready for', videoId);
                if (onReadyCb) onReadyCb();
            },
            onStateChange: function(evt) {
                // YT.PlayerState.ENDED === 0
                if (evt.data === 0) {
                    console.log('[YT] Video ended — finishing dance');
                    finishDanceWasm();
                }
            },
            onError: function(evt) {
                console.error('[YT] Player error:', evt.data);
            }
        }
    });
}

let danceWasmRunning  = false;
let danceWasmStream   = null;
let danceWasmAnimFrame = null;
let danceWasmDetector = null;   // shared PoseLandmarker (reuse across sessions)
let danceWasmLastVideoTime = -1;
let danceWasmFps = { frames: 0, last: performance.now() };

// — Scoring engine state —
let danceScoreHistory = [];      // rolling window of raw cosine scores
const SCORE_WINDOW    = 15;      // frames to average (≈0.5s at 30fps)
let danceScoreAvg     = 0;       // smoothed 0-100

// — Countdown / gate state —
let dancePhase = 'detect';       // 'detect' | 'countdown' | 'dancing'
let danceCountdownTimer = null;
let danceCountdownNum   = 3;

// — Keypoints used for scoring (subset: arms, hips, knees, shoulders, elbows, wrists) —
// MediaPipe Pose 33 landmarks; we skip face (0-10) for movement scoring
const DANCE_SCORE_INDICES = [11,12,13,14,15,16,23,24,25,26,27,28];

// — Full-body detection: require these landmarks with good visibility —
const FULL_BODY_INDICES = [11,12,23,24,25,26,27,28]; // shoulders + hips + knees + ankles

// ─── Real cosine-similarity scoring vs reference video landmarks ──────────────
// Reference landmarks are extracted by app.py (MediaPipe IMAGE mode on the mp4)
// and served at /api/ref_landmarks/<videoId> as [{t, lms:[{x,y,v}…]}…]
// Each frame: find closest reference timestamp → cosine compare normalised pose vecs.

let _refLandmarks  = null;   // loaded reference frames array
let _refVideoId    = null;   // which video the ref data is for

// Poll status, then fetch data once ready.
// app.py extracts landmarks in a background thread — may take 10-60s first run.
function loadRefLandmarks(videoId) {
    if (_refVideoId === videoId && _refLandmarks) return; // already loaded
    _refLandmarks = null;
    _refVideoId   = videoId;

    let pollAttempt = 0;
    let _partialLoaded = false;
    function pollStatus() {
        pollAttempt++;
        fetch('/api/ref_landmarks/status/' + videoId)
            .then(r => r.json())
            .then(s => {
                const frames = s.frames || 0;
                console.log(`[Score] poll #${pollAttempt} status:${s.status} frames:${frames}`);

                const statusEl = document.getElementById('dance-wasm-status');

                if (s.ready) {
                    // Data available (partial or full) — fetch and load it
                    return fetch('/api/ref_landmarks/' + videoId)
                        .then(r => r.json())
                        .then(data => {
                            if (Array.isArray(data) && data.length > 0) {
                                _refLandmarks = data;
                                const isPartial = s.status === 'partial';
                                console.log(`[Score] ✅ Loaded ${data.length} ref frames (${s.status})`);
                                if (statusEl) statusEl.textContent = isPartial
                                    ? `Running… (indexing ${frames} frames)`
                                    : 'Running…';
                                if (_partialLoaded && !isPartial) {
                                    console.log('[Score] Full data now available — upgraded from partial');
                                }
                                _partialLoaded = true;
                                // Keep polling if still partial — upgrade to full when ready
                                if (isPartial) setTimeout(pollStatus, 4000);
                            }
                        });
                } else if (s.status && s.status.startsWith('error')) {
                    console.error('[Score] Extraction error:', s.status);
                    if (statusEl) statusEl.textContent = `Pose index error: ${s.status}`;
                } else {
                    // Still extracting with no partial data yet
                    if (statusEl) statusEl.textContent = frames > 0
                        ? `Indexing dance moves… (${frames} frames)`
                        : 'Indexing dance moves… (first run, ~30s)';
                    setTimeout(pollStatus, 2000);
                }
            })
            .catch(e => {
                console.warn('[Score] Poll failed, retrying in 3s:', e);
                setTimeout(pollStatus, 3000);
            });
    }

    pollStatus();
}

// Normalise landmark array by torso centre + torso height → scale-invariant vec
function poseToVec(lms, indices) {
    const ls = lms[11], rs = lms[12];
    // Use shoulders as anchor — always present even in partial body
    const cx = ls && rs ? (ls.x + rs.x) / 2 : 0.5;
    const cy = ls && rs ? (ls.y + rs.y) / 2 : 0.5;
    // Torso height from shoulders to hips (fall back to shoulder width if no hips)
    const lh = lms[23], rh = lms[24];
    let scale = 1;
    if (lh && rh) {
        scale = Math.hypot((ls.x+rs.x)/2 - (lh.x+rh.x)/2, (ls.y+rs.y)/2 - (lh.y+rh.y)/2) || 1;
    } else if (ls && rs) {
        scale = Math.hypot(ls.x - rs.x, ls.y - rs.y) || 1;
    }
    const vec = [];
    for (const idx of indices) {
        const lm = lms[idx];
        if (!lm) { vec.push(0, 0); continue; }
        vec.push((lm.x - cx) / scale, (lm.y - cy) / scale);
    }
    return vec;
}

function cosine(a, b) {
    let dot = 0, na = 0, nb = 0;
    for (let i = 0; i < a.length; i++) { dot += a[i]*b[i]; na += a[i]*a[i]; nb += b[i]*b[i]; }
    if (na === 0 || nb === 0) return 0;
    return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

// Find the reference frame closest to the current video playback time
function findRefFrame(videoTimeSec) {
    if (!_refLandmarks || _refLandmarks.length === 0) return null;
    let lo = 0, hi = _refLandmarks.length - 1;
    while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (_refLandmarks[mid].t < videoTimeSec) lo = mid + 1; else hi = mid;
    }
    // pick closest between lo and lo-1
    if (lo > 0 && Math.abs(_refLandmarks[lo-1].t - videoTimeSec) < Math.abs(_refLandmarks[lo].t - videoTimeSec)) lo--;
    return _refLandmarks[lo];
}

// Mirror map: ref landmark index → mirrored user landmark index
// The dancer faces camera, user faces camera → left/right are mirrored.
// To compare, we flip the reference: ref's left arm = user's right arm.
const MIRROR_MAP = {
    11: 12, 12: 11,  // shoulders
    13: 14, 14: 13,  // elbows
    15: 16, 16: 15,  // wrists
    23: 24, 24: 23,  // hips
    25: 26, 26: 25,  // knees
    27: 28, 28: 27,  // ankles
};

// Limb-vector angle scoring — much more robust than raw cosine on full pose vec.
// Returns a 0–100 score based on how well arm/leg angles match (mirror-corrected).
function scoreLimbAngles(userLms, refLmsAdapted) {
    const VIS = 0.3;
    // [user_start, user_end, ref_start, ref_end, weight]
    // weight 2 = arm (more expressive), 1 = leg
    const LIMBS = [
        [12, 16,  11, 15, 2],  // right arm full  → ref left arm full
        [11, 15,  12, 16, 2],  // left arm full   → ref right arm full
        [12, 14,  11, 13, 2],  // right upper arm → ref left upper arm
        [11, 13,  12, 14, 2],  // left upper arm  → ref right upper arm
        [14, 16,  13, 15, 1],  // right forearm   → ref left forearm
        [13, 15,  14, 16, 1],  // left forearm    → ref right forearm
        [24, 28,  23, 27, 1],  // right leg full  → ref left leg full
        [23, 27,  24, 28, 1],  // left leg full   → ref right leg full
        [24, 26,  23, 25, 1],  // right thigh     → ref left thigh
        [23, 25,  24, 26, 1],  // left thigh      → ref right thigh
    ];

    function vis(lms, i) { return (lms[i]?.visibility ?? lms[i]?.v ?? 0) >= VIS; }
    function vec(lms, i, j) {
        const p = lms[i], q = lms[j];
        if (!p || !q) return null;
        const dx = (q.x ?? 0) - (p.x ?? 0), dy = (q.y ?? 0) - (p.y ?? 0);
        const n = Math.hypot(dx, dy);
        return n < 1e-5 ? null : [dx/n, dy/n];
    }
    function angleDiff(v1, v2) {
        const dot = Math.max(-1, Math.min(1, v1[0]*v2[0] + v1[1]*v2[1]));
        return Math.acos(dot) * 180 / Math.PI;
    }
    // Maps angle error → score: ≤20° = 100, ≥80° = 0, linear between
    function angleToScore(deg) {
        if (deg <= 20) return 100;
        if (deg >= 80) return 0;
        return 100 - ((deg - 20) / 60) * 100;
    }

    let totalW = 0, totalS = 0;
    const debugParts = [];
    for (const [ui, uj, ri, rj, w] of LIMBS) {
        if (!vis(userLms, ui) || !vis(userLms, uj)) continue;
        if (!vis(refLmsAdapted, ri) || !vis(refLmsAdapted, rj)) continue;
        const uv = vec(userLms, ui, uj);
        const rv = vec(refLmsAdapted, ri, rj);
        if (!uv || !rv) continue;
        const deg = angleDiff(uv, rv);
        const s = angleToScore(deg);
        totalW += w;
        totalS += s * w;
        debugParts.push(`[${ui}-${uj}→${ri}-${rj}] ${s.toFixed(0)}° err=${deg.toFixed(1)}`);
    }

    if (totalW < 2) return null; // not enough limbs visible
    const score = Math.round(totalS / totalW);
    if (Math.random() < 0.05) {  // log ~5% of frames
        console.log(`[Score] LimbAngle=${score} | ${debugParts.join(' | ')}`);
        fetch('/api/log', {method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({msg: `LimbAngle=${score} | ${debugParts.join(' | ')}`})});
    }
    return score;
}

let _scoreDebugCounter = 0;

function scoreCurrentPose(userLms, videoTimeSec) {
    const VIS_THRESH = 0.30;  // lowered from 0.35 — be more inclusive

    // Build active scoring indices — only joints visible in user frame
    const activeIndices = DANCE_SCORE_INDICES.filter(
        idx => userLms[idx] && (userLms[idx].visibility ?? 0) >= VIS_THRESH
    );
    if (activeIndices.length < 4) {
        if (Math.random() < 0.1) console.warn('[Score] Too few visible joints:', activeIndices.length);
        return 0;
    }

    // ── Path A: limb-angle scoring vs reference ───────────────────────────────
    const refFrame = findRefFrame(videoTimeSec);
    if (refFrame) {
        // Convert ref lms format {x,y,v} to {x,y,visibility}.
        // Also mirror the ref x-coordinate (1-x) so the reference dancer's
        // coordinate space matches the user's raw MediaPipe output.
        // The ref video was recorded facing the camera; MediaPipe gives raw
        // (un-flipped) coordinates for both ref and user — so they must be
        // compared in the same space, which means flipping ref x just like
        // we flip the user skeleton for display.
        const refLmsAdapted = refFrame.lms.map(lm => ({x: 1 - lm.x, y: lm.y, visibility: lm.v ?? 1}));

        const limbScore = scoreLimbAngles(userLms, refLmsAdapted);
        if (limbScore !== null) {
            _scoreDebugCounter++;
            if (_scoreDebugCounter % 30 === 0) {
                const msg = `[Score] t=${videoTimeSec.toFixed(2)}s refFrame=${refFrame.t.toFixed(2)}s LimbScore=${limbScore} joints=${activeIndices.length}`;
                console.log(msg);
                fetch('/api/log', {method:'POST', headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({msg})});
            }
            return limbScore;
        }

        // Fallback to cosine if too few limbs (e.g. upper-body only)
        const sharedIndices = activeIndices.filter(idx => {
            const rv = refFrame.lms[idx];
            return rv && (rv.v ?? 1) >= VIS_THRESH;
        });
        if (sharedIndices.length >= 4) {
            const userVec = poseToVec(userLms, sharedIndices);
            const refVec  = poseToVec(refLmsAdapted, sharedIndices);
            const sim = cosine(userVec, refVec);  // -1..1
            // Map cosine to score: sim=1→100, sim=0→50, sim=-1→0
            // Use a more generous curve: sim≥0.8→100, sim≤0→35, linear between
            let raw;
            if (sim >= 0.8) raw = 100;
            else if (sim <= 0) raw = 35;
            else raw = 35 + ((sim / 0.8) * 65);
            const score = Math.round(Math.min(Math.max(raw, 0), 100));
            if (Math.random() < 0.05) {
                console.log(`[Score] Cosine fallback: sim=${sim.toFixed(3)} → ${score}`);
            }
            return score;
        }
    } else {
        if (Math.random() < 0.05) console.warn('[Score] No ref frame for t=', videoTimeSec.toFixed(2));
    }

    // ── Path B: ref not loaded yet ────────────────────────────────────────────
    const avgVis = activeIndices.reduce((s, idx) => s + (userLms[idx].visibility ?? 0), 0) / activeIndices.length;
    return Math.min(avgVis * 55, 45); // cap at 45 so it's clearly a fallback
}

function updateDanceScore(rawScore) {
    danceScoreHistory.push(rawScore);
    if (danceScoreHistory.length > SCORE_WINDOW) danceScoreHistory.shift();
    danceScoreAvg = danceScoreHistory.reduce((s, v) => s + v, 0) / danceScoreHistory.length;

    const rounded = Math.round(danceScoreAvg);
    const scoreEl  = document.getElementById('dance-live-score');
    const barEl    = document.getElementById('dance-score-bar');

    // Color thresholds: <40 = red, 40-65 = orange, >65 = green
    let color, barColor;
    if (rounded < 40)       { color = '#ef4444'; barColor = '#ef4444'; }
    else if (rounded < 65)  { color = '#f97316'; barColor = '#f97316'; }
    else                    { color = '#22c55e'; barColor = '#22c55e'; }

    if (scoreEl) { scoreEl.textContent = rounded; scoreEl.style.color = color; }
    if (barEl)   { barEl.style.width = rounded + '%'; barEl.style.background = barColor; }

    // Add bar to history strip
    addScoreStrip(rounded, barColor);
}

function addScoreStrip(score, color) {
    const strip = document.getElementById('dance-score-strip');
    if (!strip) return;
    const bar = document.createElement('div');
    const h   = Math.max(4, Math.round((score / 100) * 36));
    bar.style.cssText = `width:5px;height:${h}px;background:${color};border-radius:2px 2px 0 0;flex-shrink:0;opacity:0.85;`;
    strip.appendChild(bar);
    // Keep last 60 bars
    while (strip.children.length > 60) strip.removeChild(strip.firstChild);
}

// ─── Full-body detection gate ────────────────────────────────────────────────

// Minimum detection: both shoulders visible — that's enough to start.
// Lower body optional; scoring uses whatever landmarks are visible each frame.
function isAnyBodyDetected(lms) {
    return lms[11] && lms[12] &&
           (lms[11].visibility ?? 0) >= 0.45 &&
           (lms[12].visibility ?? 0) >= 0.45;
}

// ─── Countdown logic ─────────────────────────────────────────────────────────

function startDanceCountdown() {
    dancePhase = 'countdown';
    danceCountdownNum = 3;

    const overlay = document.getElementById('dance-countdown-overlay');
    const numEl   = document.getElementById('dance-countdown-number');
    const nudge   = document.getElementById('dance-detect-nudge');

    if (overlay) { overlay.style.display = 'flex'; }
    if (nudge)   { nudge.style.display = 'none'; }

    function tick() {
        if (numEl) numEl.textContent = danceCountdownNum;
        if (danceCountdownNum <= 0) {
            // Start dance — play YouTube video
            if (overlay) overlay.style.display = 'none';
            dancePhase = 'dancing';
            _prevUserVec = null;
            danceScoreHistory = [];
            if (ytPlayer && ytPlayerReady) {
                let startSec = 0;
                if (typeof window._selectedSong !== 'undefined' && window._selectedSong && window._selectedSong.startSeconds) {
                    startSec = window._selectedSong.startSeconds;
                }
                ytPlayer.seekTo(startSec, true);
                ytPlayer.playVideo();
            }
            return;
        }
        danceCountdownNum--;
        danceCountdownTimer = setTimeout(tick, 1000);
    }
    tick();
}

function resetDanceGate() {
    dancePhase = 'detect';
    if (danceCountdownTimer) { clearTimeout(danceCountdownTimer); danceCountdownTimer = null; }
    const overlay = document.getElementById('dance-countdown-overlay');
    if (overlay) overlay.style.display = 'none';
    const nudge = document.getElementById('dance-detect-nudge');
    if (nudge) nudge.style.display = 'flex';
    if (ytPlayer && ytPlayerReady) {
        try { 
            let startSec = 0;
            if (typeof window._selectedSong !== 'undefined' && window._selectedSong && window._selectedSong.startSeconds) {
                startSec = window._selectedSong.startSeconds;
            }
            ytPlayer.pauseVideo(); 
            ytPlayer.seekTo(startSec, true); 
        } catch(e) {}
    }
}

// ─── Main entry point ─────────────────────────────────────────────────────────

async function startDanceWasm(videoId = 'macarena2022', youtubeId = null) {
    const view = document.getElementById('view-dance-wasm');
    if (!view) { console.error('view-dance-wasm not found'); return; }
    showView(view);

    danceWasmRunning = true;
    dancePhase = 'detect';
    danceScoreHistory = [];
    danceScoreAvg = 0;
    danceWasmFps = { frames: 0, last: performance.now() };
    danceWasmLastVideoTime = -1;
    _prevUserVec = null;

    const statusEl  = document.getElementById('dance-wasm-status');
    const loadingEl = document.getElementById('dance-wasm-loading');
    const camVideo  = document.getElementById('dance-cam-video');
    const camCanvas = document.getElementById('dance-cam-canvas');
    const nudge     = document.getElementById('dance-detect-nudge');
    const scoreEl   = document.getElementById('dance-live-score');
    const barEl     = document.getElementById('dance-score-bar');
    const strip     = document.getElementById('dance-score-strip');

    // Reset UI
    if (scoreEl)  { scoreEl.textContent = '0'; scoreEl.style.color = '#22c55e'; }
    if (barEl)    { barEl.style.width = '0%'; }
    // Hide results overlay from previous dance
    const resultsOvl = document.getElementById('results-overlay');
    if (resultsOvl) resultsOvl.classList.add('hidden');
    if (strip)    strip.innerHTML = '';
    if (nudge)    nudge.style.display = 'none';
    document.getElementById('dance-countdown-overlay').style.display = 'none';

    // Resolve YouTube ID: from parameter, from song registry, or from videoId itself
    let ytId = youtubeId;
    if (!ytId) {
        // Look up in songsData (loaded from registry)
        const song = songsData.find(s => s.id === videoId);
        if (song && song.youtubeId) {
            ytId = song.youtubeId;
        } else {
            // Assume videoId IS a YouTube ID (for custom URL flow)
            ytId = videoId;
        }
    }

    // Create YouTube IFrame player (paused — starts on countdown finish)
    console.log('[Dance] Creating YouTube player for:', ytId);
    createYouTubePlayer(ytId);

    // Start loading reference landmarks in background (for real cosine scoring)
    loadRefLandmarks(videoId);

    // Canvas auto-resize helper
    function syncCanvas() {
        const rect = camCanvas.getBoundingClientRect();
        if (camCanvas.width !== rect.width || camCanvas.height !== rect.height) {
            camCanvas.width  = rect.width;
            camCanvas.height = rect.height;
        }
    }

    try {
        // 1. Camera
        if (statusEl) statusEl.textContent = 'Accessing camera…';
        danceWasmStream = await navigator.mediaDevices.getUserMedia({
            video: { width: 640, height: 480, frameRate: 30 }
        });
        camVideo.srcObject = danceWasmStream;
        await new Promise(res => { camVideo.onloadedmetadata = () => { camVideo.play(); res(); }; });

        // 2. PoseLandmarker — reuse existing detector if available
        if (!danceWasmDetector) {
            if (statusEl) statusEl.textContent = 'Loading Tasks API model…';
            let waited = 0;
            while (!window._mpPoseLandmarkerClass && waited < 12000) {
                await new Promise(r => setTimeout(r, 100)); waited += 100;
            }
            if (!window._mpPoseLandmarkerClass) throw new Error('PoseLandmarker ESM not loaded');

            const { PoseLandmarker, FilesetResolver } = window._mpPoseLandmarkerClass;
            if (statusEl) statusEl.textContent = 'Compiling WASM runtime…';
            const vision = await FilesetResolver.forVisionTasks(
                'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@latest/wasm'
            );
            if (statusEl) statusEl.textContent = 'Creating PoseLandmarker…';
            danceWasmDetector = await PoseLandmarker.createFromOptions(vision, {
                baseOptions: {
                    modelAssetPath: 'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task',
                    delegate: 'GPU',
                },
                runningMode: 'VIDEO',
                numPoses: 1,
                minPoseDetectionConfidence: 0.4,
                minPosePresenceConfidence: 0.4,
                minTrackingConfidence: 0.5,
            });
        }

        if (loadingEl) loadingEl.style.display = 'none';
        if (nudge)     nudge.style.display = 'flex';

        // 3. Render + inference loop
        let _frameCount = 0;
        let _lastResults = null;
        let _lastScoredFrame = 0;

        function renderLoop() {
            if (!danceWasmRunning) return;
            syncCanvas();

            if (camVideo.currentTime !== danceWasmLastVideoTime && camVideo.readyState >= 2) {
                danceWasmLastVideoTime = camVideo.currentTime;
                _frameCount++;

                // Run inference every frame (lite model is fast enough at 640x480)
                const results = danceWasmDetector.detectForVideo(camVideo, performance.now());
                _lastResults = results;

                const lms = results.landmarks?.[0];

                if (lms) {
                    // Draw skeleton
                    drawDanceSkeleton(camCanvas, lms);
                    updateDanceFps();

                    // Phase state machine
                    if (dancePhase === 'detect') {
                        if (isAnyBodyDetected(lms)) {
                            if (nudge) nudge.style.display = 'none';
                            startDanceCountdown();
                        }
                    } else if (dancePhase === 'dancing') {
                        // Score every 3rd frame to smooth
                        if (_frameCount - _lastScoredFrame >= 3) {
                            _lastScoredFrame = _frameCount;
                            let startSec = 0;
                            if (typeof window._selectedSong !== 'undefined' && window._selectedSong && window._selectedSong.startSeconds) {
                                startSec = window._selectedSong.startSeconds;
                            }
                            const videoTime = (ytPlayer && ytPlayerReady) ? Math.max(0, ytPlayer.getCurrentTime() - startSec) : 0;
                            const raw = scoreCurrentPose(lms, videoTime);
                            updateDanceScore(raw);
                        }
                        // Gate fires once — no reset after dancing starts
                    }
                } else {
                    // No person detected — clear canvas but never reset once dancing started
                    const ctx = camCanvas.getContext('2d');
                    ctx.clearRect(0, 0, camCanvas.width, camCanvas.height);
                    if (nudge && dancePhase === 'detect') nudge.style.display = 'flex';
                }
            }

            danceWasmAnimFrame = requestAnimationFrame(renderLoop);
        }

        danceWasmAnimFrame = requestAnimationFrame(renderLoop);

    } catch (err) {
        console.error('[DanceWasm] Error:', err);
        if (statusEl) { statusEl.textContent = `Error: ${err.message}`; statusEl.style.color = '#ef4444'; }
        if (loadingEl) { loadingEl.style.display = 'flex'; loadingEl.style.borderColor = 'rgba(239,68,68,0.4)'; }
    }
}

// ─── Skeleton renderer (same colour coding as existing mpWasm view) ───────────

function drawDanceSkeleton(canvas, lms) {
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    function lmPx(idx) {
        return [(1 - lms[idx].x) * canvas.width, lms[idx].y * canvas.height];
    }

    function drawConn(pairs, color, width = 4) {
        ctx.strokeStyle = color; ctx.lineWidth = width;
        ctx.lineJoin = 'round'; ctx.lineCap = 'round';
        pairs.forEach(([i, j]) => {
            if (!lms[i] || !lms[j]) return;
            if ((lms[i].visibility ?? 1) < 0.3 || (lms[j].visibility ?? 1) < 0.3) return;
            const [x1, y1] = lmPx(i), [x2, y2] = lmPx(j);
            ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
        });
    }

    drawConn(MP_SKELETON.face,  'rgba(148,163,184,0.4)', 2);
    drawConn(MP_SKELETON.upper, '#22c55e');
    drawConn(MP_SKELETON.core,  '#a855f7');
    drawConn(MP_SKELETON.lower, '#f59e0b');

    // Score-coloured glow on scoring joints when dancing
    const isScoring = dancePhase === 'dancing';
    let glowColor = '#06b6d4';
    if (isScoring) {
        const s = danceScoreAvg;
        glowColor = s < 40 ? '#ef4444' : s < 65 ? '#f97316' : '#22c55e';
    }

    lms.forEach((lm, idx) => {
        if ((lm.visibility ?? 1) < 0.3) return;
        const [x, y] = lmPx(idx);
        const r = idx < 11 ? 3 : DANCE_SCORE_INDICES.includes(idx) ? 7 : 5;

        if (isScoring && DANCE_SCORE_INDICES.includes(idx)) {
            // Glow ring
            ctx.beginPath(); ctx.arc(x, y, r + 4, 0, Math.PI * 2);
            ctx.strokeStyle = glowColor + '55'; ctx.lineWidth = 3; ctx.stroke();
        }
        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fillStyle = '#06b6d4'; ctx.fill();
        ctx.strokeStyle = '#0e7490'; ctx.lineWidth = 1.5; ctx.stroke();
    });
}

function updateDanceFps() {
    danceWasmFps.frames++;
    const now = performance.now();
    if (now - danceWasmFps.last >= 500) {
        const fps = Math.round((danceWasmFps.frames / (now - danceWasmFps.last)) * 1000);
        const el = document.getElementById('dance-fps');
        if (el) el.textContent = fps;
        danceWasmFps.frames = 0;
        danceWasmFps.last = now;
    }
}

function _cleanupDanceWasm() {
    danceWasmRunning = false;
    if (danceWasmAnimFrame) { cancelAnimationFrame(danceWasmAnimFrame); danceWasmAnimFrame = null; }
    if (danceWasmStream)    { danceWasmStream.getTracks().forEach(t => t.stop()); danceWasmStream = null; }
    if (danceCountdownTimer){ clearTimeout(danceCountdownTimer); danceCountdownTimer = null; }

    // Destroy YouTube player
    if (ytPlayer) {
        try { ytPlayer.stopVideo(); ytPlayer.destroy(); } catch(e) {}
        ytPlayer = null;
        ytPlayerReady = false;
    }
    // Recreate the placeholder div for next session
    const container = document.getElementById('dance-yt-container');
    if (container && !document.getElementById('dance-yt-player')) {
        const div = document.createElement('div');
        div.id = 'dance-yt-player';
        container.appendChild(div);
    }

    const canvas = document.getElementById('dance-cam-canvas');
    if (canvas) canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);

    document.getElementById('dance-countdown-overlay').style.display = 'none';

    // Reset loading UI for next session
    const loadingEl = document.getElementById('dance-wasm-loading');
    if (loadingEl) { loadingEl.style.display = 'flex'; loadingEl.style.borderColor = 'rgba(6,182,212,0.3)'; }
    const statusEl = document.getElementById('dance-wasm-status');
    if (statusEl)  { statusEl.textContent = 'Initialising MediaPipe…'; statusEl.style.color = '#06b6d4'; }
}

function stopDanceWasm() {
    // Calculate current score if they stopped early
    const avg = danceScoreHistory.length > 0
        ? Math.round(danceScoreHistory.reduce((s, v) => s + v, 0) / danceScoreHistory.length)
        : 0;
        
    let startSec = 0;
    if (typeof window._selectedSong !== 'undefined' && window._selectedSong && window._selectedSong.startSeconds) {
        startSec = window._selectedSong.startSeconds;
    }
    const durationSec = (ytPlayer && typeof ytPlayer.getCurrentTime === 'function') 
        ? Math.max(0, ytPlayer.getCurrentTime() - startSec)
        : 5; // Default small duration if unknown
        
    // Base calorie burn (roughly ~6.5 kcal/min) multiplied by score factor
    // Prevent 0 calories if they actually danced a bit
    const kcal = Math.max(1, Math.round((durationSec / 60) * 6.5 * (avg / 100 + 0.3)));

    _cleanupDanceWasm();
    showView(document.getElementById('view-settings'));

    // Explicitly tell backend to finish dance and resume timer
    fetch('/api/dance_finished', { 
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ score: avg, duration_seconds: Math.round(durationSec), calories: kcal })
    }).catch(console.error);
    
    startBreakCountdownPoll();
}

function finishDanceWasm() {
    // Called when the YouTube video ends naturally
    _cleanupDanceWasm();

    // Calculate final score
    const avg = danceScoreHistory.length > 0
        ? Math.round(danceScoreHistory.reduce((s, v) => s + v, 0) / danceScoreHistory.length)
        : 0;

    // Estimate calories (very rough: ~5-8 kcal per minute of dancing)
    const durationMin = (window._selectedSong && window._selectedSong.endSeconds && window._selectedSong.startSeconds)
        ? (window._selectedSong.endSeconds - window._selectedSong.startSeconds) / 60
        : 3; // default 3 minutes
    const durationSec = durationMin * 60;
    const kcal = Math.max(1, Math.round(durationMin * 6.5 * (avg / 100 + 0.3)));

    // Update results overlay (WASM version)
    const titleEl = document.getElementById('dance-wasm-results-title');
    const scoreEl = document.getElementById('dance-wasm-results-score');
    const calEl   = document.getElementById('dance-wasm-results-calories');
    const overlay = document.getElementById('dance-wasm-results-overlay');

    if (titleEl) titleEl.textContent = avg >= 70 ? 'AMAZING!' : avg >= 45 ? 'WELL DONE!' : 'KEEP GOING!';
    if (scoreEl) scoreEl.textContent = avg;
    if (calEl) calEl.textContent = '🔥 ' + kcal + ' kcal burned';
    
    renderStars('dance-stars', avg);
    
    if (overlay) overlay.classList.remove('hidden');

    // Notify backend that dance is finished and send stats
    fetch('/api/dance_finished', { 
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ score: avg, duration_seconds: Math.round(durationSec), calories: kcal })
    }).catch(e => console.error(e));

    console.log('[Dance] Finished! avg=' + avg + ' kcal=' + kcal);
}

// ─── Custom YouTube URL handler ─────────────────────────────────────────────
function startCustomYouTube() {
    const input = document.getElementById('custom-yt-url');
    if (!input) return;
    const url = input.value.trim();
    if (!url) { alert('Please paste a YouTube URL.'); return; }
    const ytId = extractYouTubeId(url);
    if (!ytId) { alert('Could not extract a YouTube video ID from that URL.'); return; }
    // Use the YouTube ID as both the videoId and youtubeId
    // No reference landmarks for custom videos — scoring will use fallback
    startDanceWasm(ytId, ytId);
}

// ─── activateLicense ────────────────────────────────────────────────────────
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
            setTimeout(() => {
                checkLicenseStatus();
                showView(document.getElementById('view-settings'));
            }, 1000);
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

// --- UPDATE CHECKER ---
let updateDownloadUrl = null;

function checkForUpdates() {
    fetch('/api/check_update')
        .then(r => r.json())
        .then(data => {
            const banner = document.getElementById('update-banner');
            const versionSpan = document.getElementById('update-version');
            const title = document.getElementById('update-title');
            const desc = document.getElementById('update-desc');
            const btn = document.getElementById('update-btn');
            
            if (!banner || !versionSpan || !title || !desc || !btn) return;

            if (data.has_update) {
                // Update Available
                versionSpan.textContent = data.latest_version;
                updateDownloadUrl = data.download_url;
                banner.style.background = 'linear-gradient(90deg, #10b981, #059669)';
                banner.style.boxShadow = '0 4px 15px rgba(16,185,129,0.3)';
                banner.style.cursor = 'pointer';
                title.innerHTML = `🚀 Move-It Update Available (v<span id="update-version">${data.latest_version}</span>)`;
                desc.textContent = 'A new version is available with bug fixes and new features. Click here to download the installer and upgrade your app!';
                btn.style.display = 'block';
                banner.onclick = triggerUpdate;
            } else {
                // Up to Date
                banner.style.background = 'linear-gradient(90deg, #1e293b, #0f172a)';
                banner.style.boxShadow = '0 4px 15px rgba(0,0,0,0.3)';
                banner.style.cursor = 'default';
                title.innerHTML = `✅ App is Up to Date (v<span id="update-version">${data.current_version}</span>)`;
                desc.textContent = "You're running the latest version of Move-It!";
                btn.style.display = 'none';
                banner.onclick = null;
            }
            
            banner.style.display = 'flex';
        }).catch(e => console.error("Update check failed:", e));
}

function triggerUpdate() {
    if (updateDownloadUrl) {
        fetch('/api/open_url', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({url: updateDownloadUrl})
        });
    }
}

function downloadUpdate() {
    if (updateDownloadUrl) {
        fetch('/api/open_url', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({url: updateDownloadUrl})
        });
    }
}
function viewLogs() {
    fetch('/api/get_logs')
        .then(r => r.json())
        .then(data => {
            const overlay = document.getElementById('logs-overlay');
            const textarea = document.getElementById('logs-textarea');
            if (overlay && textarea) {
                textarea.value = data.logs || 'No logs available.';
                overlay.classList.remove('hidden');
                // Scroll to bottom
                textarea.scrollTop = textarea.scrollHeight;
            }
        }).catch(e => console.error("Failed to fetch logs:", e));
}

function copyLogs() {
    const textarea = document.getElementById('logs-textarea');
    if (textarea) {
        textarea.select();
        document.execCommand('copy');
        
        // Brief visual feedback
        const btn = event.target;
        const oldText = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => btn.textContent = oldText, 2000);
    }
}

function showAppInfo() {
    const overlay = document.getElementById('info-overlay');
    if (overlay) {
        overlay.classList.remove('hidden');
    }
}

function toggleLicenseVisibility() {
    const input = document.getElementById('license-input');
    const btn = document.getElementById('toggle-license-visibility');
    if (input.type === 'password') {
        input.type = 'text';
        btn.textContent = '🙈';
    } else {
        input.type = 'password';
        btn.textContent = '👁️';
    }
}

function checkLicenseStatus(forceShowLicense) {
    fetch('/api/license_status')
        .then(res => res.json())
        .then(data => {
            const badge = document.getElementById('license-badge');
            const input = document.getElementById('license-input');
            const btnActivate = document.getElementById('btn-activate');
            const getPromo = document.getElementById('get-moveit-section');
            
            if (data.status !== 'active') {
                if (badge) {
                    badge.innerHTML = '● NOT ACTIVE (Click to Fix)';
                    badge.style.color = '#ef4444';
                    badge.style.background = 'rgba(239, 68, 68, 0.15)';
                    badge.style.borderColor = 'rgba(239, 68, 68, 0.3)';
                }
                if (input) {
                    input.disabled = false;
                    input.style.opacity = "1";
                    input.value = "";
                    input.type = "password";
                }
                if (btnActivate) {
                    btnActivate.textContent = "ACTIVATE";
                    btnActivate.disabled = false;
                    btnActivate.style.background = "";
                }
                if (getPromo) {
                    getPromo.style.display = 'block';
                }
                // On boot, force to license screen if not active
                showView(document.getElementById('view-license'));
            } else {
                if (badge) {
                    badge.innerHTML = '● ACTIVE';
                    badge.style.color = '#10b981';
                    badge.style.background = 'rgba(16, 185, 129, 0.15)';
                    badge.style.borderColor = 'rgba(16, 185, 129, 0.3)';
                }
                if (input) {
                    input.value = data.key || "";
                    input.disabled = true;
                    input.style.opacity = "0.7";
                    input.type = "password";
                }
                if (btnActivate) {
                    btnActivate.textContent = "ACTIVATED ✅";
                    btnActivate.disabled = true;
                    btnActivate.style.background = "#10b981";
                }
                if (getPromo) {
                    getPromo.style.display = 'none';
                }
                // On boot or after activation, go to dashboard
                if (document.getElementById('view-license').classList.contains('active')) {
                    showView(document.getElementById('view-settings'));
                }
            }
        }).catch(e => {
            console.error("License check failed:", e);
            const badge = document.getElementById('license-badge');
            if (badge) {
                badge.innerHTML = '● ERROR (Offline)';
                badge.style.color = '#f59e0b';
                badge.style.background = 'rgba(245, 158, 11, 0.15)';
                badge.style.borderColor = 'rgba(245, 158, 11, 0.3)';
            }
        });
}

// Run update check slightly after load
setTimeout(checkForUpdates, 3000);

// Run license check immediately on boot
checkLicenseStatus();