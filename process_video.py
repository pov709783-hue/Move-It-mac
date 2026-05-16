import os
import cv2
import json
import yt_dlp
from ultralytics import YOLO

# =============================================================================
# CONFIGURATION
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRY_PATH = os.path.join(BASE_DIR, "songs_registry.json")

# YOLO Skeleton connections (COCO 17 keypoints)
YOLO_SKELETON = [
    (0, 1), (0, 2), (1, 3), (2, 4),   # Head
    (5, 6),                             # Shoulders
    (5, 7), (7, 9),                     # Left arm
    (6, 8), (8, 10),                    # Right arm
    (5, 11), (6, 12),                   # Torso sides
    (11, 12),                           # Hips
    (11, 13), (13, 15),                 # Left leg
    (12, 14), (14, 16),                 # Right leg
]

KEYPOINT_COLOR = (0, 255, 0)       # Green dots
SKELETON_COLOR = (0, 255, 0)       # Green lines
LOW_CONF_COLOR = (0, 0, 255)       # Red for low confidence
CONF_THRESHOLD = 0.3               # Minimum confidence to draw keypoint


# =============================================================================
# LOAD YOLO MODEL (once, reused for all videos)
# =============================================================================
def get_model():
    try:
        model = YOLO("yolo26n-pose.pt")
        print("Loaded: yolo26n-pose.pt")
    except Exception as e:
        print(f"yolo26n-pose.pt not found ({e}), falling back to yolo11n-pose.pt")
        model = YOLO("yolo11n-pose.pt")
    return model


# =============================================================================
# DRAW SKELETON ON FRAME
# =============================================================================
def draw_skeleton_on_frame(frame, keypoints, width, height):
    """Draw YOLO skeleton overlay on a frame."""
    # Draw connections
    for (i, j) in YOLO_SKELETON:
        kp1 = keypoints[i]
        kp2 = keypoints[j]
        conf1 = kp1[2]
        conf2 = kp2[2]
        if conf1 > CONF_THRESHOLD and conf2 > CONF_THRESHOLD:
            x1 = int(kp1[0] * width)
            y1 = int(kp1[1] * height)
            x2 = int(kp2[0] * width)
            y2 = int(kp2[1] * height)
            cv2.line(frame, (x1, y1), (x2, y2), SKELETON_COLOR, 3)

    # Draw keypoints
    for kp in keypoints:
        conf = kp[2]
        if conf > CONF_THRESHOLD:
            x = int(kp[0] * width)
            y = int(kp[1] * height)
            color = KEYPOINT_COLOR if conf > 0.5 else LOW_CONF_COLOR
            cv2.circle(frame, (x, y), 6, (255, 255, 255), -1)  # White fill
            cv2.circle(frame, (x, y), 6, color, 2)             # Colored border

    return frame


# =============================================================================
# PROCESS A SINGLE VIDEO
# =============================================================================
def process_video(model, video_id, title, video_path):
    """
    Process a video file with YOLO26 pose detection.
    Outputs:
      - pose_data_{video_id}.json  → keypoints for scoring
      - skeleton_{video_id}.mp4   → HD video with skeleton overlay (browser compatible H.264)
    """
    output_json = os.path.join(BASE_DIR, f"pose_data_{video_id}.json")
    output_video = os.path.join(BASE_DIR, f"skeleton_{video_id}.mp4")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open {video_path}")
        return False

    width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps    = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print(f"\nProcessing: '{title}' ({video_id})")
    print(f"Resolution: {width}x{height} @ {fps:.1f}fps | Total frames: {total}")

    # H.264 codec — browser compatible, works in pywebview/Edge
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_video, fourcc, fps, (width, height))

    all_pose_data = []
    frame_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Run YOLO inference at 640px (best accuracy for reference video)
        results = model(frame, verbose=False, imgsz=640)

        frame_data = {
            "frame_index": frame_count,
            "timestamp_ms": int((frame_count / fps) * 1000),
            "landmarks": []
        }

        if (
            results and
            results[0].keypoints is not None and
            len(results[0].keypoints.data) > 0
        ):
            kps = results[0].keypoints.data[0].cpu().numpy()

            # Normalize keypoints to 0-1 range
            normalized = []
            for kp in kps:
                normalized.append({
                    "x": round(float(kp[0] / width), 4),
                    "y": round(float(kp[1] / height), 4),
                    "visibility": round(float(kp[2]), 4)
                })
            frame_data["landmarks"] = normalized

            # Draw skeleton on frame for the output video
            kps_for_draw = [
                [float(kp[0] / width), float(kp[1] / height), float(kp[2])]
                for kp in kps
            ]
            frame = draw_skeleton_on_frame(frame, kps_for_draw, width, height)

        all_pose_data.append(frame_data)
        out.write(frame)
        frame_count += 1

        if frame_count % 100 == 0:
            pct = (frame_count / total) * 100 if total > 0 else 0
            print(f"  [{pct:.1f}%] Processed {frame_count}/{total} frames...")

    cap.release()
    out.release()

    # Save JSON
    with open(output_json, 'w') as f:
        json.dump({
            "fps": fps,
            "width": width,
            "height": height,
            "total_frames": frame_count,
            "frames": all_pose_data
        }, f)

    print(f"✅ Done: {video_id}")
    print(f"   JSON  → {output_json}")
    print(f"   Video → {output_video}")
    return True


