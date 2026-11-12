# Overview
A project for automatically recording videos in a browser (Chromium / Playwright) based on a list of companies from a CSV file, uploading the videos to a containerized web service (Apache + mod_wsgi + Flask + PostgreSQL), and viewing them.

## Architecture & Technology Stack
- **Web Container (`web`)**: Python 3.12, Apache 2.4 + `mod_wsgi`, Flask
- **Database Container (`db`)**: PostgreSQL 16 (`psycopg2`)
- **Orchestration**: Docker & Docker Compose (with named volumes `db_data` and `uploads` and DB healthcheck)
- **Video Recorder / Client**: Python 3.9+, Playwright (Chromium), Requests

---

## Running with Docker Compose (Recommended)

### 1. Configure Environment Variables
Copy the example environment file (`.env` is git-ignored so secrets like `DB_PASSWORD` are not committed):
```bash
cp .env.example .env
```

### 2. Build and Start Containers
```bash
docker compose up --build
```
*(Or add `-d` to run in detached mode: `docker compose up --build -d`)*

- **`web`** maps host port `8000` to Apache port `80` inside the container (`http://localhost:8000`).
- **`db`** runs `postgres:16` with a healthcheck (`pg_isready`); `web` waits until `db` is healthy before starting.
- Uploaded videos are persisted in the `uploads` named volume (`/app/uploads`), and PostgreSQL data is persisted in the `db_data` named volume (`/var/lib/postgresql/data`).
- Database connection settings (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`) are passed via environment variables from `.env`.

### 3. Stop / Restart Containers
```bash
docker compose down
docker compose up
```
All database records and uploaded video files survive container restarts and `docker compose down` / `docker compose up`.

### 4. Verify Inside the Container
```bash
docker compose exec web curl http://localhost/
```

---

## Local Client Setup (Video Capture & Upload)

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

3. Run video capture with upload and preview against the Dockerized web service:
```bash
python capture_video.py --server http://localhost:8000 --view-seconds 5
```

Parameters:
- `--server`: The URL of the web service (`http://localhost:8000`). If not specified, videos are saved only locally in `video/`.
- `--view-seconds`: Preview duration in seconds (default `5.0`). If set to `0`, the script waits for the Enter key in the console before moving to the next company.
- `--csv`: Path to the CSV file (default `input.csv`).

---

## Web Service API

- `GET /` — Web interface (video gallery with a built-in HTML5 player).
- `POST /upload` — video upload (`multipart/form-data` with `file` and `company_name` fields). Returns `{"company_name": "...", "video_url": "..."}`.
- `GET /get_video_url?company_name=...` — retrieves the video URL for a specified company.
- `GET /media/<filename>` — serves uploaded video files directly via Apache `Alias /media /app/uploads` (or Flask fallback when running standalone).
- `GET /videos` — returns a list of all registered videos in JSON.
