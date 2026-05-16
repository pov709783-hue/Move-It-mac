import os
import cv2
import json
import yt_dlp
from ultralytics import YOLO

# Directory setup
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_NAME = "yolo26n-pose.pt" # Or whichever model ultralytics resolves to if YOLO26 is an alias
# If yolo26n-pose.pt fails to download, we'll fallback to yolo11n-pose.pt which is guaranteed

print("Loading YOLO model...")
try:
    model = YOLO(MODEL_NAME)
except Exception as e:
    print(f"Fallback to YOLO11 due to: {e}")
    model = YOLO("yolo11n-pose.pt")

def process_yolo_video(video_id, title, video_path):
    output_data_path = os.path.join(BASE_DIR, f"yolo_pose_data_{video_id}.json")
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open {video_path}")
        return False
        
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0: fps = 30 # fallback
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    
    print(f"Processing '{title}' ({video_id}) with YOLO | {width}x{height} @ {fps:.1f}fps | {total_frames} frames")
    
    all_pose_data = []
    frame_count = 0
    
    # Run YOLO with stream=True for efficiency
    results = model(video_path, stream=True, verbose=False)
    
    for r in results:
        timestamp_ms = int((frame_count / fps) * 1000)
        frame_data = {"frame": frame_count, "timestamp_ms": timestamp_ms, "landmarks": []}
        
        if r.keypoints is not None and len(r.keypoints.data) > 0:
            # We take the first person detected (assuming they are the main dancer)
            # YOLO returns keypoints shape [Num_people, 17, 3] where 3 is x, y, confidence
            keypoints = r.keypoints.data[0].cpu().numpy()
            
            # Convert absolute coordinates to normalized (0 to 1) for compatibility with our existing system
            for idx, kp in enumerate(keypoints):
                x_norm = float(kp[0] / width)
                y_norm = float(kp[1] / height)
                conf = float(kp[2])
                
                frame_data["landmarks"].append({
                    "id": idx,
                    "x": round(x_norm, 4),
                    "y": round(y_norm, 4),
                    "visibility": round(conf, 4),
                    "presence": round(conf, 4)
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

def download_and_process_yolo(url):
    print(f"\n--- Fetching info for {url} ---")
    ydl_opts = {
        'format': 'worstvideo[ext=mp4]/mp4',
        'outtmpl': os.path.join(BASE_DIR, 'temp_yolo_video_%(id)s.%(ext)s'),
        'quiet': False
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            video_id = info['id']
            title = info.get('title', 'Unknown Dance')
            
            video_path = os.path.join(BASE_DIR, f"temp_yolo_video_{video_id}.mp4")
            
            process_yolo_video(video_id, title, video_path)
                    
            # Cleanup temp video
            if os.path.exists(video_path):
                os.remove(video_path)
                print(f"Removed temporary video file.")
                
        except Exception as e:
            print(f"Error processing {url}: {e}")

import sys

if __name__ == "__main__":
    if len(sys.argv) > 1:
        test_url = sys.argv[1]
    else:
        test_url = "https://www.youtube.com/watch?v=8ytQpZgFu-8"
        
    if not test_url.startswith('http'):
        test_url = f"https://www.youtube.com/watch?v={test_url}"
        
    download_and_process_yolo(test_url)
    print("\nYOLO video processed successfully!")
