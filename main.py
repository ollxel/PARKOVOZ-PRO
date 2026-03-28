import cv2
import numpy as np
import os
import time
import argparse
import requests
from datetime import datetime
import threading
import queue
import websockets
import asyncio
import json
import logging
import torch
import re
import html as html_lib
import random
import subprocess
import shutil
from urllib.parse import urljoin, urlparse, parse_qs, parse_qsl, urlencode, urlunparse
from ultralytics import YOLO

parser = argparse.ArgumentParser(description='Parking Lot Monitoring System')
parser.add_argument('--url', type=str, required=True, help='Camera stream URL')
parser.add_argument('--output', type=str, default='output', help='Output directory for results')
parser.add_argument('--confidence', type=float, default=0.5, help='Confidence threshold')
parser.add_argument('--nms', type=float, default=0.4, help='NMS threshold')
parser.add_argument('--size', type=int, default=640, help='Input size for network')
parser.add_argument('--fps', type=int, default=15, help='Target FPS for video processing')
parser.add_argument(
    '--device',
    type=str,
    default='auto',
    choices=['auto', 'cpu', 'gpu', 'mps'],
    help='Device for inference (auto/cpu/gpu/mps)'
)
parser.add_argument('--spots', type=str, default='parking_spots.txt', help='File to save/load parking spots')
parser.add_argument('--ws-port', type=int, default=9000, help='WebSocket server port')
parser.add_argument('--model', type=str, default='yolo12x.pt', help='Path to YOLOv8 model')
parser.add_argument('--webcam-source', type=int, default=None, help='Preferred source id for webcam7/webcamXP pages')
parser.add_argument(
    '--youtube-format',
    type=str,
    default='best[protocol*=m3u8]/best',
    help='yt-dlp format selector for YouTube streams'
)
args = parser.parse_args()

os.makedirs(args.output, exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

logging.info(f"Загрузка модели YOLOv8: {args.model}")
model = YOLO(args.model)

def resolve_device():
    if args.device == 'cpu':
        return 'cpu'
    if args.device == 'gpu':
        if torch.cuda.is_available():
            return 'cuda'
        logging.warning("CUDA недоступна, переключение на CPU")
        return 'cpu'
    if args.device == 'mps':
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return 'mps'
        logging.warning("MPS недоступен, переключение на CPU")
        return 'cpu'

    if torch.cuda.is_available():
        return 'cuda'
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return 'mps'
    return 'cpu'

device = resolve_device()
model.to(device)
logging.info(f"Используется {device.upper()} для вычислений")

if device == 'cpu' and os.path.exists(args.model):
    model_size_mb = os.path.getsize(args.model) / (1024 * 1024)
    if model_size_mb > 80:
        logging.warning(
            "Крупная модель на CPU может работать медленно. "
            "Для Mac M1 попробуйте --device mps и/или --size 320."
        )

with open("coco.names", "r") as f:
    classes = [line.strip() for line in f.readlines()]

VEHICLE_CLASSES = ["car", "motorbike", "bus", "truck"]
VEHICLE_IDS = [classes.index(cls) for cls in VEHICLE_CLASSES if cls in classes]

is_recording = False
video_writer = None
recording_start_time = None
MAX_RECORDING_MINUTES = 5

frame_queue = queue.Queue(maxsize=2)
stop_event = threading.Event()

parking_spots = []  
marking_mode = False
current_spot_id = 0

connected_clients = set()
client_lock = threading.Lock()
ws_loop = None
last_loader_error = ""
last_frame_received_at = 0.0

def build_poll_url(base_url):
    parsed = urlparse(base_url)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    nonce_key = "uniq" if "cam_" in parsed.path.lower() else "r"
    query_items = [(k, v) for k, v in query_items if k not in ("r", "uniq", "_ts")]
    query_items.append((nonce_key, str(int(time.time() * 1000))))
    return urlunparse(parsed._replace(query=urlencode(query_items)))

def upsert_query_param(url, key, value):
    parsed = urlparse(url)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    query_items = [(k, v) for k, v in query_items if k != key]
    query_items.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(query_items)))

