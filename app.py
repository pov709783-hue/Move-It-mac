import cv2
import mediapipe as mp
import json
import urllib.request
import urllib.error
import ssl
ssl._create_default_https_context = ssl._create_unverified_context
import numpy as np
import math
import threading
import time
import os
import data_store
from flask import Flask, send_from_directory, Response, jsonify, request
from flask_sock import Sock
import webview
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import pystray
from PIL import Image, ImageDraw
from pynput import mouse, keyboard
import psutil
import sys
import subprocess


try:
    import pygetwindow as gw
except ImportError:
    gw = None
import sys

# PyInstaller compatibility: resolve base path for bundled files
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# =============================================================================
# Logging Setup — writes to a file so remote users can share debug info
# =============================================================================
import logging

LOG_DIR = os.path.join(os.path.expanduser("~"), ".move-it")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "move_it.log")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'),  # Overwrite each launch
    ]
)
logger = logging.getLogger("MoveIt")

# Redirect print() and stderr to the log file so ALL output is captured
class LogRedirector:
    def __init__(self, log_func):
        self.log_func = log_func
        self.buffer = ""
    def write(self, msg):
        if msg and msg.strip():
            self.log_func(msg.strip())
    def flush(self):
        pass

sys.stdout = LogRedirector(logger.info)
sys.stderr = LogRedirector(logger.error)

logger.info(f"Move-It v{None} starting up...")  # placeholder, will be set below
logger.info(f"BASE_DIR: {BASE_DIR}")
logger.info(f"LOG_FILE: {LOG_FILE}")
logger.info(f"Python: {sys.version}")
logger.info(f"Frozen: {getattr(sys, 'frozen', False)}")

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, 'ui'))
sock = Sock(app)

CURRENT_APP_VERSION = "1.0.3"
UPDATE_CHECK_URL = "https://move-it-green-two.vercel.app/version.json"

logger.info(f"Move-It v{CURRENT_APP_VERSION} initialized")

def get_machine_uuid():
    """Retrieves a unique hardware ID (UUID) for this specific computer."""
    try:
        # On Windows, wmic gets the motherboard/system UUID
        output = subprocess.check_output('wmic csproduct get uuid', shell=True).decode().split('\n')
        for line in output:
            line = line.strip()
            if line and "UUID" not in line:
                return line
    except Exception as e:
        print(f"Error getting Machine UUID: {e}")
    # Fallback to MAC address or a random generated one if wmic fails
    import uuid
    return str(uuid.getnode())

def cleanup_and_exit():
    """Stops all background hooks and safely terminates the application."""
    global global_mouse_listener, global_kb_listener
    print("Stopping global listeners before exit...")
    if global_mouse_listener is not None:
        try:
            global_mouse_listener.stop()
            global_mouse_listener.join(timeout=2)
        except: pass
    if global_kb_listener is not None:
        try:
            global_kb_listener.stop()
            global_kb_listener.join(timeout=2)
        except: pass
    time.sleep(0.3)
    os._exit(0)

# Configuration
DATA_PATH = os.path.join(BASE_DIR, 'pose_data.json')
MODEL_PATH = os.path.join(BASE_DIR, 'pose_landmarker_heavy.task')
UNLOCK_SCORE_THRESHOLD = 95
MIN_LIMIT_SECONDS = 10 * 60  # 10 minutes minimum limit
VISIBILITY_THRESHOLD = 0.5  # Minimum confidence to consider a landmark "visible"

# State
is_tracking = False
is_locked = False
is_dancing = False
is_calibrating = False
active_time = 0
has_shown_tray_notification = False

last_activity_time = time.time()
last_camera_presence_time = 0
camera_person_present = False  # Explicit flag: is someone in front of the camera RIGHT NOW?
calibration_success_time = 0
calibration_status = {"status": "calibrating", "color": "red", "message": "Analyzing..."}

# Globals
latest_frame = None
latest_pose = None
current_score = 0
icon = None

def show_tray_notification_once():
    global has_shown_tray_notification, icon
    if sys.platform == 'win32' and not has_shown_tray_notification and icon is not None:
        try:
            icon.notify(
                "Move-It is running in the background. Click the tray icon to open it.",
                title="Move-It"
            )
            has_shown_tray_notification = True
        except Exception as e:
            print(f"Failed to show tray notification: {e}")
window = None
ref_frame_index = 0
dance_start_time = 0
score_samples = []  # Accumulate scores during dance for averaging

session_movement_score = 0.0
last_pose_for_movement = None

# Load persistent interval (default 30 mins)
saved_interval = data_store.get_setting("work_interval_minutes")
if saved_interval:
    base_limit_seconds = int(saved_interval) * 60
else:
    base_limit_seconds = 30 * 60
current_limit_seconds = base_limit_seconds

is_stretching = False
current_stretch_target = None

manual_meeting_mode = False
last_meeting_end_time = 0
meeting_grace_period_active = False

global_mouse_listener = None
global_kb_listener = None

reference_data = None
total_ref_frames = 0
songs_registry = []


# Load songs registry
REGISTRY_PATH = os.path.join(BASE_DIR, 'songs_registry.json')
if os.path.exists(REGISTRY_PATH):
    with open(REGISTRY_PATH, 'r') as f:
        songs_registry = json.load(f)
else:
    # Fallback to the original video if registry doesn't exist yet
    songs_registry = [{
        "id": "H_rRYdNCu_k",
        "title": "I Like To Move It Just Dance",
        "thumbnail": "https://img.youtube.com/vi/H_rRYdNCu_k/mqdefault.jpg",
        "url": "https://www.youtube.com/watch?v=H_rRYdNCu_k"
    }]

# Load stretches data
STRETCHES_PATH = os.path.join(BASE_DIR, 'stretches_data.json')
try:
    with open(STRETCHES_PATH, 'r') as f:
        stretches_data = json.load(f)
    print(f"Loaded stretches data: {list(stretches_data.keys())}")
except FileNotFoundError:
    print(f"WARNING: stretches_data.json not found at {STRETCHES_PATH}")
    stretches_data = {}

# Initialize MediaPipe
base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
options = vision.PoseLandmarkerOptions(
    base_options=base_options,
    running_mode=vision.RunningMode.LIVE_STREAM,
    num_poses=1,
    min_pose_detection_confidence=0.5,
    min_pose_presence_confidence=0.5,
    min_tracking_confidence=0.5,
    result_callback=lambda result, image, timestamp: process_result(result, image, timestamp)
)

def process_result(result, image, timestamp):
    global latest_pose, last_camera_presence_time, camera_person_present
    global session_movement_score, last_pose_for_movement
    if result.pose_landmarks:
        latest_pose = result.pose_landmarks[0]
        last_camera_presence_time = time.time()
        camera_person_present = True
        
        if is_dancing and last_pose_for_movement:
            movement = 0.0
            for idx in [11, 12, 15, 16, 23, 24, 27, 28]: # Shoulders, Wrists, Hips, Ankles
                if getattr(latest_pose[idx], 'visibility', 1.0) > 0.5 and getattr(last_pose_for_movement[idx], 'visibility', 1.0) > 0.5:
                    p1 = latest_pose[idx]
                    p2 = last_pose_for_movement[idx]
                    dist = ((p1.x - p2.x)**2 + (p1.y - p2.y)**2)**0.5
                    movement += dist
            session_movement_score += movement
            
        last_pose_for_movement = latest_pose
    else:
        latest_pose = None
        camera_person_present = False
        last_pose_for_movement = None

landmarker = vision.PoseLandmarker.create_from_options(options)

POSE_CONNECTIONS = [(0,1),(1,2),(2,3),(3,7),(0,4),(4,5),(5,6),(6,8),(9,10),(11,12),(11,13),(13,15),(15,17),(15,19),(15,21),(17,19),(12,14),(14,16),(16,18),(16,20),(16,22),(18,20),(11,23),(12,24),(23,24),(23,25),(24,26),(25,27),(26,28),(27,29),(28,30),(29,31),(30,32),(27,31),(28,32)]

# =============================================================================
# Scoring Engine (Vector Angle Matching with Fallback Chains)
# =============================================================================

def is_visible(pose, idx):
    """Check if a landmark index is visible enough to use."""
    return getattr(pose[idx], 'visibility', 1.0) > VISIBILITY_THRESHOLD

def get_vector(p1, p2):
    """Returns normalized 2D direction vector from p1 to p2."""
    v = np.array([p2.x - p1.x, p2.y - p1.y])
    norm = np.linalg.norm(v)
    if norm < 1e-6:
        return np.array([0.0, 0.0])
    return v / norm

def get_midpoint(p1, p2):
    """Returns a dummy landmark at the midpoint of p1 and p2."""
    class Dummy: pass
    m = Dummy()
    m.x = (p1.x + p2.x) / 2.0
    m.y = (p1.y + p2.y) / 2.0
    m.visibility = min(getattr(p1, 'visibility', 1.0), getattr(p2, 'visibility', 1.0))
    return m

def calculate_angle_diff(v1, v2):
    """Returns angle difference in degrees between two normalized vectors."""
    dot = np.dot(v1, v2)
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(math.acos(dot))

def score_angle(angle_diff):
    """Maps an angle difference (degrees) to a 0-100 score."""
    if angle_diff <= 25:
        return 100  # Within 25 degrees = perfect
    elif angle_diff >= 90:
        return 0    # 90+ degrees = zero
    else:
        return 100 - ((angle_diff - 25) / 65.0) * 100

def get_limb_vector_with_fallback(pose, joint_start, joint_full, joint_partial):
    """
    Get a limb direction vector using a fallback chain.
    First tries start->full (e.g. Shoulder->Wrist).
    If full endpoint is not visible, falls back to start->partial (e.g. Shoulder->Elbow).
    Returns (vector, True) if any segment is usable, or (None, False) if nothing is visible.
    """
    if is_visible(pose, joint_start):
        if is_visible(pose, joint_full):
            return get_vector(pose[joint_start], pose[joint_full]), True
        elif is_visible(pose, joint_partial):
            return get_vector(pose[joint_start], pose[joint_partial]), True
    return None, False

def get_smart_weight(ref_pose, start_idx, end_idx):
    """Determine how 'active' a limb is by comparing it to a neutral standing pose.
    Returns weight 3 if the limb deviates significantly from standing, else 1.
    
    Neutral standing vectors (normalized):
    - Arms: pointing downward from shoulder → (0, 1)  
    - Legs: pointing downward from hip → (0, 1)
    """
    neutral_down = np.array([0.0, 1.0])  # Pointing straight down
    
    if not is_visible(ref_pose, start_idx) or not is_visible(ref_pose, end_idx):
        return 1  # Can't determine, use default weight
    
    ref_vec = get_vector(ref_pose[start_idx], ref_pose[end_idx])
    angle_from_neutral = calculate_angle_diff(ref_vec, neutral_down)
    
    # If the limb deviates more than 30° from hanging straight down, it's "active"
    if angle_from_neutral > 30:
        return 3
    return 1

