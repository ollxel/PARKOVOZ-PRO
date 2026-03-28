# PARKOVOZ-PRO - Parking Monitoring System

[English README](README.md) | [Russian README](README_RU.md)

![](screenshot.jpg)
*demonstration of the system*

**PARKOVOZ-PRO** is a comprehensive solution for automatic parking space monitoring using neural networks. The system analyzes a video stream in real time, detects free/occupied parking spots, and provides a convenient web interface for data visualization.

## Architecture

PARKOVOZ-PRO consists of three main components:

- **Backend (Python + OpenCV + YOLO)** — processes video streams and detects vehicles
- **Frontend (JavaScript + Three.js)** — visualizes parking occupancy in real time
- **Communication Layer (WebSocket)** — transfers detection results from backend to frontend

The backend receives RTSP/HTTP camera streams, runs YOLO inference, determines parking occupancy, and sends updates to the frontend interface.

## Installation and Launch

### Requirements
- Python 3.7+
- NVIDIA GPU (recommended)
- Access to an RTSP/HTTP camera

### Step-by-step installation

1. Clone the repository:
```bash
git clone https://github.com/ollxel/PARKOVOZ-PRO
cd PARKOVOZ-PRO
```

2. Install dependencies:
```bash
pip3 install -r requirements.txt
```

3. Download the YOLOv12x model:
```bash
wget https://example.com/yolov12x.pt -O RealParking/backend/yolov12x.pt
cp RealParking/backend/yolov12x.pt ToyParking/backend/
```

### Running the system

Backend:
```bash
python3 main.py --url rtsp://your_camera_ip --device auto
```

Frontend:
```bash
python3 -m http.server  # port - 8000
```

Note: port `9000` is a WebSocket endpoint (`ws://localhost:9000`), not a regular HTTP page.

HTML camera pages are supported (Yawcam, webcam7/webcamXP): you can pass a page URL, and the script will auto-detect the direct `jpg` endpoint.

Examples:
```bash
# Yawcam page URL (auto-detects out.jpg)
python3 main.py --url http://206.169.218.170:8081/ --device auto --size 320

# webcam7 page URL, choose Source 4
python3 main.py --url http://camera-host:8080/ --webcam-source 4 --device auto --size 320

# YouTube Live URL (requires yt-dlp and ffmpeg)
python3 main.py --url "https://www.youtube.com/watch?v=LIVE_ID" --device auto --size 320
```

### Launch Parameters

| Parameter | Description | Default |
|----------|-------------|---------|
| `--url` | Video stream URL | Required |
| `--model` | Path to the model | `yolov12x.pt` |
| `--device` | Device (`auto` / `cpu` / `gpu` / `mps`) | `auto` |
| `--ws-port` | WebSocket port | `9000` |
| `--webcam-source` | Source id for webcam7/webcamXP pages | auto |
| `--youtube-format` | yt-dlp format selector (YouTube Live) | `best[protocol*=m3u8]/best` |

### Hotkeys

| Key | Action |
|-----|--------|
| `S` | Save snapshot |
| `R` | Start/stop recording |
| `M` | Toggle mode |
| `P` | Annotation mode |
| `Q` | Exit system |

## License
The project is distributed under the **GPLv3** license.  
Full text available in the file:  
https://github.com/ollxel/PARKOVOZ-PRO/blob/main/LICENSE