# =============================================================================
# DOWNLOAD FROM YOUTUBE AND PROCESS
# =============================================================================
def download_and_process(urls):
    model = get_model()

    # Load existing registry
    registry = []
    if os.path.exists(REGISTRY_PATH):
        try:
            with open(REGISTRY_PATH, 'r') as f:
                registry = json.load(f)
        except Exception:
            pass

    existing_ids = {song['id'] for song in registry}

    for url in urls:
        print(f"\n--- Fetching: {url} ---")

        ydl_opts = {
            # Download best quality mp4 for HD skeleton output
            'format': 'bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(BASE_DIR, 'temp_video_%(id)s.%(ext)s'),
            'quiet': False,
            'merge_output_format': 'mp4'
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                video_id = info['id']
                title = info.get('title', 'Unknown Dance')
                thumbnail = info.get('thumbnail', '')

                if video_id in existing_ids:
                    print(f"Already processed: {video_id}. Skipping.")
                    video_path = os.path.join(BASE_DIR, f"temp_video_{video_id}.mp4")
                    if os.path.exists(video_path):
                        os.remove(video_path)
                    continue

                video_path = os.path.join(BASE_DIR, f"temp_video_{video_id}.mp4")

                success = process_video(model, video_id, title, video_path)

                if success:
                    registry.append({
                        "id": video_id,
                        "title": title,
                        "thumbnail": thumbnail,
                        "url": url
                    })
                    with open(REGISTRY_PATH, 'w') as f:
                        json.dump(registry, f, indent=4)

                # Cleanup temp download
                if os.path.exists(video_path):
                    os.remove(video_path)
                    print(f"Removed temp file: temp_video_{video_id}.mp4")

        except Exception as e:
            print(f"Error processing {url}: {e}")


# =============================================================================
# PROCESS A LOCAL VIDEO FILE (no download needed)
# =============================================================================
def process_local(video_path, video_id=None, title=None):
    """
    Use this if you already have the video file locally.
    Example: process_local("The Macarena Dance 2022.mp4", "macarena2022", "Macarena 2022")
    """
    model = get_model()

    if video_id is None:
        video_id = os.path.splitext(os.path.basename(video_path))[0].replace(' ', '_').lower()
    if title is None:
        title = os.path.splitext(os.path.basename(video_path))[0]

    process_video(model, video_id, title, video_path)


# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":

    # --- OPTION A: Process a local video you already have ---
    process_local(
        video_path="The Macarena Dance 2022.mp4",
        video_id="macarena2022",
        title="The Macarena Dance 2022"
    )

    # --- OPTION B: Download from YouTube and process ---
    # Uncomment below and add your URLs
    # download_and_process([
    #     "https://www.youtube.com/watch?v=H_rRYdNCu_k",
    #     "https://www.youtube.com/watch?v=4JSv116uqU4",
    # ])