def calculate_score(live_pose, ref_pose_data):
    """Calculate similarity score using SMART WEIGHTED vector angles.
    Automatically detects which limbs are 'active' (not in standing position)
    and weights them 3x higher. Standing-neutral limbs get 1x weight."""
    if not live_pose or not ref_pose_data:
        return 0

    class DummyLandmark:
        def __init__(self, x, y, vis=1.0):
            self.x = x
            self.y = y
            self.visibility = vis

    ref_pose = [DummyLandmark(lm['x'], lm['y'], lm.get('visibility', 1.0)) for lm in ref_pose_data]

    weighted_scores = []  # (score, weight) tuples
    debug_parts = []

    # Limb definitions: (name, start_joint, full_endpoint, partial_endpoint)
    limbs = [
        ("Left Arm",  11, 15, 13),
        ("Right Arm", 12, 16, 14),
        ("Left Leg",  23, 27, 25),
        ("Right Leg", 24, 28, 26),
    ]

    for name, start, full, partial in limbs:
        # Smart weight: check how much this limb deviates from standing in the REFERENCE
        weight = get_smart_weight(ref_pose, start, full)
        if weight == 1:
            weight = get_smart_weight(ref_pose, start, partial)  # Try partial too
        
        live_vec, live_ok = get_limb_vector_with_fallback(live_pose, start, full, partial)
        ref_vec, ref_ok = get_limb_vector_with_fallback(ref_pose, start, full, partial)
        if live_ok and ref_ok:
            angle = calculate_angle_diff(live_vec, ref_vec)
            s = score_angle(angle)
            weighted_scores.append((s, weight))
            debug_parts.append(f"{name}: {s:.0f} (a={angle:.0f}°) w={weight}")
        elif ref_ok and not live_ok:
            if weight > 1:
                # This is an active limb — penalize if not visible
                weighted_scores.append((0, weight))
                debug_parts.append(f"{name}: 0 (missing!) w={weight}")
            else:
                debug_parts.append(f"{name}: SKIP (off cam, passive)")
        else:
            debug_parts.append(f"{name}: SKIP (ref low vis)")

    # Spine (always weight 1 — baseline sanity check)
    if is_visible(live_pose, 11) and is_visible(live_pose, 12) and is_visible(live_pose, 23) and is_visible(live_pose, 24):
        live_spine = get_vector(get_midpoint(live_pose[23], live_pose[24]),
                                get_midpoint(live_pose[11], live_pose[12]))
        ref_spine = get_vector(get_midpoint(ref_pose[23], ref_pose[24]),
                               get_midpoint(ref_pose[11], ref_pose[12]))
        angle = calculate_angle_diff(live_spine, ref_spine)
        s = score_angle(angle)
        # Spine gets smart weight too: if ref spine is tilted (side bend), weight it more
        spine_neutral = np.array([0.0, -1.0])  # Pointing straight up
        spine_angle = calculate_angle_diff(ref_spine, spine_neutral)
        spine_weight = 3 if spine_angle > 20 else 1
        weighted_scores.append((s, spine_weight))
        debug_parts.append(f"Spine: {s:.0f} (a={angle:.0f}°) w={spine_weight}")

    if not weighted_scores or len(weighted_scores) < 2:
        return 0

    total_weight = sum(w for _, w in weighted_scores)
    final = max(0, min(100, int(sum(s * w for s, w in weighted_scores) / total_weight)))
    
    import random
    if random.random() < 0.05:
        print(f"  Score={final} | {' | '.join(debug_parts)}")
    return final

def best_score_in_window(live_pose, ref_frames, center_idx, window_size=3):
    """Compare against ±3 nearby reference frames and return the best score.
    Small window accounts for slight timing differences without being too forgiving."""
    best = 0
    total = len(ref_frames)
    for offset in range(-window_size, window_size + 1):
        idx = (center_idx + offset) % total
        s = calculate_score(live_pose, ref_frames[idx]['landmarks'])
        if s > best:
            best = s
    return best

def get_color_for_score(score):
    if score >= 67: return (0, 255, 0)
    elif score >= 34: return (0, 165, 255)
    else: return (0, 0, 255)

# =============================================================================
# Camera Daemon (Single thread owns the webcam)
# =============================================================================
# Removed cv2.VideoCapture loop as per architecture change. All frames are now sourced from the frontend via WebSocket.
# Mouse/Keyboard Tracking Daemon
# =============================================================================
def on_activity(*args):
    global last_activity_time
    last_activity_time = time.time()

def _popup_prompt():
    """Show the window when work limit is reached.
    The frontend auto-detects is_locked via polling and switches to prompt view."""
    try:
        if not window:
            return
        window.show()
        time.sleep(0.8)
        window.restore()
        time.sleep(0.8)
        window.maximize()
        time.sleep(0.5)
        window.on_top = True
    except Exception as e:
        print(f"Failed to popup prompt: {e}")

def tracking_daemon():
    global active_time, is_locked, is_tracking, window
    global last_meeting_end_time, meeting_grace_period_active
    global global_mouse_listener, global_kb_listener

    try:
        global_mouse_listener = mouse.Listener(on_move=on_activity, on_click=on_activity, on_scroll=on_activity)
        global_kb_listener = keyboard.Listener(on_press=on_activity)
        global_mouse_listener.start()
        global_kb_listener.start()
    except Exception as e:
        print(f"CRITICAL ERROR STARTING PYNPUT LISTENERS: {e}", flush=True)

    no_activity_start = None
    bg_cap = None
    loop_counter = 0

    last_loop_time = time.time()
    
    try:
        while True:
            time.sleep(1)
            loop_counter += 1
            
            current_time = time.time()
            elapsed = current_time - last_loop_time
            last_loop_time = current_time

            if not is_tracking or is_locked:
                if bg_cap is not None:
                    bg_cap.release()
                    bg_cap = None
                continue
            
            license_data = data_store.get_license()
            if not license_data or license_data.get('status') != 'active':
                # Stop accumulating time if not activated
                continue

            in_meeting = check_meeting_status()
        
            if in_meeting:
                meeting_grace_period_active = True
                last_meeting_end_time = current_time
                active_time += elapsed  # Assume they are sitting
                print(f"MEETING ACTIVE - Accumulating time: {int(active_time)}/{current_limit_seconds}s")
                continue
            
            # Post meeting grace period logic
            if meeting_grace_period_active:
                time_since_meeting = current_time - last_meeting_end_time
                if time_since_meeting < 300: # 5 minutes camera grace
                    print(f"GRACE PERIOD (Camera Off) - {int(300 - time_since_meeting)}s left")
                    active_time += elapsed
                    continue
                elif time_since_meeting < 600: # 5-10 minutes (camera on, but no popup)
                    print(f"GRACE PERIOD (Camera On) - {int(600 - time_since_meeting)}s left to popup")
                    # Let it fall through to normal tracking to check if they leave, but block popup
                else:
                    meeting_grace_period_active = False

            # Normal Tracking — mouse/keyboard else mediapipe fallback
            mouse_active = (current_time - last_activity_time < 3)

            is_away = (no_activity_start is not None and (current_time - no_activity_start >= 10))

            if not mouse_active and not (is_dancing or is_stretching) and not is_away:
                if bg_cap is None:
                    if sys.platform == 'win32':
                        bg_cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
                    else:
                        bg_cap = cv2.VideoCapture(0)
                    bg_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    bg_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
                    bg_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
                    bg_cap.set(cv2.CAP_PROP_FPS, 5)
            
                # Sub-sample: only grab and infer every 4 loop counts
                if loop_counter % 4 == 0:
                    # Flush buffer to get newest frame
                    bg_cap.grab()
                    ret, frame = bg_cap.read()
                    if ret:
                        timestamp_ms = int(current_time * 1000)
                        try:
                            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                            landmarker.detect_async(mp_img, timestamp_ms)
                        except Exception as e:
                            pass
            else:
                if bg_cap is not None:
                    bg_cap.release()
                    bg_cap = None

            person_present = camera_person_present

            if mouse_active or person_present:
                if no_activity_start is not None:
                    paused_duration = current_time - no_activity_start
                    if 0 < paused_duration < 10:
                        active_time += paused_duration
                        print(f"Retroactively added {int(paused_duration)}s of camera confirmation time.")
                no_activity_start = None
                active_time += elapsed
                status = "Input" if mouse_active else "Camera"
                print(f"Active time: {int(active_time)}/{current_limit_seconds}s [{status}]")
            else:
                print(f"PAUSED - No activity detected")
                if no_activity_start is None:
                    no_activity_start = time.time()
                elif time.time() - no_activity_start >= 10:
                    if active_time > 0:
                        print(f"No activity for 10s. Resetting counter from {active_time} to 0.")
                        data_store.record_sitting_seconds(active_time)
                        data_store.record_sitting_session(active_time)
                        active_time = 0
                    no_activity_start = None
                    meeting_grace_period_active = False # They left, so grace period ends

            if active_time >= current_limit_seconds and not meeting_grace_period_active:
                print("WORK LIMIT REACHED. POPPING PROMPT.")
                data_store.record_sitting_seconds(active_time)
                data_store.record_sitting_session(active_time)
                data_store.record_prompt()
                is_locked = True
                is_tracking = False
                no_activity_start = None
                if window:
                    threading.Thread(target=_popup_prompt, daemon=True).start()

    except Exception as e:
        print(f"CRITICAL ERROR IN TRACKING LOOP: {e}", flush=True)

# =============================================================================
# Meeting Detection
# =============================================================================
def check_meeting_status():
    global manual_meeting_mode
    if manual_meeting_mode:
        return True
        
    # Check processes
    meeting_apps = ['Zoom.exe', 'Teams.exe', 'Discord.exe', 'WebexHost.exe']
    try:
        for proc in psutil.process_iter(['name']):
            if proc.info['name'] in meeting_apps:
                return True
    except Exception:
        pass
        
    # Check window titles
    if gw:
        try:
            titles = gw.getAllTitles()
            meeting_keywords = ['Google Meet', 'Zoom Meeting', 'Microsoft Teams']
            for t in titles:
                for k in meeting_keywords:
                    if k.lower() in t.lower():
                        return True
        except Exception:
            pass
            
    return False

