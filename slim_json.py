import json, os

with open('pose_data_PLEQDXWcgYQ.json', 'r') as f:
    data = json.load(f)

frames = data['frames']
print(f"Total frames: {len(frames)}")
print(f"First frame ts: {frames[0]['timestamp_ms']}ms")
print(f"Last frame ts: {frames[-1]['timestamp_ms']}ms")

# Filter to 26s-92s (buffer around 30-90)
start_ms = 26000
end_ms = 92000
filtered = [fr for fr in frames if start_ms <= fr['timestamp_ms'] <= end_ms]
print(f"Filtered frames (26-92s): {len(filtered)}")

# Slim down: keep t (seconds) and lms with x, y, v
slim = []
for fr in filtered:
    lms = []
    for lm in fr['landmarks']:
        lms.append({
            'x': round(lm['x'], 4),
            'y': round(lm['y'], 4),
            'v': round(lm.get('visibility', 1), 2)
        })
    slim.append({'t': fr['timestamp_ms'] / 1000, 'lms': lms})

print(f"Slim frames: {len(slim)}")

out_path = os.path.join('landing', 'public', 'pose_data_PLEQDXWcgYQ.json')
with open(out_path, 'w') as f:
    json.dump(slim, f)

size = os.path.getsize(out_path)
print(f"Output size: {size} bytes ({size/1024:.1f} KB)")
print(f"First slim frame keys: {list(slim[0].keys())}")
print(f"First slim lms count: {len(slim[0]['lms'])}")
