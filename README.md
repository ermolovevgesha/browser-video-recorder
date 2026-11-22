# Overview
A project for automatically recording videos in a browser (Chromium / Playwright) based on a list of companies from a CSV file, applying smooth zoom post-processing via `ffmpeg`, and uploading videos to a containerized web service.

## Architecture & Technology Stack
- **Web Container (`web`)**: Python 3.12, Apache 2.4 + `mod_wsgi`, Flask, `ffmpeg`
- **Database Container (`db`)**: PostgreSQL 16 (`psycopg2`)
- **Orchestration**: Docker & Docker Compose (with named volumes `db_data` and `uploads` and DB healthcheck)
- **Video Recorder / Post-processor**: Python 3.9+, Playwright (Chromium), Requests, `ffmpeg` (`zoompan` + `libvpx-vp9`)

---

## Running with Docker Compose (Recommended)

### 1. Configure Environment Variables
Copy the example environment file (`.env` is git-ignored so secrets like `DB_PASSWORD` are not committed):
```bash
cp .env.example .env
```

### 2. Build and Start Containers
```bash
docker compose up --build -d
```
Web gallery: `http://localhost:8000` (data and media persist in Docker volumes `db_data` and `uploads`).

---

## Local Client Setup (Video Capture, Zoom & Upload)

1. Create an environment and install client + test dependencies (skipping Docker-only `mod-wsgi`):

Using `uv`:
```bash
uv sync
uv run playwright install chromium
```

Or with `pip`:
```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/macOS
pip install playwright requests flask psycopg2-binary pytest
playwright install chromium
```

2. Run video capture with upload and preview against the Dockerized web service:
```bash
# Automatic programmatic zoom during recording
python capture_video.py --server http://localhost:8000 --view-seconds 5 --zoom

# Visible browser window for manual F5 zoom testing
python capture_video.py --headed --server http://localhost:8000
```

Parameters:
- `--server`: Web service base URL (`http://localhost:8000`). If omitted, videos are saved only locally in `video/`.
- `--view-seconds`: Preview duration in seconds (default `5.0`; `0` = wait for Enter).
- `--csv`: Path to CSV file (default `input.csv`).
- `--zoom`: Enable programmatic zoom-in/out events during the scenario.
- `--headed`: Open a visible browser window (`HEADLESS=0`) for manual cursor control and `F5` zoom.

### Zoom Behavior (`qscript.py`)
- **`F5` toggle**: 1st press starts zoom-in (green ring), 2nd press starts zoom-out (orange ring) without reloading the page. If recording ends while zoomed in, zoom holds to the end.
- **User vs. script cursor (`--headed`)**: User mouse/F5 activity pauses automation (`2.0s` cooldown), then the script smoothly resumes from the user's cursor position.
- **Programmatic zoom (`ZoomAt` / `window.__zoomAt`)**: Scenario calls use explicit `state="in"` / `state="out"` so manual `F5` presses never invert the script's zoom phase (`window.__zoomAt(x, y)` without state toggles).
- **Timeline sync & sidecar**: Each zoom event flashes a `6×6` px top-left sync marker so `qscript.py` aligns timestamps to Playwright's actual first recorded frame before writing `<video>.zoom.json` (written only when events exist).

### Standalone Zoom Post-Processing (`apply_zoom.py`)
```bash
# Preview the generated ffmpeg command without encoding (works even without local ffmpeg)
python apply_zoom.py --video "video/Acme E-Commerce.webm" --dry-run

# Render zoomed .webm locally (<stem>_zoomed.webm)
python apply_zoom.py --video "video/Acme E-Commerce.webm"

# Specify an explicit ffmpeg binary when not on PATH
python apply_zoom.py --video "video/Acme E-Commerce.webm" --ffmpeg /path/to/ffmpeg
```

---

## Web Service API

- `GET /` — Web interface (video gallery displaying zoomed videos when processed).
- `POST /upload` — video upload (`multipart/form-data` with `file`, `company_name`, and optional `sidecar` `.zoom.json`). When a sidecar is provided, creates a processing job and automatically starts zoom post-processing in the background (or synchronously with `?sync=1`).
- `POST /jobs/<id>/process` — runs (or re-runs) zoom post-processing for job `<id>`, storing `processed_video_url` in PostgreSQL (`jobs` and `videos` tables). Supports optional `sidecar` upload and `?async=1`.
- `POST /videos/<id>/process` — creates and runs a zoom post-processing job for video `<id>`.
- `GET /jobs` / `GET /jobs/<id>` — returns all jobs or status/URL details for a specific job.
- `GET /get_video_url?company_name=...` — retrieves `video_url`, `processed_video_url`, `processing_status`, and `job_id` for a company.
- `GET /media/<filename>` — serves uploaded and processed video files directly via Apache `Alias /media /app/uploads` (or Flask fallback when running standalone).
- `GET /videos` — returns a list of all registered videos in JSON.
- `GET /upload_companies` — minimal HTML form for uploading a semicolon-delimited companies CSV.
- `POST /upload_companies` — imports/upserts companies from CSV (`multipart/form-data`, field `file`) without overwriting existing enrichment fields. Returns `{"imported": N, "updated": M, "total_rows": K}`.
- `GET /companies` — returns all companies as a JSON array (optional `?status=N` filter).
- `GET /companies/<company_name>` — returns a single company (including extracted Impressum fields) or `404`.

---

## Company Enrichment (`enrich_companies.py`)

Set `DEEPSEEK_API_KEY` in `.env`, optionally override `DEEPSEEK_MODEL` (default `deepseek-chat`).

1. Upload `companies.csv` via `http://localhost:8000/upload_companies` or `curl`:
```bash
curl -F "file=@companies.csv" http://localhost:8000/upload_companies
```

2. Run the enrichment script (inside the Docker container or locally with `DB_HOST=127.0.0.1`):
```bash
# Dry run: fetch Impressum pages and inspect optimized text/token counts without calling DeepSeek
docker compose exec web python enrich_companies.py --dry-run

# Process all not-yet-OK companies (default: status IS NULL OR status <> 0)
docker compose exec web python enrich_companies.py

# Target one company, limit count, or filter by status
docker compose exec web python enrich_companies.py --company "nutrimmun GmbH"
docker compose exec web python enrich_companies.py --limit 5 --status 4
```

Status codes (`companies.status`): `NULL` pending, `0` `OK`, `1` `NO_IMPRESSUM_LINK_FOUND`, `2` `PARSING_FAILED`, `3` `ADDRESS_INCOMPLETE`, `4` `ERROR`, `5` `NO_WEBSITE`.

---

## Running Tests

```bash
uv run pytest -v   # or pytest -v inside an activated .venv
```