# =============================================================================
# Background Daemons
# =============================================================================
def gen_frames():
    global current_score, ref_frame_index, is_locked, is_dancing, is_calibrating
    global active_time, current_limit_seconds, base_limit_seconds, window, is_tracking
    global calibration_status, calibration_success_time, dance_start_time
    global is_stretching, current_stretch_target

    while True:
        if not (is_dancing or is_stretching) or latest_frame is None:
            time.sleep(0.1)
            continue
            
        frame = latest_frame.copy()

        # Run MediaPipe detection on every frame during dance/calibration
        try:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
            timestamp_ms = int(cv2.getTickCount() / cv2.getTickFrequency() * 1000)
            landmarker.detect_async(mp_image, timestamp_ms)
        except Exception:
            pass

        h, w, _ = frame.shape

        try:
            if is_calibrating:
                # ----- CALIBRATION: Just need head + shoulders visible (standing posture) -----
                if latest_pose:
                    head = latest_pose[0]
                    left_shoulder = latest_pose[11]
                    right_shoulder = latest_pose[12]
                    left_hip = latest_pose[23]
                    right_hip = latest_pose[24]

                    head_ok = is_visible(latest_pose, 0) and head.y > 0.02 and head.y < 0.98
                    shoulders_ok = is_visible(latest_pose, 11) and is_visible(latest_pose, 12)
                    hips_ok = is_visible(latest_pose, 23) and is_visible(latest_pose, 24)

                    if head_ok and shoulders_ok and hips_ok:
                        calibration_status = {"status": "calibrating", "color": "green", "message": "PERFECT POSITION - Hold it!"}
                        if calibration_success_time == 0:
                            calibration_success_time = time.time()
                        elif time.time() - calibration_success_time > 2.0:
                            is_calibrating = False
                            dance_start_time = time.time()  # Mark when dancing actually starts
                            current_score = 0  # Reset score
                            calibration_status = {"status": "ready"}
                    else:
                        calibration_status = {"status": "calibrating", "color": "red", "message": "Stand up and face the camera"}
                        calibration_success_time = 0

                    # Draw skeleton dots during calibration
                    for lm in latest_pose:
                        if getattr(lm, 'visibility', 1.0) > VISIBILITY_THRESHOLD:
                            cv2.circle(frame, (int(lm.x * w), int(lm.y * h)), 4, (255, 255, 255), -1)
                else:
                    calibration_status = {"status": "calibrating", "color": "red", "message": "Step into the camera"}
                    calibration_success_time = 0

            elif is_stretching:
                # ----- STRETCHING PHASE -----
                color = (0, 0, 255)
                if latest_pose and current_stretch_target in stretches_data:
                    ref_pose = stretches_data[current_stretch_target]
                    current_score = calculate_score(latest_pose, ref_pose)
                    color = get_color_for_score(current_score)
                    
                    for connection in POSE_CONNECTIONS:
                        start_lm = latest_pose[connection[0]]
                        end_lm = latest_pose[connection[1]]
                        if getattr(start_lm, 'visibility', 1.0) > VISIBILITY_THRESHOLD and getattr(end_lm, 'visibility', 1.0) > VISIBILITY_THRESHOLD:
                            cv2.line(frame, (int(start_lm.x * w), int(start_lm.y * h)),
                                     (int(end_lm.x * w), int(end_lm.y * h)), color, 4)

                    for lm in latest_pose:
                        if getattr(lm, 'visibility', 1.0) > VISIBILITY_THRESHOLD:
                            cx, cy = int(lm.x * w), int(lm.y * h)
                            cv2.circle(frame, (cx, cy), 6, (255, 255, 255), -1)
                            cv2.circle(frame, (cx, cy), 8, color, 2)
                            
            else:
                # ----- DANCING PHASE -----
                if reference_data is None:
                    continue
                    
                current_ref_data = reference_data['frames'][ref_frame_index]['landmarks']
                color = (0, 0, 255)

                if latest_pose:
                    current_score = best_score_in_window(latest_pose, reference_data['frames'], ref_frame_index)
                    color = get_color_for_score(current_score)

                    for connection in POSE_CONNECTIONS:
                        start_lm = latest_pose[connection[0]]
                        end_lm = latest_pose[connection[1]]
                        if getattr(start_lm, 'visibility', 1.0) > VISIBILITY_THRESHOLD and getattr(end_lm, 'visibility', 1.0) > VISIBILITY_THRESHOLD:
                            cv2.line(frame, (int(start_lm.x * w), int(start_lm.y * h)),
                                     (int(end_lm.x * w), int(end_lm.y * h)), color, 4)

                    for lm in latest_pose:
                        if getattr(lm, 'visibility', 1.0) > VISIBILITY_THRESHOLD:
                            cx, cy = int(lm.x * w), int(lm.y * h)
                            cv2.circle(frame, (cx, cy), 6, (255, 255, 255), -1)
                            cv2.circle(frame, (cx, cy), 8, color, 2)

                    # Accumulate scores for averaging (skip first 3 seconds)
                    if (time.time() - dance_start_time) > 3.0 and current_score > 0:
                        score_samples.append(current_score)
                else:
                    current_score = 0

                ref_frame_index = (ref_frame_index + 1) % total_ref_frames

        except Exception as e:
            # Crash protection: draw error on frame instead of killing the stream
            cv2.putText(frame, f"Error: {str(e)[:60]}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            print(f"gen_frames error: {e}")

        ret, buffer = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

# =============================================================================
# API Endpoints
# =============================================================================
# =============================================================================
# MEDIAPIPE DANCE MODE — Add this entire block to app.py
# Paste it directly after the existing YOLO section (after line 1296)
# before the @app.route('/api/log') route
# =============================================================================

# MediaPipe state variables (mirrors the YOLO ones)
mp_test_active = False
mp_ref_data = None
mp_ref_frame_index = 0
mp_total_ref_frames = 0
mp_current_score = 0
mp_score_samples = []
mp_dance_start_time = 0
mp_calibration_status = "calibrating"
mp_calibration_message = ""
mp_calibration_color = [0, 0, 255]
mp_calibration_success_time = 0
mp_countdown_start_time = 0

# MediaPipe Pose Landmarker Lite — lazy loaded
mp_landmarker = None

def get_mp_landmarker():
    """Lazy-load MediaPipe Pose Landmarker Lite. Only loaded when needed."""
    global mp_landmarker
    if mp_landmarker is None:
        try:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision

            model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pose_landmarker_lite.task")

            # Download if not present
            if not os.path.exists(model_path):
                print("Downloading MediaPipe Pose Landmarker Lite model...")
                import urllib.request
                url = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
                urllib.request.urlretrieve(url, model_path)
                print("MediaPipe model downloaded.")

            base_options = mp_python.BaseOptions(model_asset_path=model_path)
            options = mp_vision.PoseLandmarkerOptions(
                base_options=base_options,
                running_mode=mp_vision.RunningMode.IMAGE,
                num_poses=1,
                min_pose_detection_confidence=0.5,
                min_pose_presence_confidence=0.5,
                min_tracking_confidence=0.5
            )
            mp_landmarker = mp_vision.PoseLandmarker.create_from_options(options)
            print("SUCCESS: MediaPipe Pose Landmarker Lite loaded.")
        except Exception as e:
            print(f"ERROR loading MediaPipe: {e}")
            mp_landmarker = None
    return mp_landmarker


# MediaPipe keypoint index → COCO 17 keypoint index mapping
# This lets us reuse the same scoring function as YOLO
MP_TO_COCO = {
    0:  0,   # nose
    11: 5,   # left_shoulder
    12: 6,   # right_shoulder
    13: 7,   # left_elbow
    14: 8,   # right_elbow
    15: 9,   # left_wrist
    16: 10,  # right_wrist
    23: 11,  # left_hip
    24: 12,  # right_hip
    25: 13,  # left_knee
    26: 14,  # right_knee
    27: 15,  # left_ankle
    28: 16,  # right_ankle
}


def mp_landmarks_to_coco17(landmarks, w, h):
    """
    Convert MediaPipe 33-keypoint landmarks to COCO 17-keypoint numpy array.
    Returns numpy array shape (17, 3) with [x_norm, y_norm, visibility].
    """
    coco = np.zeros((17, 3), dtype=np.float32)
    for mp_idx, coco_idx in MP_TO_COCO.items():
        lm = landmarks[mp_idx]
        coco[coco_idx] = [lm.x, lm.y, getattr(lm, 'visibility', 1.0)]
    return coco


@app.route('/api/start_mp_test', methods=['POST'])
def api_start_mp_test():
    global mp_test_active, mp_ref_data, mp_ref_frame_index, mp_total_ref_frames
    global mp_current_score, mp_score_samples, mp_dance_start_time

    data = request.get_json(silent=True) or {}
    video_id = data.get('video_id', 'macarena2022')

    # Reuse the same YOLO reference JSON — scoring format is compatible
    pose_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f'yolo_pose_data_{video_id}.json')

    if not os.path.exists(pose_path):
        return jsonify({'status': 'error', 'message': f'Pose data not found for {video_id}'}), 404

    with open(pose_path, 'r') as f:
        mp_ref_data = json.load(f)

    mp_total_ref_frames = len(mp_ref_data['frames'])
    mp_ref_frame_index = 0
    mp_current_score = 0
    mp_score_samples = []
    mp_dance_start_time = time.time()
    mp_test_active = True

    # Pre-load the model in background so it's ready when WebSocket connects
    threading.Thread(target=get_mp_landmarker, daemon=True).start()

    print(f'MediaPipe Test started. {mp_total_ref_frames} reference frames loaded.')
    return jsonify({'status': 'ok', 'video_id': video_id, 'total_frames': mp_total_ref_frames})


@app.route('/api/mp_dance_finished', methods=['POST'])
def api_mp_dance_finished():
    global mp_score_samples, mp_dance_start_time, mp_test_active
    global is_dancing, is_stretching, is_locked, is_tracking, current_limit_seconds, base_limit_seconds, active_time, current_score
    
    mp_test_active = False

    avg_score = int(sum(mp_score_samples) / len(mp_score_samples)) if mp_score_samples else 0
    duration = int(time.time() - mp_dance_start_time)
    calories = int(duration * 0.1 * (avg_score / 100.0))

    print(f"MediaPipe Dance finished! Avg: {avg_score}, Dur: {duration}s, Cal: {calories}")
    data_store.record_dance(avg_score, duration, calories)
    
    # Reset tracking state
    is_dancing = False
    is_stretching = False
    is_locked = False
    active_time = 0
    current_score = 0
    current_limit_seconds = base_limit_seconds
    is_tracking = True
    mp_score_samples = []

    return jsonify({
        "status": "success",
        "average_score": avg_score,
        "duration_seconds": duration,
        "calories": calories
    })


