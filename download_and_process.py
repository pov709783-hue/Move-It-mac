import os
import cv2
import json
import urllib.request
import yt_dlp
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

# Directory setup
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "pose_landmarker_heavy.task")
REGISTRY_PATH = os.path.join(BASE_DIR, "songs_registry.json")

# Ensure model exists
if not os.path.exists(MODEL_PATH):
    print("Downloading Pose Landmarker model...")
    url = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/1/pose_landmarker_heavy.task"
    urllib.request.urlretrieve(url, MODEL_PATH)
    print("Model downloaded.")

# Initialize MediaPipe Pose Landmarker
# We set num_poses=3 to detect multiple people, then we'll filter for the "main" one
base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
options = vision.PoseLandmarkerOptions(
    base_options=base_options,
    running_mode=vision.RunningMode.VIDEO,
    num_poses=3,  
    min_pose_detection_confidence=0.5,
    min_pose_presence_confidence=0.5,
    min_tracking_confidence=0.5)

def get_pose_area(landmarks):
    """Calculate the bounding box area of a pose to determine who is 'closest' (largest)."""
    xs = [lm.x for lm in landmarks if getattr(lm, 'visibility', 1.0) > 0.5]
    ys = [lm.y for lm in landmarks if getattr(lm, 'visibility', 1.0) > 0.5]
    if not xs or not ys:
        return 0
    return (max(xs) - min(xs)) * (max(ys) - min(ys))

def process_video(video_id, title, video_path):
    output_data_path = os.path.join(BASE_DIR, f"pose_data_{video_id}.json")
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open {video_path}")
        return False
        
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0: fps = 30 # fallback
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    
    safe_title = title.encode('ascii', 'ignore').decode('ascii')
    print(f"Processing '{safe_title}' ({video_id}) | {width}x{height} @ {fps:.1f}fps | {total_frames} frames")
    
    all_pose_data = []
    frame_count = 0
    
    with vision.PoseLandmarker.create_from_options(options) as landmarker:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
                
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
            
            timestamp_ms = int(cap.get(cv2.CAP_PROP_POS_MSEC))
            if timestamp_ms < 0:
                timestamp_ms = int((frame_count / fps) * 1000)
                
            detection_result = landmarker.detect_for_video(mp_image, timestamp_ms)
            
            frame_data = {"frame": frame_count, "timestamp_ms": timestamp_ms, "landmarks": []}
            
            if detection_result.pose_landmarks:
                # Find the most prominent pose (largest bounding box)
                best_pose = None
                max_area = -1
                for pose in detection_result.pose_landmarks:
                    area = get_pose_area(pose)
                    if area > max_area:
                        max_area = area
                        best_pose = pose
                
                if best_pose:
                    for idx, lm in enumerate(best_pose):
                        frame_data["landmarks"].append({
                            "id": idx,
                            "x": round(lm.x, 4),
                            "y": round(lm.y, 4),
                            "z": round(lm.z, 4),
                            "visibility": round(getattr(lm, 'visibility', 1.0), 4),
                            "presence": round(getattr(lm, 'presence', 1.0), 4)
                        })
            
            all_pose_data.append(frame_data)
            frame_count += 1
            
            if frame_count % 500 == 0:
                print(f"  Processed {frame_count}/{total_frames} frames...")
                
    cap.release()
    
    with open(output_data_path, 'w') as f:
        json.dump({"fps": fps, "width": width, "height": height, "frames": all_pose_data}, f)
        
    print(f"Saved {output_data_path}")
    return True

def download_and_process(urls):
    registry = []
    
    # Load existing registry if available
    if os.path.exists(REGISTRY_PATH):
        try:
            with open(REGISTRY_PATH, 'r') as f:
                registry = json.load(f)
        except Exception:
            pass
            
    existing_ids = {song['id'] for song in registry}
    
    for url in urls:
        print(f"\n--- Fetching info for {url} ---")
        ydl_opts = {
            'format': 'worstvideo[ext=mp4]/mp4', # Download low quality for faster processing (we just need skeletons)
            'outtmpl': os.path.join(BASE_DIR, 'temp_video_%(id)s.%(ext)s'),
            'quiet': False
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=True)
                video_id = info['id']
                title = info.get('title', 'Unknown Dance')
                # Try to get best thumbnail
                thumbnail = info.get('thumbnail')
                
                if video_id in existing_ids:
                    print(f"Video {video_id} already exists in registry. Skipping.")
                    video_path = os.path.join(BASE_DIR, f"temp_video_{video_id}.mp4")
                    if os.path.exists(video_path): os.remove(video_path)
                    continue
                
                video_path = os.path.join(BASE_DIR, f"temp_video_{video_id}.mp4")
                
                success = process_video(video_id, title, video_path)
                
                if success:
                    registry.append({
                        "id": video_id,
                        "title": title,
                        "thumbnail": thumbnail,
                        "url": url
                    })
                    # Save registry
                    with open(REGISTRY_PATH, 'w') as f:
                        json.dump(registry, f, indent=4)
                        
                # Cleanup temp video
                if os.path.exists(video_path):
                    os.remove(video_path)
                    print(f"Removed temporary video file.")
                    
            except Exception as e:
                print(f"Error processing {url}: {e}")

if __name__ == "__main__":
    # Also add the original video to the registry if it doesn't exist
    new_urls = [
        "https://www.youtube.com/watch?v=dqdctxjD60g",
        "https://www.youtube.com/watch?v=-LS89QO3U28",
        "https://www.youtube.com/watch?v=NJh5idlanrc",
        "https://www.youtube.com/watch?v=iQ1DCl5mgHY",
        "https://www.youtube.com/watch?v=Hkyu76pigxM"
    ]
    download_and_process(new_urls)
    print("\nAll videos processed successfully!")