def ensure_yawcam_session(image_url):
    parsed = urlparse(image_url)
    if not parsed.path.lower().endswith("out.jpg"):
        return image_url

    query = parse_qs(parsed.query, keep_blank_values=True)
    yawcam_id = query.get("id", [None])[0]
    if not yawcam_id:
        yawcam_id = str(random.random())
        image_url = upsert_query_param(image_url, "id", yawcam_id)

    get_url = upsert_query_param(urljoin(image_url, "get"), "id", yawcam_id)
    get_url = upsert_query_param(get_url, "r", str(int(time.time() * 1000)))

    try:
        with requests.get(get_url, timeout=(5, 5), headers={"User-Agent": "PARKOVOZ-PRO/1.0", "Connection": "close"}) as resp:
            answer = resp.text.strip().lower()
            if answer == "ok":
                logging.info("Yawcam handshake выполнен успешно")
            else:
                logging.warning(f"Yawcam handshake ответил: {answer}")
    except Exception as e:
        logging.warning(f"Ошибка Yawcam handshake: {e}")

    return image_url

def extract_img_candidates(page_html, base_url):
    candidates = []
    for match in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', page_html, re.IGNORECASE):
        src = html_lib.unescape(match.group(1).strip())
        if not src:
            continue
        candidates.append(urljoin(base_url, src))
    return candidates

def score_candidate_url(candidate_url):
    u = candidate_url.lower()
    score = 0

    if any(key in u for key in ("out.jpg", "cam_", "snapshot", "webcapture", "mjpg", "mjpeg")):
        score += 4
    if any(u.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".mjpg", ".mjpeg")):
        score += 3
    if any(bad in u for bad in ("loading", "offline", "logo", "favicon", "banner", "icon")):
        score -= 6
    if "/img/" in u:
        score -= 2

    return score

def is_youtube_url(url):
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return any(domain in host for domain in ("youtube.com", "youtu.be", "youtube-nocookie.com"))