@sock.route('/ws/keypoints_mp')
def ws_keypoints_mp(ws):
    """WebSocket endpoint identical to /ws/keypoints but uses MediaPipe Lite instead of YOLO."""
    global mp_test_active, mp_current_score, mp_ref_data, mp_ref_frame_index
    global mp_total_ref_frames, mp_score_samples, mp_dance_start_time
    global mp_calibration_status, mp_calibration_message, mp_calibration_color
    global mp_calibration_success_time, mp_countdown_start_time

    import base64
    import mediapipe as mp_lib

    landmarker = get_mp_landmarker()
    if landmarker is None:
        ws.send(json.dumps({"status": "error", "message": "MediaPipe model failed to load"}))
        return

    # Reset calibration state
    mp_calibration_status = "calibrating"
    mp_calibration_message = "Step back to show full body (legs required)"
    mp_calibration_color = [0, 0, 255]
    mp_calibration_success_time = 0
    mp_countdown_start_time = 0
    mp_current_score = 0

    # Send config to frontend — MediaPipe is faster so use better quality
    ws.send(json.dumps({
        "type": "config",
        "imgsz": 640,
        "skip": 1  # Process every 2nd frame
    }))
    print("MediaPipe WebSocket connected.")

    while mp_test_active:
        try:
            # Drain queue — always grab latest frame, discard old ones
            latest_msg = None
            while True:
                try:
                    msg = ws.receive(timeout=0.01)
                    if msg:
                        latest_msg = msg
                    else:
                        break
                except:
                    break

            if not latest_msg:
                time.sleep(0.01)
                continue

            payload = json.loads(latest_msg)
            if payload.get("type") != "frame" or "image" not in payload:
                continue

            # Decode base64 frame
            base64_str = payload["image"]
            if "," in base64_str:
                base64_str = base64_str.split(",")[1]

            img_data = base64.b64decode(base64_str)
            np_arr = np.frombuffer(img_data, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if frame is None:
                continue

            h, w, _ = frame.shape

            # Run MediaPipe inference
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp_lib.Image(image_format=mp_lib.ImageFormat.SRGB, data=rgb_frame)
            result = landmarker.detect(mp_image)

            kps_export = None

            if result.pose_landmarks and len(result.pose_landmarks) > 0:
                landmarks = result.pose_landmarks[0]  # First detected person

                # Convert to COCO 17 format for scoring
                coco_kps = mp_landmarks_to_coco17(landmarks, w, h)

                def is_vis(idx):
                    return coco_kps[idx][2] > 0.3

                # Calibration logic — identical to YOLO version
                if mp_calibration_status == "calibrating":
                    head_ok = is_vis(0)
                    shoulders_ok = is_vis(5) and is_vis(6)
                    hips_ok = is_vis(11) or is_vis(12)
                    legs_ok = is_vis(13) or is_vis(14) or is_vis(15) or is_vis(16)

                    if head_ok and shoulders_ok and hips_ok and legs_ok:
                        mp_calibration_message = "PERFECT - Hold still!"
                        mp_calibration_color = [0, 255, 0]
                        if mp_calibration_success_time == 0:
                            mp_calibration_success_time = time.time()
                        elif time.time() - mp_calibration_success_time > 2.0:
                            mp_calibration_status = "countdown"
                            mp_countdown_start_time = time.time()
                    else:
                        mp_calibration_message = "Step back to show full body (legs required)"
                        mp_calibration_color = [0, 0, 255]
                        mp_calibration_success_time = 0

                elif mp_calibration_status == "countdown":
                    elapsed = time.time() - mp_countdown_start_time
                    if elapsed < 1.0:
                        mp_calibration_message = "3"
                    elif elapsed < 2.0:
                        mp_calibration_message = "2"
                    elif elapsed < 3.0:
                        mp_calibration_message = "1"
                    else:
                        mp_calibration_status = "playing"
                        mp_calibration_message = ""
                        mp_dance_start_time = time.time()

                elif mp_calibration_status == "playing":
                    if mp_ref_data and mp_total_ref_frames > 0:
                        mp_current_score = yolo_best_score_in_window(
                            coco_kps, mp_ref_data['frames'], mp_ref_frame_index
                        )
                        mp_score_samples.append(mp_current_score)

                        # Time-based frame sync
                        ref_fps = mp_ref_data.get('fps', 30)
                        elapsed_time = time.time() - mp_dance_start_time
                        mp_ref_frame_index = int(elapsed_time * ref_fps) % mp_total_ref_frames

                        import random as _rand
                        if _rand.random() < 0.06:
                            vis_joints = [i for i in range(17) if coco_kps[i][2] > 0.3]
                            avg30 = int(sum(mp_score_samples[-30:]) / min(len(mp_score_samples), 30)) if mp_score_samples else 0
                            print(f"  [MP-Dance] t={elapsed_time:.1f}s refFrame={mp_ref_frame_index}/{mp_total_ref_frames}"
                                  f" score={mp_current_score} avg30={avg30}"
                                  f" visJoints={len(vis_joints)}/17"
                                  f" samples={len(mp_score_samples)}")

                    c = get_color_for_score(mp_current_score)
                    mp_calibration_color = [c[0], c[1], c[2]]

                # Export keypoints to frontend (COCO 17 format)
                kps_export = [[float(coco_kps[i][0]), float(coco_kps[i][1]), float(coco_kps[i][2])] for i in range(17)]

            else:
                # No person detected
                if mp_calibration_status == "calibrating":
                    mp_calibration_message = "Step back to show full body (legs required)"
                    mp_calibration_color = [0, 0, 255]
                    mp_calibration_success_time = 0
                elif mp_calibration_status == "playing":
                    mp_current_score = 0
                    mp_calibration_color = [255, 0, 0]

            ws.send(json.dumps({
                "status": mp_calibration_status,
                "message": mp_calibration_message,
                "color": mp_calibration_color,
                "score": mp_current_score,
                "keypoints": kps_export
            }))

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"MP WS Loop Error: {e}")
            pass

        time.sleep(0.005)  # 5ms sleep — MediaPipe is faster than YOLO

    print("MediaPipe WebSocket stream ended.")
    try:
        ws.close()
    except:
        pass

