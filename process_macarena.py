import cv2
import os
import json
import time
from ultralytics import YOLO

# Project Base Directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Input Settings
INPUT_VIDEO = os.path.join(BASE_DIR, "The Macarena Dance 2022.mp4")
VIDEO_ID = "macarena2022"
OUTPUT_JSON = os.path.join(BASE_DIR, f"yolo_pose_data_{VIDEO_ID}.json")

def process_landmarks_only():
    """Extracts landmarks from HD video and saves to JSON. No video writing."""
    print("Loading YOLO Model...")
    model = YOLO("yolo26n-pose.pt")

    cap = cv2.VideoCapture(INPUT_VIDEO)
    if not cap.isOpened():
        print(f"Error: Could not open video {INPUT_VIDEO}")
        return

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0: fps = 30
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print(f"Processing HD Video Landmarks: {width}x{height} @ {fps} FPS")
    print(f"Total Frames to process: {total_frames}")

    all_pose_data = []
    frame_count = 0
    start_time = time.time()

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        # HD Inference
        results = model(frame, verbose=False, imgsz=640)
        
        if len(results) > 0 and results[0].keypoints is not None and len(results[0].keypoints.data) > 0:
            kps = results[0].keypoints.data[0].cpu().numpy()
            
            frame_data = {"frame_index": frame_count, "landmarks": []}
            for idx, kp in enumerate(kps):
                # Store normalized keypoints (0 to 1 range)
                frame_data["landmarks"].append({
                    "id": idx,
                    "x": round(float(kp[0] / width), 4),
                    "y": round(float(kp[1] / height), 4),
                    "visibility": round(float(kp[2]), 4)
                })
            all_pose_data.append(frame_data)

        frame_count += 1
        if frame_count % 100 == 0:
            elapsed = time.time() - start_time
            fps_proc = frame_count / elapsed if elapsed > 0 else 0
            print(f"  [{frame_count}/{total_frames}] {fps_proc:.1f} FPS processing...")

    cap.release()

    # Save to JSON
    output_data = {
        "video_id": VIDEO_ID,
        "fps": fps,
        "width": width,
        "height": height,
        "total_frames": total_frames,
        "frames": all_pose_data
    }

    with open(OUTPUT_JSON, 'w') as f:
        json.dump(output_data, f)

    print(f"\nSUCCESS! Pose data saved to: {OUTPUT_JSON}")
    print(f"Total frames processed: {len(all_pose_data)}")

if __name__ == "__main__":
    process_landmarks_only()
