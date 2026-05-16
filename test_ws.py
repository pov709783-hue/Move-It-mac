import json
import base64
import time
import cv2
import numpy as np
from simple_websocket import Client, ConnectionClosed

try:
    print("Connecting to ws://127.0.0.1:5000/ws/keypoints...")
    ws = Client.connect('ws://127.0.0.1:5000/ws/keypoints')
    
    # Wait for config
    msg = ws.receive()
    print("Config received:", msg)
    
    # Send a dummy frame
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(frame, "Test", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    _, buffer = cv2.imencode('.jpg', frame)
    b64 = base64.b64encode(buffer).decode('utf-8')
    
    print("Sending frame...")
    ws.send(json.dumps({
        "type": "frame",
        "image": "data:image/jpeg;base64," + b64
    }))
    
    # Wait for response
    resp = ws.receive(timeout=5)
    print("Response:", resp)
    
except ConnectionClosed as e:
    print("Connection closed:", e)
except Exception as e:
    print("Error:", e)
