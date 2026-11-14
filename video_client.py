from __future__ import annotations

import logging
import mimetypes
import time
from contextlib import ExitStack
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

mimetypes.add_type("video/webm", ".webm")
TIMEOUT = (5, 300)
JOB_POLL_TIMEOUT_SEC = 180.0
JOB_POLL_INTERVAL_SEC = 1.0


def _find_sidecar(video_path: Path) -> Path | None:
    primary = Path(f"{video_path}.zoom.json")
    if primary.is_file():
        return primary
    fallback = video_path.with_suffix(".zoom.json")
    if fallback.is_file():
        return fallback
    return None


def _wait_for_job(server_url: str, job_id: int, timeout_sec: float = JOB_POLL_TIMEOUT_SEC) -> str | None:
    job_url = f"{server_url.rstrip('/')}/jobs/{job_id}"
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            resp = requests.get(job_url, timeout=15)
            if resp.ok:
                job = resp.json()
                status = job.get("status")
                if status == "done":
                    return job.get("processed_video_url")
                if status == "failed":
                    logger.warning("Zoom job %d failed: %s", job_id, job.get("error_message"))
                    return None
        except Exception as exc:
            logger.debug("Polling job %d error: %s", job_id, exc)
        time.sleep(JOB_POLL_INTERVAL_SEC)
    logger.warning("Timed out waiting for zoom job %d", job_id)
    return None


def UploadVideo(server_url: str, video_path: str, company_name: str) -> str:
    """Uploads a video file (and optional .zoom.json sidecar) to the server and returns its URL."""
    base = server_url.rstrip("/")
    url = f"{base}/upload"
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    sidecar = _find_sidecar(path)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    logger.info("Uploading '%s' for %s to %s", path.name, company_name, url)

    with ExitStack() as stack:
        vf = stack.enter_context(path.open("rb"))
        files: dict[str, tuple[str, object, str]] = {"file": (path.name, vf, mime)}
        if sidecar is not None:
            sf = stack.enter_context(sidecar.open("rb"))
            files["sidecar"] = (sidecar.name, sf, "application/json")
            logger.info("Attaching zoom sidecar: %s", sidecar.name)
        data = {"company_name": company_name}
        response = requests.post(url, files=files, data=data, timeout=TIMEOUT)

    if not response.ok:
        try:
            err = response.json().get("error", response.text)
        except Exception:
            err = response.text
        raise RuntimeError(f"Upload failed (HTTP {response.status_code}): {err}")

    payload = response.json()
    video_url = payload.get("video_url")
    if not video_url:
        raise ValueError(f"Server response missing 'video_url': {payload}")

    processed_url = payload.get("processed_video_url")
    job_id = payload.get("job_id")
    if not processed_url and job_id is not None:
        logger.info("Waiting for zoom post-processing job %s...", job_id)
        processed_url = _wait_for_job(base, int(job_id))

    final_url = processed_url or video_url
    logger.info("Uploaded successfully: %s", final_url)
    return final_url


def GetVideoURL(server_url: str, company_name: str) -> str:
    """Returns the video URL (processed if available) for the given company name."""
    url = f"{server_url.rstrip('/')}/get_video_url"
    logger.info("Requesting video URL for %s from %s", company_name, url)
    response = requests.get(url, params={"company_name": company_name}, timeout=30)
    if not response.ok:
        try:
            err = response.json().get("error", response.text)
        except Exception:
            err = response.text
        if response.status_code == 404:
            raise LookupError(f"Video not found for company '{company_name}': {err}")
        raise RuntimeError(f"Request failed (HTTP {response.status_code}): {err}")

    payload = response.json()
    video_url = payload.get("processed_video_url") or payload.get("video_url")
    if not video_url:
        raise ValueError(f"Server response missing 'video_url': {payload}")
    return video_url
