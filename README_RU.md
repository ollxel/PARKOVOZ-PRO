# PARKOVOZ-PRO  - Система мониторинга парковок  

[English README](README.md) | [Russian README](README_RU.md)

![](screenshot.jpg)
*demonstration of the system*

**PARKOVOZ-PRO** - комплексное решение для автоматического мониторинга парковочных мест с использованием нейросетей. Система анализирует видеопоток в реальном времени, определяет свободные/занятые места и предоставляет удобный веб-интерфейс для визуализации данных.

## Архитектура

PARKOVOZ-PRO состоит из трёх основных компонентов:

- **Backend (Python + OpenCV + YOLO)** — обрабатывает видеопоток и выполняет детекцию автомобилей
- **Frontend (JavaScript + Three.js)** — отображает состояние парковочных мест в реальном времени
- **Слой коммуникации (WebSocket)** — передаёт результаты детекции от backend к frontend

Backend получает видеопотоки с IP-камер (RTSP/HTTP), выполняет инференс модели YOLO, определяет занятость парковочных мест и отправляет обновления во frontend-интерфейс.

## Установка и запуск

### Требования
- Python 3.7+
- NVIDIA GPU (рекомендуется)
- Доступ к RTSP/HTTP камере

### Пошаговая установка

1. Клонируйте репозиторий:
```bash
git clone https://github.com/ollxel/PARKOVOZ-PRO
cd PARKOVOZ-PRO
```

2. Установите зависимости:
```bash
pip3 install -r requirements.txt
```

3. Загрузите модель YOLOv12x:
```bash
wget https://example.com/yolov12x.pt -O RealParking/backend/yolov12x.pt
cp RealParking/backend/yolov12x.pt ToyParking/backend/
```

### Запуск системы
Backend:
```bash
python3 main.py --url rtsp://your_camera_ip --device auto
```

Frontend:
```bash
python3 -m http.server #port - 8000
```

Примечание: порт `9000` используется только для WebSocket (`ws://localhost:9000`), а не для открытия в браузере как обычной HTTP-страницы.

Поддерживаются HTML-страницы камер (Yawcam, webcam7/webcamXP): можно передавать URL страницы, скрипт сам найдет прямой `jpg` поток.

Примеры:
```bash
# Yawcam page URL (автоопределение out.jpg)
python3 main.py --url http://206.169.218.170:8081/ --device auto --size 320

# webcam7 page URL, выбор источника Source 4
python3 main.py --url http://camera-host:8080/ --webcam-source 4 --device auto --size 320

# YouTube Live URL (нужны yt-dlp и ffmpeg)
python3 main.py --url "https://www.youtube.com/watch?v=LIVE_ID" --device auto --size 320
```

### Параметры запуска
| Параметр | Описание | По умолчанию |
|----------|----------|--------------|
| `--url` | URL видеопотока | Обязательный |
| `--model` | Путь к модели | `yolov12x.pt` |
| `--device` | Устройство (`auto`/`cpu`/`gpu`/`mps`) | `auto` |
| `--ws-port` | WebSocket порт | `9000` |
| `--webcam-source` | Источник для webcam7/webcamXP | авто |
| `--youtube-format` | Формат для yt-dlp (YouTube Live) | `best[protocol*=m3u8]/best` |


### Горячие клавиши
| Комбинация | Действие |
|------------|----------|
| `S` | Сохранить снимок |
| `R` | Старт/стоп записи |
| `M` | Переключение режима |
| `P` | Режим разметки |
| `Q` | Выход из системы |

## Лицензия
Проект распространяется под лицензией **GPLv3**.  
Полный текст доступен в файле https://github.com/ollxel/PARKOVOZ-PRO/blob/main/LICENSE
