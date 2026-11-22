from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.exceptions import HTTPException

import apply_zoom
from db import (
    claim_job,
    close_db,
    complete_job,
    create_job,
    fail_job,
    get_all_companies,
    get_all_jobs,
    get_all_videos,
    get_company,
    get_company_by_path,
    get_job,
    get_video,
    get_video_by_id,
    init_db,
    save_video,
    update_job_sidecar,
    upsert_companies,
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


def _extract_sidecar_text_from_request() -> str | None:
    sidecar_file = request.files.get("sidecar") or request.files.get("zoom_sidecar")
    if sidecar_file and sidecar_file.filename:
        raw_bytes = sidecar_file.read()
        raw_text = raw_bytes.decode("utf-8")
        apply_zoom.parse_sidecar(raw_text)
        return raw_text

    form_json = request.form.get("sidecar_json")
    if form_json and form_json.strip():
        apply_zoom.parse_sidecar(form_json)
        return form_json

    if request.is_json:
        payload = request.get_json(silent=True)
        if payload is not None:
            raw_text = json.dumps(payload, indent=2)
            apply_zoom.parse_sidecar(raw_text)
            return raw_text

    return None


def _write_sidecar_file(video_dest: Path, sidecar_text: str) -> Path:
    sidecar_dest = Path(f"{video_dest}.zoom.json")
    fd, tmp_file = tempfile.mkstemp(dir=str(video_dest.parent), prefix="sidecar_", suffix=".json")
    os.close(fd)
    tmp_path = Path(tmp_file)
    try:
        tmp_path.write_text(sidecar_text, encoding="utf-8")
        os.chmod(tmp_path, 0o644)
        tmp_path.replace(sidecar_dest)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return sidecar_dest


def _unlink_if_exists(path_str: str | None, keep: Path | None = None) -> None:
    if not path_str:
        return
    p = Path(path_str)
    if keep is not None and p == keep:
        return
    if p.is_file():
        try:
            p.unlink()
        except OSError as exc:
            logger.warning("Could not delete old file %s: %s", p, exc)


def run_zoom_job(
    job_id: int,
    host_url: str | None = None,
    allow_rerun: bool = False,
) -> dict[str, Any] | None:
    """Execute zoom post-processing for a job and store the resulting URL in PostgreSQL."""
    job = claim_job(job_id, allow_rerun=allow_rerun)
    if job is None:
        return get_job(job_id)

    try:
        input_path = Path(job["input_path"])
        sidecar_path = Path(job["sidecar_path"]) if job.get("sidecar_path") else None
        output_path = apply_zoom.default_output_path(input_path)
        resolved_out, _ = apply_zoom.process_video(
            video_path=input_path,
            output_path=output_path,
            sidecar_path=sidecar_path,
            ffmpeg_path=os.environ.get("FFMPEG_PATH"),
        )
        base = (host_url or job.get("base_url") or "").rstrip("/")
        processed_url = f"{base}/media/{resolved_out.name}" if base else f"/media/{resolved_out.name}"
        completed = complete_job(job_id, str(resolved_out), processed_url)
        logger.info("Job %d completed -> %s", job_id, processed_url)
        return completed
    except Exception as exc:
        logger.exception("Job %d failed: %s", job_id, exc)
        return fail_job(job_id, str(exc))


def _spawn_zoom_job(job_id: int, host_url: str) -> None:
    thread = threading.Thread(
        target=run_zoom_job,
        args=(job_id, host_url, False),
        daemon=True,
        name=f"zoom-job-{job_id}",
    )
    thread.start()


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

    try:
        sidecar_text = _extract_sidecar_text_from_request()
    except Exception as exc:
        return jsonify({"error": f"Invalid zoom sidecar JSON: {exc}"}), 400

    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    host_url = request.host_url.rstrip("/")

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

            os.chmod(tmp_path, 0o644)
            tmp_path.replace(dest_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

        sidecar_dest: Path | None = None
        if sidecar_text is not None:
            sidecar_dest = _write_sidecar_file(dest_path, sidecar_text)
        else:
            _unlink_if_exists(f"{dest_path}.zoom.json")
            _unlink_if_exists(str(apply_zoom.default_output_path(dest_path)))

        if existing_record:
            _unlink_if_exists(existing_record.get("file_path"), keep=dest_path)
            _unlink_if_exists(existing_record.get("sidecar_path"), keep=sidecar_dest)
            _unlink_if_exists(existing_record.get("processed_file_path"))

        video_url = f"{host_url}/media/{dest_path.name}"
        initial_status = "pending" if sidecar_dest is not None else "none"
        video_id = save_video(
            company_name,
            str(dest_path),
            video_url,
            sidecar_path=str(sidecar_dest) if sidecar_dest else None,
            processing_status=initial_status,
        )
        logger.info("Uploaded video for '%s' -> %s", company_name, video_url)

        job_id: int | None = None
        processed_video_url: str | None = None
        status = initial_status

        if sidecar_dest is not None:
            job = create_job(
                video_id=video_id,
                company_name=company_name,
                input_path=str(dest_path),
                sidecar_path=str(sidecar_dest),
                base_url=host_url,
            )
            job_id = int(job["id"])
            auto_env = os.environ.get("AUTO_PROCESS_ZOOM", "1").strip().lower() not in ("0", "false", "no")
            auto_form = request.form.get("auto_process", "1").strip().lower() not in ("0", "false", "no")
            sync_mode = (
                request.args.get("sync") == "1"
                or request.form.get("sync", "").strip().lower() in ("1", "true", "yes")
            )
            if sync_mode:
                result = run_zoom_job(job_id, host_url=host_url)
                if result:
                    status = result["status"]
                    processed_video_url = result.get("processed_video_url")
            elif auto_env and auto_form:
                _spawn_zoom_job(job_id, host_url)

    return jsonify({
        "company_name": company_name,
        "video_url": video_url,
        "video_id": video_id,
        "job_id": job_id,
        "status": status,
        "processed_video_url": processed_video_url,
    }), 200


@app.route("/jobs", methods=["GET"])
def list_jobs():
    return jsonify({"jobs": get_all_jobs()}), 200


@app.route("/jobs/<int:job_id>", methods=["GET"])
def get_job_status(job_id: int):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": f"Job {job_id} not found"}), 404
    return jsonify(job), 200


@app.route("/jobs/<int:job_id>/process", methods=["POST"])
def process_job_endpoint(job_id: int):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": f"Job {job_id} not found"}), 404

    try:
        sidecar_text = _extract_sidecar_text_from_request()
    except Exception as exc:
        return jsonify({"error": f"Invalid zoom sidecar JSON: {exc}"}), 400

    if sidecar_text is not None:
        sidecar_dest = _write_sidecar_file(Path(job["input_path"]), sidecar_text)
        update_job_sidecar(job_id, str(sidecar_dest))

    host_url = request.host_url.rstrip("/")
    if request.args.get("async") == "1":
        if job["status"] != "processing":
            _spawn_zoom_job(job_id, host_url)
        return jsonify(get_job(job_id)), 202

    result = run_zoom_job(job_id, host_url=host_url, allow_rerun=True)
    if result is None:
        return jsonify({"error": f"Job {job_id} not found"}), 404
    if result["status"] == "processing":
        return jsonify(result), 202
    if result["status"] == "failed":
        return jsonify(result), 500
    return jsonify(result), 200


@app.route("/videos/<int:video_id>/process", methods=["POST"])
def process_video_endpoint(video_id: int):
    video = get_video_by_id(video_id)
    if not video:
        return jsonify({"error": f"Video {video_id} not found"}), 404

    try:
        sidecar_text = _extract_sidecar_text_from_request()
    except Exception as exc:
        return jsonify({"error": f"Invalid zoom sidecar JSON: {exc}"}), 400

    sidecar_path = video.get("sidecar_path")
    if sidecar_text is not None:
        sidecar_path = str(_write_sidecar_file(Path(video["file_path"]), sidecar_text))

    host_url = request.host_url.rstrip("/")
    job = create_job(
        video_id=video_id,
        company_name=video["company_name"],
        input_path=video["file_path"],
        sidecar_path=sidecar_path,
        base_url=host_url,
    )
    job_id = int(job["id"])

    if request.args.get("async") == "1":
        _spawn_zoom_job(job_id, host_url)
        return jsonify(job), 202

    result = run_zoom_job(job_id, host_url=host_url)
    if result and result["status"] == "failed":
        return jsonify(result), 500
    return jsonify(result), 200


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
    return jsonify({
        "company_name": video["company_name"],
        "video_url": video["video_url"],
        "processed_video_url": video.get("processed_video_url"),
        "processing_status": video.get("processing_status"),
        "job_id": video.get("job_id"),
    }), 200


@app.route("/media/<path:filename>", methods=["GET"])
def serve_media(filename: str):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/videos", methods=["GET"])
def list_videos():
    return jsonify({"videos": get_all_videos()}), 200


def _opt_csv_field(row: dict[str, str], key: str) -> str | None:
    val = row.get(key)
    if val is None:
        return None
    cleaned = val.strip()
    return cleaned if cleaned else None


@app.route("/upload_companies", methods=["GET"])
def upload_companies_form():
    return render_template("upload_companies.html"), 200


@app.route("/upload_companies", methods=["POST"])
def upload_companies():
    if "file" not in request.files:
        return jsonify({"error": "Missing 'file' in multipart/form-data"}), 400

    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "Empty or missing file"}), 400

    try:
        raw_text = file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify({"error": "CSV file must be UTF-8 encoded"}), 400

    if not raw_text.strip():
        return jsonify({"error": "Uploaded CSV file is empty"}), 400

    reader = csv.DictReader(io.StringIO(raw_text), delimiter=";", quotechar='"')
    if not reader.fieldnames:
        return jsonify({"error": "CSV header row is missing"}), 400

    normalized_headers = [h.strip() for h in reader.fieldnames if h is not None]
    if "Company Name" not in normalized_headers:
        return jsonify({"error": "CSV must contain a 'Company Name' column"}), 400

    parsed_rows: list[dict[str, Any]] = []
    for line_no, raw_row in enumerate(reader, start=2):
        row = {
            (k.strip() if k is not None else ""): (v if isinstance(v, str) else "")
            for k, v in raw_row.items()
        }
        if not any(v.strip() for v in row.values()):
            continue

        company_name = _opt_csv_field(row, "Company Name")
        if not company_name:
            return jsonify({"error": f"Row {line_no}: missing 'Company Name'"}), 400

        parsed_rows.append(
            {
                "company_name": company_name,
                "language": _opt_csv_field(row, "Language"),
                "country": _opt_csv_field(row, "Country"),
                "postcode": _opt_csv_field(row, "Postcode"),
                "city": _opt_csv_field(row, "City"),
                "street": _opt_csv_field(row, "Street"),
                "house_no": _opt_csv_field(row, "HouseNo"),
                "web": _opt_csv_field(row, "Web"),
            }
        )

    result = upsert_companies(parsed_rows)
    logger.info(
        "CSV import finished: imported=%d updated=%d total_rows=%d",
        result["imported"],
        result["updated"],
        result["total_rows"],
    )
    return jsonify(result), 200


@app.route("/companies", methods=["GET"])
def list_companies():
    status_param = request.args.get("status")
    status_filter: int | None = None
    if status_param is not None and status_param.strip() != "":
        try:
            status_filter = int(status_param.strip())
        except ValueError:
            return jsonify({"error": "Query parameter 'status' must be an integer"}), 400

    return jsonify(get_all_companies(status=status_filter)), 200


@app.route("/companies/<path:company_name>", methods=["GET"])
def get_company_endpoint(company_name: str):
    company = get_company(company_name)
    if not company:
        return jsonify({"error": f"Company '{company_name}' not found"}), 404
    return jsonify(company), 200


def main() -> None:
    init_db()
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", "8000"))
    logger.info("Starting web service on %s:%d", host, port)
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
