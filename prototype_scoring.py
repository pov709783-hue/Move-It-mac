import cv2
import mediapipe as mp
import json
import numpy as np
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

# Load reference data
DATA_PATH = r"C:\Users\Ali\Desktop\3D_Models\Move-It\pose_data.json"
MODEL_PATH = r"C:\Users\Ali\Desktop\3D_Models\Move-It\pose_landmarker_heavy.task"

with open(DATA_PATH, 'r') as f:
    reference_data = json.load(f)

# Initialize MediaPipe Pose for Live Stream
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

# Global variables to hold the latest result
latest_pose = None

def process_result(result, image, timestamp):
    global latest_pose
    if result.pose_landmarks:
        latest_pose = result.pose_landmarks[0]
    else:
        latest_pose = None

# MediaPipe Pose Connections (Pairs of points to draw lines between)
POSE_CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 7), (0, 4), (4, 5), (5, 6), (6, 8), (9, 10), (11, 12), (11, 13), (13, 15), (15, 17), (15, 19), (15, 21), (17, 19), (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20), (11, 23), (12, 24), (23, 24), (23, 25), (24, 26), (25, 27), (26, 28), (27, 29), (28, 30), (29, 31), (30, 32), (27, 31), (28, 32)]

def normalize_pose(landmarks):
    """Centers the pose at the hips and scales it by torso size for fair comparison."""
    if not landmarks or len(landmarks) < 33:
        return None
        
    # Get Left (23) and Right (24) Hips
    left_hip = np.array([landmarks[23].x, landmarks[23].y])
    right_hip = np.array([landmarks[24].x, landmarks[24].y])
    center_hip = (left_hip + right_hip) / 2.0
    
    # Get Left (11) and Right (12) Shoulders
    left_shoulder = np.array([landmarks[11].x, landmarks[11].y])
    right_shoulder = np.array([landmarks[12].x, landmarks[12].y])
    center_shoulder = (left_shoulder + right_shoulder) / 2.0
    
    # Calculate torso size for scaling
    torso_size = np.linalg.norm(center_shoulder - center_hip)
    if torso_size < 0.01: # Prevent division by zero
        torso_size = 0.01
        
    normalized = []
    for lm in landmarks:
        norm_x = (lm.x - center_hip[0]) / torso_size
        norm_y = (lm.y - center_hip[1]) / torso_size
        normalized.append((norm_x, norm_y))
        
    return normalized

def calculate_score(live_pose, ref_pose_data):
    """Calculates a score from 0-100 based on similarity with tolerance."""
    if not live_pose or not ref_pose_data:
        return 0
        
    # Create fake objects for the reference data so we can use the same normalize function
    class DummyLandmark:
        def __init__(self, x, y):
            self.x = x
            self.y = y
            
    ref_pose = [DummyLandmark(lm['x'], lm['y']) for lm in ref_pose_data]
    
    norm_live = normalize_pose(live_pose)
    norm_ref = normalize_pose(ref_pose)
    
    if not norm_live or not norm_ref:
        return 0
        
    # Compare key joints (Wrists, Elbows, Shoulders, Knees, Ankles)
    key_joints = [11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]
    total_error = 0
    
    for i in key_joints:
        dist = np.linalg.norm(np.array(norm_live[i]) - np.array(norm_ref[i]))
        total_error += dist
        
    avg_error = total_error / len(key_joints)
    
    # The Tolerance Logic
    # If error is 0, score is 100. If error is high (e.g. > 1.5), score is 0.
    score = max(0, min(100, int(100 - (avg_error * 60))))
    return score

def get_color_for_score(score):
    if score >= 67:
        return (0, 255, 0) # Green (BGR)
    elif score >= 34:
        return (0, 165, 255) # Orange (BGR)
    else:
        return (0, 0, 255) # Red (BGR)

# Open Webcam
cap = cv2.VideoCapture(0)

landmarker = vision.PoseLandmarker.create_from_options(options)

ref_frame_index = 0
total_ref_frames = len(reference_data['frames'])

print("Starting Webcam. Press 'q' to quit.")

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break
        
    # Mirror frame for natural feel
    frame = cv2.flip(frame, 1)
    
    # Process with MediaPipe
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
    timestamp_ms = int(cap.get(cv2.CAP_PROP_POS_MSEC))
    if timestamp_ms <= 0:
        timestamp_ms = cv2.getTickCount() / cv2.getTickFrequency() * 1000
    landmarker.detect_async(mp_image, int(timestamp_ms))
    
    # Get the current reference frame data to compare against
    current_ref_data = reference_data['frames'][ref_frame_index]['landmarks']
    
    score = 0
    color = (0, 0, 255) # Default Red
    
    if latest_pose:
        score = calculate_score(latest_pose, current_ref_data)
        color = get_color_for_score(score)
        
        h, w, c = frame.shape
        
        # Draw Lines (Bones)
        for connection in POSE_CONNECTIONS:
            start_idx = connection[0]
            end_idx = connection[1]
            
            start_lm = latest_pose[start_idx]
            end_lm = latest_pose[end_idx]
            
            if getattr(start_lm, 'visibility', 1.0) > 0.5 and getattr(end_lm, 'visibility', 1.0) > 0.5:
                start_point = (int(start_lm.x * w), int(start_lm.y * h))
                end_point = (int(end_lm.x * w), int(end_lm.y * h))
                cv2.line(frame, start_point, end_point, color, 4)
        
        # Draw Dots (Joints)
        for lm in latest_pose:
            if getattr(lm, 'visibility', 1.0) > 0.5:
                cx, cy = int(lm.x * w), int(lm.y * h)
                cv2.circle(frame, (cx, cy), 6, (255, 255, 255), -1) # White dots
                cv2.circle(frame, (cx, cy), 8, color, 2) # Colored outline
                
    # Display Score
    cv2.putText(frame, f"SCORE: {score}", (30, 70), cv2.FONT_HERSHEY_SIMPLEX, 2, color, 5)
    
    # Advance the reference frame (looping for now)
    ref_frame_index = (ref_frame_index + 1) % total_ref_frames
    
    # Show the webcam feed
    cv2.imshow('Move-It Prototype', frame)
    
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
landmarker.close()
