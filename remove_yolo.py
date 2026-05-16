import re

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove YOLO26 Pose global vars and get_yolo_model / run_yolo_benchmark
content = re.sub(r'# YOLO26 Pose \(lazy-loaded to avoid startup cost if not used\)\s*\nyolo_model = None\s*\nyolo_benchmark_tier = .*?yolo_benchmark_done = True\n', '', content, flags=re.DOTALL)

# 2. Remove YOLO Test State
content = re.sub(r'# YOLO Test State\s*\nyolo_test_active = False\s*\nyolo_ref_data = None\s*\nyolo_ref_frame_index = 0\s*\nyolo_total_ref_frames = 0\s*\nyolo_current_score = 0\s*\nyolo_latest_keypoints = None\n', '', content, flags=re.DOTALL)

# 3. Remove yolo_test_active from tracking daemon
content = content.replace('if not mouse_active and not (is_dancing or is_stretching or yolo_test_active):', 'if not mouse_active and not (is_dancing or is_stretching):')

# 4. Remove YOLO Test Mode Block
content = re.sub(r'# =============================================================================\s*\n# YOLO26 Test Mode\s*\n# =============================================================================.*?@app\.route\(''/api/log'', methods=\[''POST''\]\)', '@app.route(''/api/log'', methods=[''POST''])', content, flags=re.DOTALL)

# 5. Remove serve_yolo_video_dynamic
content = re.sub(r'@app\.route\(''/yolo_video/<video_id>\.<ext>''\)\s*\ndef serve_yolo_video_dynamic\(video_id, ext\):.*?return "Video not found", 404\n', '', content, flags=re.DOTALL)

# 6. Remove run_yolo_benchmark thread
content = content.replace('    threading.Thread(target=run_yolo_benchmark, daemon=True).start()\n', '')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('YOLO removed.')
