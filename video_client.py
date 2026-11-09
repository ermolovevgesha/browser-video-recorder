from __future__ import annotations

import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


def UploadVideo(server_url: str, video_path: str, company_name: str) -> str:
    url = f"{server_url.rstrip('/')}/upload"
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    logger.info("Uploading '%s' for %s to %s", path.name, company_name, url)
    with path.open("rb") as f:
        files = {"file": (path.name, f)}
        data = {"company_name": company_name}
        response = requests.post(url, files=files, data=data, timeout=60)

    response.raise_for_status()
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
    response.raise_for_status()
    payload = response.json()
    video_url = payload.get("video_url")
    if not video_url:
        raise ValueError(f"Server response missing 'video_url': {payload}")
    return video_url