@app.route('/api/license_status', methods=['GET'])
def api_license_status():
    license_data = data_store.get_license()
    if license_data and license_data.get('status') == 'active':
        key = license_data.get('key')
        if key.startswith("TEST1234"):
            return jsonify({"status": "active", "key": key})
            
        # Verify with Vercel Proxy in background
        url = "https://move-it-green-two.vercel.app/api/validate"
        payload = json.dumps({"license_key": key}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        
        try:
            response = urllib.request.urlopen(req, timeout=5)
            result = json.loads(response.read().decode('utf-8'))
            if result.get("valid") == True:
                return jsonify({"status": "active", "key": key})
            else:
                data_store.set_license(key, "inactive")
                return jsonify({"status": "inactive"})
        except Exception as e:
            # If offline or server error, trust the local cache to allow them to work
            print(f"Validation check failed, trusting cache: {e}")
            return jsonify({"status": "active", "key": key})
            
    return jsonify({"status": "inactive"})

@app.route('/api/activate_license', methods=['POST'])
def api_activate_license():
    data = request.json or {}
    key = data.get('key')
    if not key:
        return jsonify({"status": "error", "message": "No key provided"}), 400
        
    machine_id = get_machine_uuid()
    
    # STUB: For testing UI quickly
    if key.startswith("TEST1234"):
        data_store.set_license(key, "active")
        return jsonify({"status": "success"})
        
    # Call the secure Vercel proxy
    url = "https://move-it-green-two.vercel.app/api/activate"
    payload = json.dumps({
        "license_key": key,
        "instance_name": machine_id
    }).encode('utf-8')
    
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    
    try:
        response = urllib.request.urlopen(req)
        result = json.loads(response.read().decode('utf-8'))
        
        if result.get("activated") == True:
            data_store.set_license(key, "active")
            return jsonify({"status": "success"})
        else:
            return jsonify({"status": "error", "message": result.get("error", "Invalid or inactive license key")}), 400
            
    except Exception as e:
        print(f"Activation error: {e}")
        return jsonify({"status": "error", "message": "Could not connect to activation server"}), 500

@app.route('/Images/<path:filename>')
def serve_image(filename):
    images_dir = os.path.join(BASE_DIR, 'Images')
    response = send_from_directory(images_dir, filename, max_age=0)
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

@app.route('/api/set_stretch_target', methods=['POST'])
def api_set_stretch_target():
    global current_stretch_target
    data = request.json or {}
    current_stretch_target = data.get('target', 'im1.png')
    print(f"Stretch target changed to: {current_stretch_target}")
    return jsonify({"status": "ok"})

@app.route('/api/start_tracking', methods=['POST'])
def api_start_tracking():
    global base_limit_seconds, current_limit_seconds, active_time, is_tracking, window, is_locked
    data = request.json
    base_limit_seconds = data.get('limit', 30 * 60)
    current_limit_seconds = base_limit_seconds
    active_time = 0
    is_tracking = True
    is_locked = False

    # Save setting to DB (convert seconds back to minutes for saving)
    data_store.set_setting("work_interval_minutes", int(base_limit_seconds / 60))
    
    print(f"Tracking started. Limit set to {current_limit_seconds} seconds.")
    if window:
        window.minimize()  # Hide instead of minimize since we use system tray
        show_tray_notification_once()
    return jsonify({"status": "ok"})

@app.route('/api/update_limit', methods=['POST'])
def api_update_limit():
    global base_limit_seconds, current_limit_seconds
    data = request.json
    base_limit_seconds = data.get('limit', 30 * 60)
    current_limit_seconds = base_limit_seconds
    data_store.set_setting("work_interval_minutes", int(base_limit_seconds / 60))
    print(f"Limit updated to {current_limit_seconds} seconds.")
    return jsonify({"status": "ok"})

@app.route('/api/stop_tracking', methods=['POST'])
def api_stop_tracking():
    global is_tracking
    is_tracking = False
    print("Tracking manually stopped.")
    return jsonify({"status": "ok", "is_tracking": is_tracking})

@app.route('/api/tracking_status', methods=['GET'])
def api_tracking_status():
    return jsonify({"is_tracking": is_tracking})

@app.route('/api/app_state', methods=['GET'])
def api_app_state():
    return jsonify({
        "is_tracking": is_tracking,
        "is_locked": is_locked,
        "is_dancing": is_dancing,
        "is_stretching": is_stretching,
        "active_time": active_time,
        "limit": current_limit_seconds,
        "manual_meeting_mode": manual_meeting_mode
    })

@app.route('/api/toggle_meeting_mode', methods=['POST'])
def api_toggle_meeting_mode():
    global manual_meeting_mode
    manual_meeting_mode = not manual_meeting_mode
    status = "ON" if manual_meeting_mode else "OFF"
    print(f"Manual Meeting Mode toggled via UI: {status}")
    return jsonify({"status": "ok", "manual_meeting_mode": manual_meeting_mode})

@app.route('/api/skip', methods=['POST'])
@app.route('/api/skip_workout', methods=['POST'])
def api_skip():
    global current_limit_seconds, active_time, is_locked, is_tracking, window
    current_limit_seconds = int(current_limit_seconds / 2)
    if current_limit_seconds < MIN_LIMIT_SECONDS:
        current_limit_seconds = MIN_LIMIT_SECONDS
    print(f"SKIPPED. Next limit is {current_limit_seconds} seconds.")
    
    data_store.record_skip()
    
    active_time = 0
    is_locked = False
    is_tracking = True
    if window:
        window.minimize()
    return jsonify({"status": "ok", "new_limit": current_limit_seconds})

@app.route('/api/songs', methods=['GET'])
def api_songs():
    global songs_registry
    # Reload registry in case it changed
    if os.path.exists(REGISTRY_PATH):
        with open(REGISTRY_PATH, 'r') as f:
            songs_registry = json.load(f)
    return jsonify(songs_registry)

@app.route('/api/stretches', methods=['GET'])
def api_stretches():
    return jsonify(stretches_data)

@app.route('/api/dance_started', methods=['POST'])
def api_dance_started():
    global is_dancing, is_stretching, is_calibrating, is_locked, is_tracking, calibration_success_time, score_samples
    global reference_data, total_ref_frames, ref_frame_index
    
    data = request.json or {}
    video_id = data.get('video_id', 'H_rRYdNCu_k')
    
    # Load the specific pose data
    pose_path = os.path.join(SCRIPT_DIR, f'pose_data_{video_id}.json')
    if not os.path.exists(pose_path):
        # Fallback to the original default
        pose_path = DATA_PATH
        
    try:
        with open(pose_path, 'r') as f:
            reference_data = json.load(f)
        total_ref_frames = len(reference_data['frames'])
        ref_frame_index = 0
        print(f"Loaded {total_ref_frames} reference frames for video {video_id}")
    except Exception as e:
        print(f"Error loading pose data for {video_id}: {e}")
        reference_data = None
        total_ref_frames = 0
        
    is_dancing = True
    is_stretching = False
    is_calibrating = True
    is_locked = True
    is_tracking = False  # PAUSE the work timer during dancing
    calibration_success_time = 0
    is_calibrating = True
    is_locked = True
    is_tracking = False
    dance_start_time = time.time()
    score_samples = []
    session_movement_score = 0.0
    calibration_success_time = 0
    print(f"Dance started for video {video_id}")
    return jsonify({"status": "success"})

@app.route('/api/stretch_started', methods=['POST'])
def api_stretch_started():
    global is_stretching, is_dancing, is_calibrating, is_locked, is_tracking, calibration_success_time, score_samples
    global current_stretch_target, dance_start_time
    
    is_stretching = True
    is_dancing = False
    is_calibrating = True
    is_locked = True
    is_tracking = False
    calibration_success_time = 0
    score_samples = []
    dance_start_time = time.time()
    
    data = request.json or {}
    current_stretch_target = data.get('target', 'im1.png')
    print(f"Stretching phase started. Target: {current_stretch_target}")
    return jsonify({"status": "ok"})

@app.route('/api/calibration_status')
def api_calibration_status():
    return jsonify(calibration_status)

@app.route('/api/score')
def api_score():
    return jsonify({"score": current_score})

@app.route('/api/dance_finished', methods=['POST'])
def api_dance_finished():
    global is_dancing, is_locked, is_tracking, active_time, current_score
    global current_limit_seconds, base_limit_seconds, score_samples, window, dance_start_time
    global session_movement_score
    
    data = request.json or {}
    print(f"DEBUG /api/dance_finished received: {data}")
    
    avg_score = data.get('score', 0)
    duration = data.get('duration_seconds', 0)
    calories = data.get('calories', 0)
    
    # Fallback to python state if client didn't send data or sent bad data
    if (not duration or duration == 0) and is_dancing:
        duration = int(time.time() - dance_start_time)
        
    # Safeguard against huge unix timestamps due to dance_start_time being 0
    if duration > 1000000:
        duration = 0
        
    if avg_score == 0 and score_samples:
        avg_score = int(sum(score_samples) / len(score_samples))
        
    if calories == 0:
        calories = int(session_movement_score * 0.05) if session_movement_score else int(duration * 0.12)
        
    print(f"Dance finished! Average score: {avg_score} (Duration: {duration}s), Calories: {calories}")
    data_store.record_dance(avg_score, duration, calories)
        
    is_dancing = False
    is_stretching = False
    is_locked = False
    active_time = 0
    current_score = 0
    current_limit_seconds = base_limit_seconds
    is_tracking = True
    score_samples = []
    
    return jsonify({
        "status": "success",
        "average_score": avg_score,
        "duration_seconds": duration,
        "calories": calories
    })
@app.route('/api/stretch_finished', methods=['POST'])
def api_stretch_finished():
    global is_stretching, is_locked, is_tracking, active_time, current_score
    global current_limit_seconds, base_limit_seconds, score_samples, window, dance_start_time
    
    data = request.json or {}
    avg = data.get('score', 0)
    duration = int(time.time() - dance_start_time) if dance_start_time else 0
    # Fallback to backend score_samples only if frontend didn't send a score
    if avg == 0 and score_samples:
        avg = int(sum(score_samples) / len(score_samples))
    print(f"Stretch finished! Average score: {avg} (duration: {duration}s)")
    
    data_store.record_stretch(avg, duration)
    
    # Reset everything and restart tracking
    is_dancing = False
    is_stretching = False
    is_locked = False
    active_time = 0
    current_score = 0
    current_limit_seconds = base_limit_seconds  # Reset skip penalty
    is_tracking = True  # Resume work tracking
    score_samples = []
    
    return jsonify({"status": "ok", "average_score": avg})

@app.route('/api/dashboard_data')
def api_dashboard_data():
    return jsonify(data_store.get_all_dashboard_data())

@app.route('/api/close', methods=['POST'])
def api_close():
    global window
    print("Close requested by user. Fully exiting.")
    if window:
        window.destroy()
    cleanup_and_exit()
    return jsonify({"status": "ok"})

@app.route('/api/hide', methods=['POST'])
def api_hide():
    global window
    print("Hide requested. Hiding window to system tray.")
    if window:
        window.minimize()
    return jsonify({"status": "ok"})

# =============================================================================
# YOLO26 Test Mode
# =============================================================================

# COCO 17-keypoint skeleton connections
YOLO_SKELETON = [
    (0, 1), (0, 2), (1, 3), (2, 4),  # Head
    (5, 6),  # Shoulders
    (5, 7), (7, 9),  # Left arm
    (6, 8), (8, 10),  # Right arm
    (5, 11), (6, 12),  # Torso
    (11, 12),  # Hips
    (11, 13), (13, 15),  # Left leg
    (12, 14), (14, 16),  # Right leg
]

# COCO keypoint names for reference:
# 0:nose, 1:left_eye, 2:right_eye, 3:left_ear, 4:right_ear,
# 5:left_shoulder, 6:right_shoulder, 7:left_elbow, 8:right_elbow,
# 9:left_wrist, 10:right_wrist, 11:left_hip, 12:right_hip,
# 13:left_knee, 14:right_knee, 15:left_ankle, 16:right_ankle

def calculate_yolo_score(live_kps, ref_landmarks):
    """Calculate score comparing YOLO live keypoints to YOLO reference data.
    Uses vector-angle matching on key limbs with generous tolerance."""
    if live_kps is None or not ref_landmarks or len(ref_landmarks) < 17:
        return 0

    # live_kps: numpy array shape (17, 3) — x, y, conf
    # ref_landmarks: list of dicts with x, y, visibility (normalized 0-1)

    def vec(kps, i, j):
        """Get normalized direction vector between two keypoints."""
        if isinstance(kps, np.ndarray):
            x1, y1, c1 = kps[i]
            x2, y2, c2 = kps[j]
            if c1 < 0.3 or c2 < 0.3:
                return None
        else:
            x1, y1 = kps[i]['x'], kps[i]['y']
            x2, y2 = kps[j]['x'], kps[j]['y']
            if kps[i].get('visibility', 1.0) < 0.3 or kps[j].get('visibility', 1.0) < 0.3:
                return None
        v = np.array([x2 - x1, y2 - y1])
        n = np.linalg.norm(v)
        if n < 1e-6:
            return None
        return v / n

    def angle_diff(v1, v2):
        dot = np.clip(np.dot(v1, v2), -1.0, 1.0)
        return math.degrees(math.acos(dot))

    # Limb pairs: (start, end) — focusing on arms and legs
    # The reference dancer is facing the camera. The user is facing the camera.
    # Dance games use MIRROR MATCHING. 
    # If the dancer raises their LEFT arm (on the right side of the screen),
    # the user raises their RIGHT arm (on the right side of their mirrored camera).
    # Therefore, we must map the user's RIGHT limb to the dancer's LEFT limb.
    
    # Format: (live_start, live_end, ref_start, ref_end)
    limb_mappings = [
        # ARMS
        (6, 10, 5, 9),   # Live Right Shoulder/Wrist -> Ref Left Shoulder/Wrist
        (5, 9, 6, 10),   # Live Left Shoulder/Wrist -> Ref Right Shoulder/Wrist
        (6, 8, 5, 7),    # Live Right Upper Arm -> Ref Left Upper Arm
        (5, 7, 6, 8),    # Live Left Upper Arm -> Ref Right Upper Arm
        (8, 10, 7, 9),   # Live Right Forearm -> Ref Left Forearm
        (7, 9, 8, 10),   # Live Left Forearm -> Ref Right Forearm
        
        # LEGS
        (12, 16, 11, 15), # Live Right Leg -> Ref Left Leg
        (11, 15, 12, 16), # Live Left Leg -> Ref Right Leg
        (12, 14, 11, 13), # Live Right Thigh -> Ref Left Thigh
        (11, 13, 12, 14), # Live Left Thigh -> Ref Right Thigh
    ]

    LIMB_LABELS = {
        (6,10,5,9): "R-Arm", (5,9,6,10): "L-Arm",
        (6,8,5,7):  "R-UArm",(5,7,6,8):  "L-UArm",
        (8,10,7,9): "R-FA",  (7,9,8,10): "L-FA",
        (12,16,11,15):"R-Leg",(11,15,12,16):"L-Leg",
        (12,14,11,13):"R-Thi",(11,13,12,14):"L-Thi",
    }
    scores = []
    debug_parts = []
    for (l_i, l_j, r_i, r_j) in limb_mappings:
        v_live = vec(live_kps, l_i, l_j)
        v_ref = vec(ref_landmarks, r_i, r_j)
        label = LIMB_LABELS.get((l_i, l_j, r_i, r_j), f"{l_i}→{r_i}")
        if v_live is not None and v_ref is not None:
            diff = angle_diff(v_live, v_ref)
            if diff <= 22:
                s = 100
            elif diff >= 75:
                s = 0
            else:
                s = 100 - ((diff - 22) / 53.0) * 100
            scores.append(s)
            debug_parts.append(f"{label}:{s:.0f}({diff:.0f}°)")
        else:
            skip_reason = "no-live" if v_live is None else "no-ref"
            debug_parts.append(f"{label}:SKIP({skip_reason})")

    if not scores or len(scores) < 3:
        import random
        if random.random() < 0.15:
            print(f"  [YOLO-Score] ZERO — not enough limbs ({len(scores)}). {' '.join(debug_parts)}")
        return 0

    final = max(0, min(100, int(sum(scores) / len(scores))))
    import random
    if random.random() < 0.08:
        print(f"  [YOLO-Score] {final} ({len(scores)} limbs) | {' | '.join(debug_parts)}")
    return final

def yolo_best_score_in_window(live_kps, ref_frames, center_idx, window_size=4):
    """Compare against nearby reference frames and return the best score.
    Wider window than MediaPipe for more forgiving matching."""
    best = 0
    total = len(ref_frames)
    for offset in range(-window_size, window_size + 1):
        idx = (center_idx + offset) % total
        s = calculate_yolo_score(live_kps, ref_frames[idx]['landmarks'])
        if s > best:
            best = s
    return best

yolo_calibration_status = "calibrating"
yolo_calibration_message = ""
yolo_calibration_color = (0, 0, 255)
yolo_calibration_success_time = 0
yolo_countdown_start_time = 0
yolo_model = None

def get_yolo_model():
    global yolo_model
    if yolo_model is None:
        try:
            from ultralytics import YOLO
            model_path = "yolo26n-pose.pt"
            yolo_model = YOLO(model_path)
            print(f"SUCCESS: Loaded custom YOLO model -> {model_path}")
        except Exception as e:
            print(f"Failed to load yolo26n-pose.pt due to: {e}")
            from ultralytics import YOLO
            model_path = "yolo11n-pose.pt"
            yolo_model = YOLO(model_path)
            print(f"SUCCESS: Fallback loaded custom YOLO model -> {model_path}")
    return yolo_model

@app.route('/api/start_yolo_test', methods=['POST'])
def api_start_yolo_test():
    global yolo_test_active, yolo_ref_data, yolo_ref_frame_index, yolo_total_ref_frames, yolo_current_score
    global yolo_score_samples, yolo_dance_start_time

    data = request.get_json(silent=True) or {}
    video_id = data.get('video_id', 'ffd1WHeYG64')
    yolo_pose_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f'yolo_pose_data_{video_id}.json')

    if not os.path.exists(yolo_pose_path):
        return jsonify({'status': 'error', 'message': f'YOLO pose data not found for {video_id}'}), 404

    with open(yolo_pose_path, 'r') as f:
        yolo_ref_data = json.load(f)
    yolo_total_ref_frames = len(yolo_ref_data['frames'])
    yolo_ref_frame_index = 0
    yolo_current_score = 0
    # Reset tracking state
    yolo_score_samples = []
    yolo_dance_start_time = time.time()
    yolo_test_active = True
    
    ref_fps_v = yolo_ref_data.get('fps', 30)
    print(f'')
    print(f'════════════════════════════════════════')
    print(f'  YOLO DANCE SESSION STARTED')
    print(f'  Video: {video_id}  |  Ref frames: {yolo_total_ref_frames} @ {ref_fps_v}fps')
    print(f'  Benchmark tier: imgsz={yolo_benchmark_tier["imgsz"]} skip={yolo_benchmark_tier["skip"]}')
    print(f'════════════════════════════════════════')
    return jsonify({'status': 'ok', 'video_id': video_id, 'total_frames': yolo_total_ref_frames})