def looks_like_video_url(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    query = parsed.query.lower()
    scheme = parsed.scheme.lower()

    if scheme in ("rtsp", "rtmp"):
        return True
    if any(token in path for token in (".m3u8", ".mp4", ".flv", ".webm", ".ts", ".avi", ".mov", ".mjpg", ".mjpeg")):
        return True
    if any(token in host for token in ("googlevideo.com", "youtube.com", "youtu.be")):
        return True
    if "videoplayback" in path or "videoplayback" in query:
        return True
    if "stream" in path and not any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png")):
        return True
    return False

def looks_like_still_image_url(url):
    path = urlparse(url).path.lower()
    return (
        path.endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp"))
        or "webcapture.jpg" in path
        or "out.jpg" in path
        or "cam_" in path
    )

def resolve_youtube_stream_url(input_url):
    if not is_youtube_url(input_url):
        return input_url

    logging.info("Обнаружен YouTube URL, попытка получить прямой поток...")

    try:
        import yt_dlp  # optional dependency
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": args.youtube_format,
            "skip_download": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(input_url, download=False)
        direct_url = info.get("url")
        if not direct_url and info.get("entries"):
            for entry in info["entries"]:
                if entry and entry.get("url"):
                    direct_url = entry["url"]
                    break
        if direct_url:
            logging.info("YouTube поток успешно получен через yt-dlp (python)")
            return direct_url
    except Exception as e:
        logging.warning(f"yt-dlp (python) не смог получить поток: {e}")

    yt_dlp_cli = shutil.which("yt-dlp")
    if yt_dlp_cli:
        try:
            output = subprocess.check_output(
                [yt_dlp_cli, "-g", "-f", args.youtube_format, input_url],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=30
            )
            direct_url = next((line.strip() for line in output.splitlines() if line.strip()), None)
            if direct_url:
                logging.info("YouTube поток успешно получен через yt-dlp (cli)")
                return direct_url
        except Exception as e:
            logging.warning(f"yt-dlp (cli) не смог получить поток: {e}")

    logging.warning("Для YouTube Live установите yt-dlp и ffmpeg. Используем исходный URL без преобразования.")
    return input_url

def resolve_camera_page_url(input_url):
    if looks_like_video_url(input_url) and not looks_like_still_image_url(input_url):
        return input_url

    headers = {"User-Agent": "PARKOVOZ-PRO/1.0", "Connection": "close"}
    try:
        with requests.get(input_url, timeout=(5, 5), headers=headers, allow_redirects=True) as resp:
            final_url = resp.url
            content_type = resp.headers.get("content-type", "").lower()
            body = resp.text if "text" in content_type or "html" in content_type else ""
    except Exception as e:
        logging.warning(f"Не удалось проверить URL камеры как веб-страницу: {e}")
        return input_url

    if "image/" in content_type or "multipart/x-mixed-replace" in content_type:
        return final_url

    html_low = body.lower()
    if not html_low:
        return input_url

    if "yawcam" in html_low or "out.jpg" in html_low:
        yawcam_url = urljoin(final_url, "out.jpg?q=30")
        yawcam_url = ensure_yawcam_session(yawcam_url)
        logging.info(f"Обнаружен интерфейс Yawcam, используем: {yawcam_url}")
        return yawcam_url

    if "webcam 7" in html_low or "webcamxp" in html_low or re.search(r"cam_\s*\+\s*currentcamera", html_low):
        selected_source = args.webcam_source
        if selected_source is None:
            query_src = parse_qs(urlparse(final_url).query).get("src", [])
            if query_src and str(query_src[0]).isdigit():
                selected_source = int(query_src[0])
        if selected_source is None:
            src_match = re.search(r"currentcamera1\s*=\s*(\d+)", html_low)
            if not src_match:
                src_match = re.search(r'<option[^>]*selected[^>]*value=["\']?(\d+)', html_low, re.IGNORECASE)
            selected_source = int(src_match.group(1)) if src_match else 1
        webcam7_url = urljoin(final_url, f"cam_{selected_source}.jpg")
        logging.info(f"Обнаружен интерфейс webcam7/webcamXP, используем: {webcam7_url}")
        return webcam7_url

    candidates = extract_img_candidates(body, final_url)
    if candidates:
        best = max(candidates, key=score_candidate_url)
        if score_candidate_url(best) > 0:
            logging.info(f"Обнаружена ссылка на изображение в HTML: {best}")
            return best

    return input_url

async def websocket_handler(websocket):
    with client_lock:
        connected_clients.add(websocket)
    logging.info(f"Новый клиент подключен. Всего клиентов: {len(connected_clients)}")
    
    try:
        async for message in websocket:
            try:
                if message == "ping":
                    continue
                
                data = json.loads(message)
                if data.get('type') == 'request_data':
                    if parking_spots:
                        spot_states = [s[2] for s in parking_spots]
                        free_count = spot_states.count(0)
                        occupied_count = spot_states.count(1)
                        
                        await websocket.send(json.dumps({
                            'type': 'parking_data',
                            'data': spot_states,
                            'free': free_count,
                            'occupied': occupied_count,
                            'timestamp': datetime.now().isoformat()
                        }))
            except json.JSONDecodeError:
                continue
            except Exception as e:
                logging.error(f"Ошибка обработки сообщения от клиента: {e}")
    except Exception as e:
        logging.error(f"WebSocket error: {e}")
    finally:
        with client_lock:
            connected_clients.discard(websocket)

async def send_to_clients(data):
    if connected_clients:
        for client in connected_clients.copy():
            try:
                await client.send(json.dumps(data))
            except websockets.exceptions.ConnectionClosed:
                logging.warning("Клиент отключился")
                with client_lock:
                    connected_clients.discard(client)

def websocket_process_request(connection, request):
    connection_header = request.headers.get("Connection", "")
    upgrade_header = request.headers.get("Upgrade", "")
    has_upgrade = "upgrade" in connection_header.lower()
    is_websocket = upgrade_header.lower() == "websocket"

    if not has_upgrade or not is_websocket:
        return connection.respond(
            426,
            "WebSocket endpoint on this port. "
            "Use ws://<host>:<port> from WebSocket client."
        )
    return None

def start_websocket_server():
    global ws_loop
    ws_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(ws_loop)
    
    async def start():
        server = await websockets.serve(
            websocket_handler, 
            "0.0.0.0", 
            args.ws_port,
            process_request=websocket_process_request
        )
        logging.info(f"WebSocket сервер запущен на порту {args.ws_port}")
        await server.wait_closed()
    
    ws_loop.run_until_complete(start())
    ws_loop.run_forever()

def image_loader(url):
    global last_loader_error, last_frame_received_at
    last_frame = None
    session = requests.Session()
    session.headers.update({
        "User-Agent": "PARKOVOZ-PRO/1.0",
        "Connection": "close",
    })
    last_error_log_at = 0.0

    while not stop_event.is_set():
        try:
            poll_url = build_poll_url(url)
            with session.get(poll_url, timeout=(2, 2)) as response:
                response.raise_for_status()
                img_array = np.frombuffer(response.content, dtype=np.uint8)
            frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if frame is not None:
                last_frame = frame
                last_loader_error = ""
                last_frame_received_at = time.time()
                if frame_queue.full():
                    try:
                        frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                frame_queue.put(frame.copy())
            else:
                raise ValueError("Кадр не декодирован (frame is None)")
        except Exception as e:
            last_loader_error = str(e)
            now = time.time()
            if now - last_error_log_at > 5:
                logging.warning(f"Ошибка получения кадра: {e}")
                last_error_log_at = now
            if last_frame is not None:
                frame_queue.put(last_frame.copy())
        time.sleep(0.05)

def detect_vehicles(image):
    height, width = image.shape[:2]
    
    results = model.predict(
        image,
        imgsz=args.size,
        conf=args.confidence,
        iou=args.nms,
        device=device,
        verbose=False
    )
    
    boxes = []
    confidences = []
    class_ids = []
    centers = []

    for result in results:
        if result.boxes is not None:
            for box in result.boxes:
                if int(box.cls) in VEHICLE_IDS:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(width, x2), min(height, y2)
                    
                    w = x2 - x1
                    h = y2 - y1
                    
                    center_x = int((x1 + x2) / 2)
                    center_y = int((y1 + y2) / 2)
                    
                    boxes.append([x1, y1, w, h])
                    centers.append((center_x, center_y))
                    confidences.append(float(box.conf))
                    class_ids.append(int(box.cls))

    indices = list(range(len(boxes))) if boxes else []
    
    return boxes, confidences, class_ids, indices, centers

def create_video_capture(url):
    cap = cv2.VideoCapture(url)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FPS, args.fps)
    return cap

