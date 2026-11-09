# Overview
A project for automatically recording videos in a browser (Chromium / Playwright) based on a list of companies from a CSV file, uploading the videos to a local web service, and viewing them.

## Technology Stack
- Python 3.9+
- Flask
- SQLite (WAL mode)
- Requests
- Playwright

---

## Installation

1. Create and activate a virtual environment:
```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/macOS
```

2. Install dependencies:
```bash
pip install -r requirements.txt
playwright install chromium ffmpeg
```

---

## Getting Started

### 1. Web Service
```bash
python web_service.py
```

By default, the server runs at `http://0.0.0.0:8000`. The port and host can be overridden using the `FLASK_PORT` and `FLASK_HOST` environment variables.

By opening `http://localhost:8000/` in a browser, you can view a web gallery of all uploaded videos with a player.

### 2. Recording a Video with Upload and Preview
The script reads company names from a CSV file, performs a search on DuckDuckGo, records a video of the page load, uploads it to the server, and opens a Chromium window for preview:

```bash
python capture_video.py --server http://localhost:8000 --view-seconds 5
```

Parameters:
- `--server`: The URL of the web service. If not specified, videos are saved only locally.
- `--view-seconds`: Preview duration in seconds (default `5.0`). If set to `0`, the script will wait for the Enter key to be pressed in the console before moving on to the next company.
- `--csv`: Path to the CSV file (default `input.csv`).

### 3. Local Recording Without a Server
```bash
python capture_video.py
```
Videos are saved to the `video/` directory.

---

## Web Service API

- `GET /` — Web interface (video gallery with a built-in HTML5 player).
- `POST /upload` — video upload (`multipart/form-data` with `file` and `company_name` fields). Returns `{"company_name": "...", "video_url": "..."}`.
- `GET /get_video_url?company_name=...` — retrieves the video URL for a specified company.
- `GET /media/<filename>` — serves video files with support for partial content (`Accept-Ranges: bytes` for seeking).
- `GET /videos` — returns a list of all registered videos in JSON.