@app.route('/api/yolo_dance_finished', methods=['POST'])
def api_yolo_dance_finished():
    global yolo_score_samples, yolo_dance_start_time, yolo_test_active
    yolo_test_active = False
    
    avg_score = int(sum(yolo_score_samples) / len(yolo_score_samples)) if yolo_score_samples else 0
    duration = int(time.time() - yolo_dance_start_time)
    calories = int(duration * 0.1 * (avg_score / 100.0))

    # Score distribution breakdown
    dist = {'0-30': 0, '31-50': 0, '51-65': 0, '66-80': 0, '81-100': 0}
    for s in yolo_score_samples:
        if s <= 30: dist['0-30'] += 1
        elif s <= 50: dist['31-50'] += 1
        elif s <= 65: dist['51-65'] += 1
        elif s <= 80: dist['66-80'] += 1
        else: dist['81-100'] += 1
    total_s = len(yolo_score_samples) or 1
    print(f'')
    print(f'════════════════════════════════════════')
    print(f'  YOLO DANCE SESSION FINISHED')
    print(f'  Avg Score : {avg_score}/100')
    print(f'  Duration  : {duration}s  |  Samples: {len(yolo_score_samples)}')
    print(f'  Score dist: 0-30={dist["0-30"]} ({100*dist["0-30"]//total_s}%)'
          f' 31-50={dist["31-50"]} ({100*dist["31-50"]//total_s}%)'
          f' 51-65={dist["51-65"]} ({100*dist["51-65"]//total_s}%)'
          f' 66-80={dist["66-80"]} ({100*dist["66-80"]//total_s}%)'
          f' 81+=  {dist["81-100"]} ({100*dist["81-100"]//total_s}%)')
    print(f'════════════════════════════════════════')
    data_store.record_dance(avg_score, duration, calories)
    
    return jsonify({
        "status": "success",
        "average_score": avg_score,
        "duration_seconds": duration,
        "calories": calories
    })

@app.route('/api/stop_yolo_test', methods=['POST'])
def api_stop_yolo_test():
    global yolo_test_active, yolo_current_score
    yolo_test_active = False
    yolo_current_score = 0
    print('YOLO Test stopped.')
    return jsonify({'status': 'ok'})

@sock.route('/ws/keypoints')
def ws_keypoints(ws):
    """WebSocket endpoint that streams YOLO keypoints and scores asynchronously."""
    global yolo_test_active, yolo_latest_keypoints, yolo_current_score
    global yolo_calibration_status, yolo_calibration_message, yolo_calibration_color
    global yolo_calibration_success_time, yolo_countdown_start_time
    global yolo_ref_frame_index, latest_frame, yolo_dance_start_time
    global yolo_score_samples
    
    # Benchmark is now pre-run at startup
    tier = yolo_benchmark_tier
    model = get_yolo_model()
    
    frame_counter = 0
    yolo_calibration_status = "calibrating"
    yolo_calibration_message = "Step back to show full body (legs required)"
    yolo_calibration_color = [0, 0, 255] # JSON uses lists
    yolo_calibration_success_time = 0
    yolo_countdown_start_time = 0
    yolo_current_score = 0
    
    # Send initial configuration tier to the frontend
    ws.send(json.dumps({
        "type": "config",
        "imgsz": tier["imgsz"],
        "skip": tier["skip"]
    }))
    print(f"WebSocket connected. Sent config tier: {tier['imgsz']}x{tier['imgsz']} (skip {tier['skip']})")
    
    import base64
    
    while yolo_test_active:
        try:
            # DRAIN QUEUE: Always grab the LATEST frame, discard older ones
            latest_msg = None
            while True:
                try:
                    # Non-blocking receive (0.01s is small enough to be instant)
                    msg = ws.receive(timeout=0.01)
                    if msg: latest_msg = msg
                    else: break
                except:
                    break
            
            if not latest_msg:
                time.sleep(0.01)
                continue
                
            payload = json.loads(latest_msg)
            if payload.get("type") != "frame" or "image" not in payload:
                continue
                
            base64_str = payload["image"]
            if "," in base64_str:
                base64_str = base64_str.split(",")[1]
                
            img_data = base64.b64decode(base64_str)
            np_arr = np.frombuffer(img_data, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            
            if frame is None:
                continue
                
            latest_frame = frame.copy() 
            h, w, _ = frame.shape
            
            results = model(frame, verbose=False, imgsz=tier["imgsz"])
            
            kps_export = None
            if len(results) > 0 and results[0].keypoints is not None and len(results[0].keypoints.data) > 0:
                kps = results[0].keypoints.data[0].cpu().numpy()
                
                # Helper to check visibility (Lower threshold for robustness)
                def is_vis(idx): return kps[idx][2] > 0.3
                
                if yolo_calibration_status == "calibrating":
                    head_ok = is_vis(0) or is_vis(1) or is_vis(2)
                    shoulders_ok = is_vis(5) and is_vis(6)
                    hips_ok = is_vis(11) or is_vis(12) # Only one hip needed for calibration
                    legs_ok = is_vis(13) or is_vis(14) or is_vis(15) or is_vis(16) # Any leg joint
                    
                    if head_ok and shoulders_ok and hips_ok and legs_ok:
                        yolo_calibration_message = "PERFECT - Hold still!"
                        yolo_calibration_color = [0, 255, 0]
                        if yolo_calibration_success_time == 0:
                            yolo_calibration_success_time = time.time()
                        elif time.time() - yolo_calibration_success_time > 2.0:
                            yolo_calibration_status = "countdown"
                            yolo_countdown_start_time = time.time()
                    else:
                        yolo_calibration_message = "Step back to show full body (legs required)"
                        yolo_calibration_color = [0, 0, 255]
                        yolo_calibration_success_time = 0
                        
                elif yolo_calibration_status == "countdown":
                    elapsed = time.time() - yolo_countdown_start_time
                    if elapsed < 1.0: yolo_calibration_message = "3"
                    elif elapsed < 2.0: yolo_calibration_message = "2"
                    elif elapsed < 3.0: yolo_calibration_message = "1"
                    else:
                        yolo_calibration_status = "playing"
                        yolo_calibration_message = ""
                        yolo_dance_start_time = time.time()
                        
                elif yolo_calibration_status == "playing":
                    if yolo_ref_data and yolo_total_ref_frames > 0:
                        kps_norm = kps.copy()
                        kps_norm[:, 0] = kps_norm[:, 0] / w
                        kps_norm[:, 1] = kps_norm[:, 1] / h
                        yolo_current_score = yolo_best_score_in_window(
                            kps_norm, yolo_ref_data['frames'], yolo_ref_frame_index
                        )
                        yolo_score_samples.append(yolo_current_score)
                        
                        yolo_ref_fps = yolo_ref_data.get('fps', 30)
                        elapsed_time = time.time() - yolo_dance_start_time
                        prev_idx = yolo_ref_frame_index
                        yolo_ref_frame_index = int(elapsed_time * yolo_ref_fps) % yolo_total_ref_frames

                        # Verbose periodic log
                        import random as _rand
                        if _rand.random() < 0.06:
                            vis_joints = [i for i in range(17) if kps_norm[i][2] > 0.3]
                            avg_so_far = int(sum(yolo_score_samples[-30:]) / min(len(yolo_score_samples), 30)) if yolo_score_samples else 0
                            print(f"  [YOLO-Dance] t={elapsed_time:.1f}s refFrame={yolo_ref_frame_index}/{yolo_total_ref_frames}"
                                  f" score={yolo_current_score} avg30={avg_so_far}"
                                  f" visJoints={len(vis_joints)}/17"
                                  f" samples={len(yolo_score_samples)}")
                        
                    c = get_color_for_score(yolo_current_score)
                    yolo_calibration_color = [c[0], c[1], c[2]]
                
                # Convert kps to relative 0-1 format for the frontend canvas
                kps_export = []
                for idx_kp in range(17):
                    x, y, c = kps[idx_kp]
                    # Do not flip X again, frontend image is already mirrored
                    kps_export.append([float(x / w), float(y / h), float(c)])
            
            else:
                # No person detected. Reset calibration timer if calibrating.
                if yolo_calibration_status == "calibrating":
                    yolo_calibration_message = "Step back to show full body (legs required)"
                    yolo_calibration_color = [0, 0, 255]
                    yolo_calibration_success_time = 0
                elif yolo_calibration_status == "playing":
                    yolo_current_score = 0
                    yolo_calibration_color = [255, 0, 0]
                    
            ws.send(json.dumps({
                "status": yolo_calibration_status,
                "message": yolo_calibration_message,
                "color": yolo_calibration_color,
                "score": yolo_current_score,
                "keypoints": kps_export
            }))
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"WS Loop Error: {e}")
            pass # Ignore broken pipe if client disconnects
            
        time.sleep(0.01)
    
    print("WebSocket stream ended.")
    try: ws.close()
    except: pass