def save_parking_spots(filename):
    with open(filename, 'w') as f:
        for spot in parking_spots:
            f.write(f"{spot[0]},{spot[1]},{spot[2]}\n")
    logging.info(f"Сохранено {len(parking_spots)} парковочных мест в {filename}")

def load_parking_spots(filename):
    global parking_spots
    if os.path.exists(filename):
        parking_spots = []
        with open(filename, 'r') as f:
            for line in f:
                parts = line.strip().split(',')
                if len(parts) >= 3:
                    x, y, status = int(parts[0]), int(parts[1]), int(parts[2])
                    parking_spots.append((x, y, status))
        logging.info(f"Загружено {len(parking_spots)} парковочных мест из {filename}")
        return True
    return False

def mouse_callback(event, x, y, flags, param):
    global marking_mode, parking_spots, frame_copy
    
    if marking_mode:
        if event == cv2.EVENT_LBUTTONDOWN:
            parking_spots.append((x, y, 0))
            logging.info(f"Добавлено свободное место {len(parking_spots)}: ({x}, {y})")
            cv2.circle(frame_copy, (x, y), 8, (0, 255, 0), -1)
            cv2.putText(frame_copy, str(len(parking_spots)), (x+10, y+10), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.imshow("Mark Parking Spots", frame_copy)
        
        elif event == cv2.EVENT_RBUTTONDOWN:
            parking_spots.append((x, y, 1))
            logging.info(f"Добавлено занятое место {len(parking_spots)}: ({x}, {y})")
            cv2.circle(frame_copy, (x, y), 8, (0, 0, 255), -1)
            cv2.putText(frame_copy, str(len(parking_spots)), (x+10, y+10), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            cv2.imshow("Mark Parking Spots", frame_copy)

def mark_parking_spots(frame):
    global marking_mode, frame_copy, parking_spots
    
    marking_mode = True
    frame_copy = frame.copy()
    
    cv2.namedWindow("Mark Parking Spots")
    cv2.setMouseCallback("Mark Parking Spots", mouse_callback, param=frame)
    
    logging.info("\n=== РЕЖИМ РАЗМЕТКИ ПАРКОВОЧНЫХ МЕСТ ===")
    logging.info("ЛКМ - добавить свободное место (зеленое)")
    logging.info("ПКМ - добавить занятое место (красное)")
    logging.info("'z' - удалить последнее добавленное место")
    logging.info("'s' - сохранить разметку")
    logging.info("'c' - отменить и выйти")
    
    while True:
        cv2.imshow("Mark Parking Spots", frame_copy)
        key = cv2.waitKey(1) & 0xFF
        
        if key == ord('s'):
            if parking_spots:
                save_parking_spots(args.spots)
                marking_mode = False
                cv2.destroyWindow("Mark Parking Spots")
                return True
            else:
                logging.warning("Нет парковочных мест для сохранения!")
        
        elif key == ord('c'):
            parking_spots = []
            marking_mode = False
            cv2.destroyWindow("Mark Parking Spots")
            logging.info("Разметка отменена")
            return False
        
        elif key == ord('z'):
            if parking_spots:
                removed = parking_spots.pop()
                logging.info(f"Удалено парковочное место: {removed}")
                frame_copy = frame.copy()
                for i, (x, y, status) in enumerate(parking_spots):
                    color = (0, 255, 0) if status == 0 else (0, 0, 255)
                    cv2.circle(frame_copy, (x, y), 8, color, -1)
                    cv2.putText(frame_copy, str(i+1), (x+10, y+10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                cv2.imshow("Mark Parking Spots", frame_copy)

def process_stream():
    global is_recording, video_writer, recording_start_time, parking_spots, marking_mode, last_loader_error

    logging.info(f"Запуск мониторинга парковки: {args.url}")

    is_video_stream = False
    cap = None
    last_success_time = time.time()

    if looks_like_video_url(args.url) and not looks_like_still_image_url(args.url):
        try:
            cap = create_video_capture(args.url)
            if cap.isOpened():
                ret, test_frame = cap.read()
                if ret and test_frame is not None:
                    is_video_stream = True
                    logging.info("Режим: видеопоток")
                else:
                    cap.release()
                    cap = None
        except:
            pass

    if not is_video_stream:
        logging.info("Режим: Статичное изображение (многопоточная загрузка)")
        loader_thread = threading.Thread(target=image_loader, args=(args.url,), daemon=True)
        loader_thread.start()

    cv2.namedWindow("Parking Lot Monitoring", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Parking Lot Monitoring", 1000, 700)

    frame_count = 0
    total_vehicles = 0
    start_time = time.time()
    last_frame_time = time.time()
    frame = None
    reconnect_attempts = 0
    last_stat_update = time.time()
    stats_interval = 0.5
    current_fps = 0
    vehicle_count = 0
    first_frame_processed = False
    no_frame_warning_at = 0.0
    last_rendered_frame = None

    try:
        while True:
            current_time = time.time()
            elapsed = current_time - last_frame_time
            sleep_time = (1.0 / args.fps) - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

            last_frame_time = time.time()
            frame_count += 1

            if is_video_stream and cap is not None:
                ret, frame = cap.read()
                if not ret or frame is None:
                    if current_time - last_success_time > 5:
                        logging.warning("Ошибка чтения видеопотока. Попытка переподключения...")
                        cap.release()
                        cap = create_video_capture(args.url)
                        reconnect_attempts += 1

                        if reconnect_attempts > 5 or not cap.isOpened():
                            logging.warning("Не удалось восстановить видеопоток. Переключение на режим статичного изображения")
                            is_video_stream = False
                            loader_thread = threading.Thread(target=image_loader, args=(args.url,), daemon=True)
                            loader_thread.start()
                    continue
                last_success_time = current_time
                reconnect_attempts = 0
            else:
                try:
                    frame = frame_queue.get(timeout=0.3)
                except queue.Empty:
                    frame = None

            if frame is None:
                if current_time - no_frame_warning_at > 5:
                    if last_loader_error:
                        logging.warning(f"Ожидание кадра от камеры. Последняя ошибка: {last_loader_error}")
                    elif last_frame_received_at > 0:
                        logging.warning("Временная потеря обновлений потока, показываем последний кадр")
                    else:
                        logging.warning("Ожидание первого кадра от камеры...")
                    no_frame_warning_at = current_time

                if last_rendered_frame is not None:
                    display_frame = last_rendered_frame.copy()
                    cv2.putText(display_frame, "No new frame, showing last image", (20, 40),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
                    cv2.imshow("Parking Lot Monitoring", display_frame)
                else:
                    waiting_frame = np.zeros((700, 1000, 3), dtype=np.uint8)
                    cv2.putText(waiting_frame, "Waiting for camera frame...", (50, 100),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
                    cv2.putText(waiting_frame, f"Source: {args.url[:80]}", (50, 150),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 180, 180), 1)
                    cv2.putText(waiting_frame, "Press 'q' to quit", (50, 200),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (120, 220, 120), 2)
                    cv2.imshow("Parking Lot Monitoring", waiting_frame)

                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    break
                elif key == ord('m'):
                    is_video_stream = not is_video_stream
                    if cap is not None:
                        cap.release()
                        cap = None
                    if is_video_stream:
                        logging.info("Переключено на режим видеопотока")
                        cap = create_video_capture(args.url)
                    else:
                        logging.info("Переключено на режим статичного изображения")
                        stop_event.clear()
                        loader_thread = threading.Thread(target=image_loader, args=(args.url,), daemon=True)
                        loader_thread.start()
                continue

            last_loader_error = ""
            
            if not first_frame_processed:
                first_frame_processed = True
                if not os.path.exists(args.spots):
                    logging.info("Файл с разметкой не найден, запуск режима разметки...")
                    if mark_parking_spots(frame):
                        logging.info("Разметка парковочных мест сохранена")
                    else:
                        logging.info("Разметка парковочных мест отменена, выход")
                        break
                else:
                    load_parking_spots(args.spots)

            boxes, confidences, class_ids, indices, centers = detect_vehicles(frame)
            vehicle_count = len(indices) if isinstance(indices, list) else 0
            total_vehicles += vehicle_count

            occupied_spots = [False] * len(parking_spots)
            
            for i, (x, y, _) in enumerate(parking_spots):
                for center in centers:
                    distance = np.sqrt((x - center[0])**2 + (y - center[1])**2)
                    if distance < 30:
                        occupied_spots[i] = True
                        break

            for i, occupied in enumerate(occupied_spots):
                parking_spots[i] = (parking_spots[i][0], parking_spots[i][1], 1 if occupied else 0)
            
            spot_states = [s[2] for s in parking_spots]
            free_count = spot_states.count(0)
            occupied_count = spot_states.count(1)
            
            if ws_loop and ws_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    send_to_clients({
                        'type': 'parking_data',
                        'data': spot_states,
                        'free': free_count,
                        'occupied': occupied_count,
                        'timestamp': datetime.now().isoformat()
                    }), 
                    ws_loop
                )

            if vehicle_count > 0:
                for i, box in enumerate(boxes):
                    (x, y, w, h) = box
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                    
                    label = f"{classes[class_ids[i]]}: {confidences[i]:.2f}"
                    cv2.putText(frame, label, (x, y - 5),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            free_count = 0
            for i, (x, y, status) in enumerate(parking_spots):
                if status == 1:
                    color = (0, 0, 255)  
                else:
                    color = (0, 255, 0)  
                    free_count += 1
                
                cv2.circle(frame, (x, y), 8, color, -1)
                cv2.putText(frame, str(i+1), (x+10, y+10), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

            current_time = time.time()
            if current_time - last_stat_update > stats_interval:
                elapsed_time = current_time - start_time
                current_fps = frame_count / elapsed_time if elapsed_time > 0 else 0
                frame_count = 0
                start_time = current_time
                last_stat_update = current_time

            
            if is_recording:
                cv2.putText(frame, "REC", (frame.shape[1] - 100, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                recording_time = time.time() - recording_start_time
                mins, secs = divmod(int(recording_time), 60)
                cv2.putText(frame, f"{mins:02d}:{secs:02d}", (frame.shape[1] - 100, 80),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

            if is_recording and video_writer is not None:
                video_writer.write(frame)

                if time.time() - recording_start_time > MAX_RECORDING_MINUTES * 60:
                    stop_recording()

            last_rendered_frame = frame.copy()
            cv2.imshow("Parking Lot Monitoring", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('s'):
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = os.path.join(args.output, f"parking_snapshot_{timestamp}.jpg")
                cv2.imwrite(filename, frame)
                logging.info(f"Снимок сохранен: {filename}")
            elif key == ord('r'):
                if is_recording:
                    stop_recording()
                else:
                    start_recording(frame)
            elif key == ord('m'):
                is_video_stream = not is_video_stream
                if cap is not None:
                    cap.release()
                    cap = None
                if is_video_stream:
                    logging.info("Переключено на режим видеопотока")
                    cap = create_video_capture(args.url)
                else:
                    logging.info("Переключено на режим статичного изображения")
                    stop_event.clear()
                    loader_thread = threading.Thread(target=image_loader, args=(args.url,), daemon=True)
                    loader_thread.start()
            elif key == ord('p'):
                if mark_parking_spots(frame):
                    logging.info("Разметка парковочных мест сохранена")
                else:
                    logging.info("Разметка парковочных мест отменена")

    except KeyboardInterrupt:
        logging.info("Программа остановлена пользователем")
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
    finally:
        stop_event.set()
        if cap is not None and cap.isOpened():
            cap.release()
        if is_recording:
            stop_recording()
        cv2.destroyAllWindows()


def start_recording(frame):
    global is_recording, video_writer, recording_start_time

    if not is_recording:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(args.output, f"parking_recording_{timestamp}.avi")

        height, width = frame.shape[:2]

        fourcc = cv2.VideoWriter_fourcc(*'XVID')
        video_writer = cv2.VideoWriter(output_path, fourcc, args.fps, (width, height))

        if video_writer.isOpened():
            is_recording = True
            recording_start_time = time.time()
            logging.info(f"Начата запись видео: {output_path}")
        else:
            logging.error("Не удалось создать видеофайл для записи")

def stop_recording():
    global is_recording, video_writer

    if is_recording and video_writer is not None:
        video_writer.release()
        is_recording = False
        logging.info("Запись видео остановлена")

def main():
    original_camera_url = args.url
    was_youtube_input = is_youtube_url(args.url)
    args.url = resolve_youtube_stream_url(args.url)
    if was_youtube_input and args.url == original_camera_url:
        logging.error("Не удалось получить прямой поток YouTube. Установите yt-dlp и ffmpeg, затем повторите запуск.")
        return
    args.url = resolve_camera_page_url(args.url)
    if args.url != original_camera_url:
        logging.info(f"URL камеры преобразован: {original_camera_url} -> {args.url}")

    print("=" * 70)
    print(f"Система мониторинга парковки | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)
    print("Инструкция:")
    print("- Нажмите 's' для сохранения снимка")
    print("- Нажмите 'r' для начала/остановки записи видео")
    print("- Нажмите 'm' для переключения режима (поток/изображение)")
    print("- Нажмите 'p' для разметки парковочных мест")
    print("- Нажмите 'q' для выхода из программы")
    print(f"\nИспользуемая камера: {args.url}")
    print(f"Модель: YOLOv8 | Размер: {args.size}px | Устройство: {device.upper()}")
    print("=" * 70)

    if not os.path.exists(args.model):
        print(f"[ERROR] Файл модели YOLOv8 не найден: {args.model}")
        print("Скачайте предобученные модели с https://github.com/ultralytics/ultralytics")
        return

    ws_thread = threading.Thread(target=start_websocket_server, daemon=True)
    ws_thread.start()

    process_stream()

if __name__ == "__main__":
    main()
