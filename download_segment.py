import os
import yt_dlp
import sys
from download_and_process import process_video
import json

def download_and_process_segment(url, start_time, end_time):
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    REGISTRY_PATH = os.path.join(BASE_DIR, "songs_registry.json")
    
    print(f"\n--- Fetching info for {url} ---")
    ydl_opts = {
        'format': 'worstvideo[ext=mp4]/mp4',
        'outtmpl': os.path.join(BASE_DIR, 'temp_video_%(id)s.%(ext)s'),
        'download_ranges': yt_dlp.utils.download_range_func(None, [(start_time, end_time)]),
        'force_keyframes_at_cuts': True,
        'quiet': False
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            video_id = info['id']
            title = info.get('title', 'Unknown Dance')
            thumbnail = info.get('thumbnail')
            
            video_path = os.path.join(BASE_DIR, f"temp_video_{video_id}.mp4")
            
            print(f"Processing segment for {video_id}...")
            success = process_video(video_id, title, video_path)
            
            if success:
                registry = []
                if os.path.exists(REGISTRY_PATH):
                    with open(REGISTRY_PATH, 'r') as f:
                        registry = json.load(f)
                        
                # Only add if not exists
                if not any(s['id'] == video_id for s in registry):
                    registry.append({
                        "id": video_id,
                        "title": title,
                        "thumbnail": thumbnail,
                        "youtubeId": video_id,
                        "startSeconds": start_time,
                        "endSeconds": end_time
                    })
                    with open(REGISTRY_PATH, 'w') as f:
                        json.dump(registry, f, indent=4)
                    print("Added to songs_registry.json")
                else:
                    # Update start/end times if it exists
                    for s in registry:
                        if s['id'] == video_id:
                            s['startSeconds'] = start_time
                            s['endSeconds'] = end_time
                    with open(REGISTRY_PATH, 'w') as f:
                        json.dump(registry, f, indent=4)
                    print("Updated songs_registry.json")
                    
            if os.path.exists(video_path):
                os.remove(video_path)
                
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    download_and_process_segment("https://www.youtube.com/watch?v=PLEQDXWcgYQ", 26, 230)
