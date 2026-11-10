from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
import tempfile
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.exceptions import HTTPException

from db import (
    close_db,
    get_all_videos,
    get_company_by_path,
    get_video,
    init_db,
    save_video,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
ALLOWED_EXTENSIONS = {".webm", ".mp4", ".mkv", ".mov", ".ogv"}

mimetypes.add_type("video/webm", ".webm")

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
app.json.sort_keys = False
app.json.ensure_ascii = False
app.teardown_appcontext(close_db)

upload_lock = threading.Lock()


@app.errorhandler(HTTPException)
def handle_http_exception(e: HTTPException):
    return jsonify({"error": e.description}), e.code


def sanitize_filename(name: str) -> str:
    clean = re.sub(r"[^\w\-_.]", "_", name.strip())
    clean = re.sub(r"_+", "_", clean).strip("_.")
    if not clean:
        clean = "video_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return clean


def pick_filename(company_name: str, ext: str) -> Path:
    stem = sanitize_filename(company_name)
    filename = f"{stem}{ext}"
    candidate = (UPLOAD_FOLDER / filename).resolve()
    idx = 2
    while True:
        owner = get_company_by_path(str(candidate))
        if owner is None or owner == company_name:
            return candidate
        filename = f"{stem}_{idx}{ext}"
        candidate = (UPLOAD_FOLDER / filename).resolve()
        idx += 1


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", videos=get_all_videos())


@app.route("/upload", methods=["POST"])
def upload_video():
    if "file" not in request.files:
        return jsonify({"error": "Missing 'file' in multipart/form-data"}), 400

    file = request.files["file"]
    company_name = request.form.get("company_name", "").strip()

    if not file or not file.filename:
        return jsonify({"error": "Empty or missing file"}), 400

    if not company_name:
        return jsonify({"error": "Missing 'company_name' parameter"}), 400

    ext = Path(file.filename).suffix.lower() or ".webm"
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({
            "error": f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        }), 400

    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

    with upload_lock:
        dest_path = pick_filename(company_name, ext)
        existing_record = get_video(company_name)

        fd, tmp_file = tempfile.mkstemp(dir=str(UPLOAD_FOLDER), prefix="upload_", suffix=".part")
        os.close(fd)
        tmp_path = Path(tmp_file)

        try:
            file.save(str(tmp_path))
            if tmp_path.stat().st_size == 0:
                return jsonify({"error": "Uploaded file is empty"}), 400

            tmp_path.replace(dest_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

        if existing_record:
            old_path = Path(existing_record["file_path"])
            if old_path != dest_path and old_path.is_file():
                try:
                    old_path.unlink()
                except OSError as e:
                    logger.warning("Could not delete old file %s: %s", old_path, e)

        host_url = request.host_url.rstrip("/")
        video_url = f"{host_url}/media/{dest_path.name}"

        save_video(company_name, str(dest_path), video_url)
        logger.info("Uploaded video for '%s' -> %s", company_name, video_url)

    return jsonify({"company_name": company_name, "video_url": video_url}), 200


@app.route("/get_video_url", methods=["GET"])
def get_video_url():
    company_name = request.args.get("company_name", "").strip()
    if not company_name:
        return jsonify({"error": "Missing 'company_name' query parameter"}), 400

    video = get_video(company_name)
    if not video:
        logger.warning("Company not found: '%s'", company_name)
        return jsonify({"error": f"Company '{company_name}' not found"}), 404

    logger.info("Found video URL for '%s'", company_name)
    return jsonify({"company_name": video["company_name"], "video_url": video["video_url"]}), 200


@app.route("/media/<path:filename>", methods=["GET"])
def serve_media(filename: str):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/videos", methods=["GET"])
def list_videos():
    return jsonify({"videos": get_all_videos()}), 200


def main() -> None:
    init_db()
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", "8000"))
    logger.info("Starting web service on %s:%d", host, port)
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