@app.route('/api/check_update', methods=['GET'])
def check_update():
    try:
        import urllib.request
        import json
        req = urllib.request.Request(UPDATE_CHECK_URL, headers={'User-Agent': 'MoveItApp/1.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode())
            if data.get('latest_version') and data['latest_version'] != CURRENT_APP_VERSION:
                return jsonify({
                    "has_update": True,
                    "latest_version": data['latest_version'],
                    "current_version": CURRENT_APP_VERSION,
                    "download_url": data.get('download_url', 'https://move-it.app')
                })
    except Exception as e:
        print(f"Update check failed: {e}")
    return jsonify({"has_update": False, "current_version": CURRENT_APP_VERSION})

@app.route('/api/open_url', methods=['POST'])
def open_url():
    data = request.json
    url = data.get('url')
    if url:
        import webbrowser
        webbrowser.open(url)
    return jsonify({"status": "ok"})

@app.route('/api/log', methods=['POST'])
def api_log():
    data = request.json or {}
    msg = data.get('msg', '')
    logger.info(f"[Frontend] {msg}")
    return jsonify({"status": "ok"})

@app.route('/api/get_logs', methods=['GET'])
def get_logs():
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
            return jsonify({"logs": content})
        return jsonify({"logs": "Log file not found."})
    except Exception as e:
        return jsonify({"logs": f"Error reading logs: {e}"})

@app.route('/api/tray/<action>')
def api_tray_action(action):
    global is_tracking, manual_meeting_mode
    if action == 'open_dashboard' and window:
        window.show()
        window.restore()
    elif action == 'pause_tracking':
        is_tracking = False
    elif action == 'toggle_meeting_mode':
        manual_meeting_mode = not manual_meeting_mode
    elif action == 'quit':
        if window: window.destroy()
        cleanup_and_exit()
    return "OK"

# Static Files
@app.route('/')
def index(): return send_from_directory(app.static_folder, 'index.html')

@app.route('/video_feed')
def video_feed(): return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/yolo_video/<video_id>.<ext>')
def serve_yolo_video_dynamic(video_id, ext):
    if ext not in ['webm', 'mp4']:
        return "Invalid extension", 400
    
    # Special priority for Macarena Original HD
    if video_id == "macarena2022":
        # Always check for the web-friendly version first, then original
        for filename in ["macarena_web.mp4", "The Macarena Dance 2022.mp4"]:
            if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)):
                return send_from_directory(os.path.dirname(os.path.abspath(__file__)), filename)
                
    if video_id == "moveit_justdance":
        for filename in ["I Like To Move It Just Dance.mp4"]:
            if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)):
                return send_from_directory(os.path.dirname(os.path.abspath(__file__)), filename)
    
    # Check if processed video exists for other IDs
    processed_name = f'yolo_video_{video_id}.{ext}'
    processed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), processed_name)
    if os.path.exists(processed_path):
        return send_from_directory(os.path.dirname(os.path.abspath(__file__)), processed_name)
        
    return "Video not found", 404


# ── Reference landmark extraction for JS-side cosine scoring ─────────────────
# Extracts MediaPipe pose landmarks from the local dance video in a background
# thread at startup, so the data is ready before the user even opens dance mode.
# JS polls /api/ref_landmarks/status/<id> and fetches data when ready=true.

_ref_landmarks_data   = {}   # video_id -> list[{t, lms}]  (ready data)
_ref_landmarks_status = {}   # video_id -> "extracting" | "ready" | "error:<msg>"

LITE_MODEL_PATH = os.path.join(BASE_DIR, 'pose_landmarker_lite.task')

def _resolve_video_path(video_id):
    if video_id == "macarena2022":
        for filename in ["macarena_web.mp4", "The Macarena Dance 2022.mp4"]:
            p = os.path.join(BASE_DIR, filename)
            if os.path.exists(p):
                return p
    if video_id == "moveit_justdance":
        for filename in ["I Like To Move It Just Dance.mp4"]:
            p = os.path.join(BASE_DIR, filename)
            if os.path.exists(p):
                return p
    for ext in ["mp4", "webm"]:
        p = os.path.join(BASE_DIR, f"yolo_video_{video_id}.{ext}")
        if os.path.exists(p):
            return p
    return None

def _do_extract(video_id):
    """Background worker: extract pose landmarks from video, cache to disk."""
    _ref_landmarks_status[video_id] = "extracting"
    cache_path = os.path.join(BASE_DIR, f"ref_landmarks_{video_id}.json")

    try:
        # Fast path: disk cache exists
        if os.path.exists(cache_path):
            with open(cache_path, 'r') as f:
                data = json.load(f)
            _ref_landmarks_data[video_id]   = data
            _ref_landmarks_status[video_id] = "ready"
            print(f"[ref_lms] {video_id}: loaded {len(data)} frames from disk cache")
            return

        # Backward compatibility: Convert legacy pose_data JSON if it exists
        legacy_path = os.path.join(BASE_DIR, f"pose_data_{video_id}.json")
        if os.path.exists(legacy_path):
            print(f"[ref_lms] {video_id}: Converting legacy pose_data to ref_landmarks...")
            try:
                with open(legacy_path, 'r') as f:
                    legacy_data = json.load(f)
                
                frames_data = []
                for frame_info in legacy_data.get("frames", []):
                    t_sec = frame_info.get("timestamp_ms", 0) / 1000.0
                    lms = []
                    # Keep same 33 landmarks mapping or just take what we have
                    for lm in frame_info.get("landmarks", []):
                        lms.append({"x": lm["x"], "y": lm["y"], "v": lm.get("visibility", 1.0)})
                    if lms:
                        frames_data.append({"t": round(t_sec, 4), "lms": lms})
                        
                if frames_data:
                    with open(cache_path, 'w') as f:
                        json.dump(frames_data, f, separators=(',', ':'))
                    _ref_landmarks_data[video_id] = frames_data
                    _ref_landmarks_status[video_id] = "ready"
                    print(f"[ref_lms] {video_id}: Converted {len(frames_data)} frames from legacy data")
                    return
            except Exception as e:
                print(f"[ref_lms] {video_id}: Failed to convert legacy data: {e}")

        video_path = _resolve_video_path(video_id)
        if not video_path:
            _ref_landmarks_status[video_id] = "error:video file not found"
            return

        print(f"[ref_lms] {video_id}: extracting from {video_path} …")
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            _ref_landmarks_status[video_id] = "error:cv2 cannot open video"
            return

        fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        # Use lite model — fast enough for offline extraction, 3× faster than heavy
        model_path = LITE_MODEL_PATH if os.path.exists(LITE_MODEL_PATH) else MODEL_PATH
        print(f"[ref_lms] using model: {os.path.basename(model_path)}")

        opts = vision.PoseLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.IMAGE,
            num_poses=1,
            min_pose_detection_confidence=0.25,
            min_pose_presence_confidence=0.25,
        )
        landmarker = vision.PoseLandmarker.create_from_options(opts)

        frames_data = []
        frame_idx   = 0
        # Adaptive sample rate: target ~30s total extraction time.
        # At ~80ms/frame for MediaPipe lite, we can do ~375 frames in 30s.
        # Spread those evenly across the video.
        TARGET_EXTRACT_SECONDS = 35
        MP_MS_PER_FRAME = 80  # conservative estimate
        max_inferences = int((TARGET_EXTRACT_SECONDS * 1000) / MP_MS_PER_FRAME)
        SAMPLE_EVERY = max(3, int(total / max(max_inferences, 1)))
        last_progress_print = time.time()
        extract_start = time.time()
        last_partial_push = time.time()

        print(f"[ref_lms] {video_id}: {total} frames @ {fps:.1f}fps = {total/fps:.0f}s."
              f" Sampling every {SAMPLE_EVERY} frames (~{total//SAMPLE_EVERY} inferences, target ~{TARGET_EXTRACT_SECONDS}s)")

        while True:
            ret, bgr = cap.read()
            if not ret:
                break
            if frame_idx % SAMPLE_EVERY == 0:
                t_sec  = frame_idx / fps
                # Resize to 480px wide for fastest inference
                if bgr.shape[1] > 480:
                    bgr = cv2.resize(bgr, (480, int(bgr.shape[0] * 480 / bgr.shape[1])))
                rgb    = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = landmarker.detect(mp_img)
                if result.pose_landmarks:
                    lms = result.pose_landmarks[0]
                    frames_data.append({
                        "t": round(t_sec, 4),
                        "lms": [
                            {"x": round(lm.x, 5), "y": round(lm.y, 5),
                             "v": round(float(getattr(lm, "visibility", 1.0)), 3)}
                            for lm in lms
                        ]
                    })
                    # ── PARTIAL PUSH: make partial data available for scoring
                    # every 5 seconds of real time so user can start dancing
                    # before full extraction completes.
                    _now2 = time.time()
                    if _now2 - last_partial_push >= 5.0 and len(frames_data) >= 20:
                        _ref_landmarks_data[video_id] = list(frames_data)  # snapshot
                        if _ref_landmarks_status[video_id] == "extracting":
                            _ref_landmarks_status[video_id] = "partial"
                        last_partial_push = _now2

            frame_idx += 1

            # Progress log every 10 seconds
            _now = time.time()
            if _now - last_progress_print >= 10.0:
                pct = int(frame_idx / max(total, 1) * 100)
                _elapsed = _now - extract_start
                eta = (_elapsed / max(frame_idx, 1)) * (total - frame_idx)
                print(f"[ref_lms] {video_id}: {pct}% ({frame_idx}/{total} frames,"
                      f" {len(frames_data)} poses, ETA {eta:.0f}s)")
                last_progress_print = _now

        cap.release()
        landmarker.close()

        if not frames_data:
            _ref_landmarks_status[video_id] = "error:no poses detected in video"
            print(f"[ref_lms] {video_id}: no poses detected!")
            return

        elapsed_total = time.time() - extract_start
        print(f"[ref_lms] {video_id}: DONE. {len(frames_data)} pose frames"
              f" from {total} total in {elapsed_total:.1f}s")

        with open(cache_path, 'w') as f:
            json.dump(frames_data, f, separators=(',', ':'))

        _ref_landmarks_data[video_id]   = frames_data
        _ref_landmarks_status[video_id] = "ready"

    except Exception as e:
        _ref_landmarks_status[video_id] = f"error:{e}"
        print(f"[ref_lms] {video_id}: EXCEPTION: {e}")
        import traceback; traceback.print_exc()

