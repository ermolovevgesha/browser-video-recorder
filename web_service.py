from __future__ import annotations

import os
import re
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from db import close_db, get_all_videos, get_video, init_db, save_video

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
app.teardown_appcontext(close_db)


def sanitize_filename(name: str) -> str:
    clean = re.sub(r"[^\w\-_.]", "_", name.strip())
    clean = re.sub(r"_+", "_", clean)
    return clean or "video"


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

    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix or ".webm"
    filename = f"{sanitize_filename(company_name)}{ext}"
    dest_path = (UPLOAD_FOLDER / filename).resolve()

    file.save(str(dest_path))

    host_url = request.host_url.rstrip("/")
    video_url = f"{host_url}/media/{filename}"

    save_video(company_name, str(dest_path), video_url)

    return jsonify({"company_name": company_name, "video_url": video_url}), 200


@app.route("/get_video_url", methods=["GET"])
def get_video_url():
    company_name = request.args.get("company_name", "").strip()
    if not company_name:
        return jsonify({"error": "Missing 'company_name' query parameter"}), 400

    video = get_video(company_name)
    if not video:
        return jsonify({"error": f"Company '{company_name}' not found"}), 404

    return jsonify({"company_name": video["company_name"], "video_url": video["video_url"]}), 200


@app.route("/media/<path:filename>", methods=["GET"])
def serve_media(filename: str):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/videos", methods=["GET"])
def list_videos():
    return jsonify({"videos": get_all_videos()}), 200


def main() -> None:
    init_db()
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", "8000"))
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
