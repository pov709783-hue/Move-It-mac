import os
import json
import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

# Setup Pose Landmarker for static images
base_options = python.BaseOptions(model_asset_path='pose_landmarker_heavy.task')
options = vision.PoseLandmarkerOptions(
    base_options=base_options,
    output_segmentation_masks=False,
    num_poses=1,
    running_mode=vision.RunningMode.IMAGE)
detector = vision.PoseLandmarker.create_from_options(options)

IMAGES_DIR = r"C:\Users\Ali\Desktop\3D_Models\Move-It\Images"
OUTPUT_FILE = r"C:\Users\Ali\Desktop\3D_Models\Move-It\stretches_data.json"

stretches_data = {}

def extract_landmarks(image_path):
    image = cv2.imread(image_path)
    if image is None:
        print(f"Failed to load {image_path}")
        return None
        
    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
    
    detection_result = detector.detect(mp_image)
    
    if not detection_result.pose_landmarks:
        print(f"No pose detected in {image_path}")
        return None
        
    landmarks = []
    # detection_result.pose_landmarks is a list of poses, we take the first [0]
    for lm in detection_result.pose_landmarks[0]:
        landmarks.append({
            "x": lm.x,
            "y": lm.y,
            "z": lm.z,
            "visibility": lm.visibility
        })
    return landmarks

def main():
    import glob
    search_pattern = os.path.join(IMAGES_DIR, '*.png')
    image_files = glob.glob(search_pattern)
    
    for path in image_files:
        filename = os.path.basename(path)
        print(f"Processing {filename}...")
        landmarks = extract_landmarks(path)
        if landmarks:
            stretches_data[filename] = landmarks
            print(f"Success for {filename}!")
            
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(stretches_data, f, indent=4)
    print(f"Saved {len(stretches_data)} poses to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
