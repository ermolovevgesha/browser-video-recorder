from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

mimetypes.add_type("video/webm", ".webm")
TIMEOUT = (5, 300)


def UploadVideo(server_url: str, video_path: str, company_name: str) -> str:
    url = f"{server_url.rstrip('/')}/upload"
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    logger.info("Uploading '%s' for %s to %s", path.name, company_name, url)
    with path.open("rb") as f:
        files = {"file": (path.name, f, mime)}
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
    logger.info("Uploaded successfully: %s", video_url)
    return video_url


def GetVideoURL(server_url: str, company_name: str) -> str:
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
    video_url = payload.get("video_url")
    if not video_url:
        raise ValueError(f"Server response missing 'video_url': {payload}")
    return video_url