def ensure_ref_landmarks(video_id):
    """Kick off background extraction if not already running/done."""
    if video_id not in _ref_landmarks_status:
        t = threading.Thread(target=_do_extract, args=(video_id,), daemon=True)
        t.start()

@app.route('/api/ref_landmarks/status/<video_id>')
def api_ref_landmarks_status(video_id):
    ensure_ref_landmarks(video_id)
    status = _ref_landmarks_status.get(video_id, "extracting")
    frames = len(_ref_landmarks_data.get(video_id, []))
    # "partial" or "ready" both mean data is usable for scoring
    ready  = status in ("ready", "partial")
    return jsonify({"status": status, "ready": ready, "frames": frames})

@app.route('/api/ref_landmarks/<video_id>')
def api_ref_landmarks(video_id):
    ensure_ref_landmarks(video_id)
    status = _ref_landmarks_status.get(video_id, "extracting")
    if status == "extracting":
        return jsonify({"error": "still extracting", "status": "extracting"}), 202
    if status not in ("ready", "partial"):
        return jsonify({"error": status}), 500
    data = _ref_landmarks_data.get(video_id, [])
    print(f"[ref_lms] Serving {len(data)} frames to JS (status={status})")
    return jsonify(data)

@app.route('/api/set_fullscreen', methods=['POST'])
def api_set_fullscreen():
    data = request.json or {}
    fs = data.get('fullscreen', False)
    if window:
        try:
            if fs:
                window.maximize()  # Fallback to ensure it fills the screen
                if not window.fullscreen:
                    window.toggle_fullscreen()
            else:
                if window.fullscreen:
                    window.toggle_fullscreen()
        except Exception as e:
            print(f"Fullscreen toggle error: {e}")
    return jsonify({"status": "ok", "fullscreen": fs})

@app.route('/api/quit', methods=['POST', 'GET'])
def api_quit():
    print("Move-It shutting down...")
    cleanup_and_exit()
    return jsonify({"status": "ok"})

# ── MediaPipe local asset server ─────────────────────────────────────────────
# pywebview blocks all external fetch()/importScripts() from renderer.
# We proxy everything through Flask so the renderer only talks to localhost.

MP_ASSETS = {
    'pose_landmarker_full.task': {
        'urls': [
            'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task',
        ],
        'mime': 'application/octet-stream',
    },
    'pose_landmarker_lite.task': {
        'urls': [
            'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task',
        ],
        'mime': 'application/octet-stream',
    },
    'vision_bundle.js': {
        'urls': [
            'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.cjs',
            'https://unpkg.com/@mediapipe/tasks-vision@0.10.14/vision_bundle.cjs',
        ],
        'mime': 'application/javascript',
    },
}

# WASM files are fetched by MediaPipe itself inside the worker via FilesetResolver.
# We proxy the whole /wasm/ path so those requests also stay on localhost.
WASM_CDN_BASES = [
    'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm',
    'https://unpkg.com/@mediapipe/tasks-vision@0.10.14/wasm',
]
WASM_CDN_BASE = WASM_CDN_BASES[0]  # kept for backward-compat reference

# Some CDNs (jsDelivr, unpkg) block Python's default urllib user-agent.
# Using a browser UA fixes this without requiring any extra dependencies.
_BROWSER_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
}

def _download_with_fallback(urls, local_path, retries=2):
    """Try each URL in order with retries, using a browser User-Agent to avoid CDN blocks."""
    for url in urls:
        for attempt in range(retries):
            print(f'[asset] Downloading {url} (attempt {attempt + 1})…')
            try:
                req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                if len(data) < 1024:
                    print(f'[asset] WARNING: {url} returned only {len(data)} bytes — likely an error page, skipping')
                    break  # Try next URL
                with open(local_path, 'wb') as f:
                    f.write(data)
                print(f'[asset] Saved {os.path.basename(local_path)} ({len(data) // 1024} KB) from {url}')
                return True
            except Exception as e:
                print(f'[asset] ERROR on attempt {attempt + 1} for {url}: {e}')
                if os.path.exists(local_path):
                    os.remove(local_path)
                time.sleep(1)
    return False

def _predownload_mp_assets():
    """Pre-download MediaPipe JS bundle and WASM files at startup so they're
    ready before the user opens dance mode. Runs in a background thread."""
    print('[asset] Pre-downloading MediaPipe assets in background…')
    # JS bundle
    _get_asset('vision_bundle.js')
    # Core WASM files needed by FilesetResolver
    for wasm_file in [
        'vision_wasm_internal.js',
        'vision_wasm_internal.wasm',
        'vision_wasm_nosimd_internal.js',
        'vision_wasm_nosimd_internal.wasm',
    ]:
        _get_asset(wasm_file, subdir='wasm')
    print('[asset] MediaPipe asset pre-download complete.')

def _get_asset(filename, subdir=None):
    """Return local path for a cached asset, downloading if missing.
    Tries multiple CDN mirrors with retries before giving up."""
    local_dir = os.path.join(BASE_DIR, 'mp_cache', subdir) if subdir else os.path.join(BASE_DIR, 'mp_cache')
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, filename)

    if not os.path.exists(local_path):
        info = MP_ASSETS.get(filename)
        if info:
            urls = info['urls']
        elif subdir == 'wasm':
            urls = [base + '/' + filename for base in WASM_CDN_BASES]
        else:
            return None

        if not _download_with_fallback(urls, local_path):
            print(f'[asset] FAILED to download {filename} from all sources')
            return None

    return local_dir, filename

@app.route('/pose_landmarker_full.task')
def serve_pose_model():
    result = _get_asset('pose_landmarker_full.task')
    if not result: return 'Download failed', 404
    return send_from_directory(result[0], result[1], mimetype='application/octet-stream')
    
@app.route('/pose_landmarker_lite.task')
def serve_pose_model_lite():
    result = _get_asset('pose_landmarker_lite.task')
    if not result: return 'Download failed', 404
    return send_from_directory(result[0], result[1], mimetype='application/octet-stream')

@app.route('/vision_bundle.js')
def serve_vision_bundle():
    result = _get_asset('vision_bundle.js')
    if not result: return 'Download failed', 404
    return send_from_directory(result[0], result[1], mimetype='application/javascript')

@app.route('/mp_wasm/<path:filename>')
def serve_mp_wasm(filename):
    result = _get_asset(filename, subdir='wasm')
    if not result: return 'Download failed', 404
    # Determine MIME type for WASM files
    mime = 'application/wasm' if filename.endswith('.wasm') else 'application/javascript'
    return send_from_directory(result[0], result[1], mimetype=mime)

# Static Files Catch-All MUST be last
@app.route('/<path:filename>')
def serve_static(filename):
    response = send_from_directory(app.static_folder, filename, max_age=0)
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

def start_server():
    from werkzeug.serving import make_server
    import threading
    
    # Run IPv4 localhost
    try:
        srv_v4 = make_server('127.0.0.1', 5000, app)
        threading.Thread(target=srv_v4.serve_forever, daemon=True).start()
        print("Server listening on 127.0.0.1:5000")
    except Exception as e:
        print(f"IPv4 bind failed: {e}")

    # Run IPv6 localhost (Crucial for macOS WKWebView resolving 'localhost' to ::1)
    try:
        srv_v6 = make_server('::1', 5000, app)
        threading.Thread(target=srv_v6.serve_forever, daemon=True).start()
        print("Server listening on [::1]:5000")
    except Exception as e:
        print(f"IPv6 bind failed: {e}")
    
    # Keep the main thread alive since serve_forever is daemonized
    while True:
        time.sleep(1)

def create_tray_image():
    # Simple icon: green square with white dot
    image = Image.new('RGB', (64, 64), color=(34, 197, 94))
    draw = ImageDraw.Draw(image)
    draw.ellipse((20, 20, 44, 44), fill=(255, 255, 255))
    return image

def on_open_dashboard(icon, item):
    if window:
        window.show()
        window.restore()


def setup_tray():
    if sys.platform == 'darwin':
        return  # pystray conflicts with pywebview on macOS; users use the Dock instead


    global icon
    icon = pystray.Icon("Move-It")
    icon.menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", on_open_dashboard),
        pystray.MenuItem("Toggle Meeting Mode", on_toggle_meeting_mode),
        pystray.MenuItem("Pause Tracking", on_pause_tracking),
        pystray.MenuItem("Quit", on_quit)
    )
    icon.icon = create_tray_image()
    icon.title = "Move-It Tracker"
    # Run detached in a daemon thread so it doesn't block webview
    icon.run_detached()

def on_pause_tracking(icon, item):
    global is_tracking
    is_tracking = False
    print("Tracking paused via system tray.")

def on_quit(icon, item):
    global window
    if window:
        window.destroy()
    icon.stop()
    cleanup_and_exit()

def on_toggle_meeting_mode(icon, item):
    global manual_meeting_mode
    manual_meeting_mode = not manual_meeting_mode
    status = "ON" if manual_meeting_mode else "OFF"
    print(f"Manual Meeting Mode: {status}")




if __name__ == '__main__':

    # Run benchmark in background
    threading.Thread(target=start_server, daemon=True).start()
    threading.Thread(target=tracking_daemon, daemon=True).start()
    # Pre-download MediaPipe JS/WASM assets so they're cached before dance mode opens
    threading.Thread(target=_predownload_mp_assets, daemon=True).start()
    # Pre-extract reference landmarks for macarena2022 so scoring is ready immediately
    threading.Thread(target=_do_extract, args=('macarena2022',), daemon=True).start()

    setup_tray()

    print("Move-It launching...")
    # Show the dashboard on launch. It hides to tray when user clicks "Start Tracking" or the X button.
    # YouTube's iframe API strictly requires 'localhost' in the Referer header to allow VEVO videos.
    window = webview.create_window('Move-It', 'http://localhost:5000', width=600, height=700, resizable=True, maximized=True)
    
    def on_closing():
        if window:
            window.minimize()
            show_tray_notification_once()
        return False
        
    window.events.closing += on_closing
    if sys.platform == 'win32':
        webview.start(private_mode=False, gui='edgechromium')
    else:
        # On macOS, WKWebView blocks third-party cookies and identifies itself as Safari.
        # YouTube checks both of these and refuses to play embedded videos unless:
        #   1) The user-agent looks like Chrome (so YouTube enables its full player)
        #   2) Third-party cookies are allowed (so YouTube's security tokens can be set)
        CHROME_UA = (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
        # Settings must be set on the module-level dict, not passed to start()
        webview.settings['ALLOW_DOWNLOADS'] = False
        webview.settings['OPEN_DEVTOOLS_IN_DEBUG'] = True
        webview.start(
            private_mode=False,
            user_agent=CHROME_UA,
        )