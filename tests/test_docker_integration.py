from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
BASE_URL = os.environ.get("VIDEO_SERVICE_URL", "http://localhost:8000").rstrip("/")
TEST_COMPANY = "__pytest_docker_integration__"


def _require_docker_stack() -> None:
    if not shutil.which("docker"):
        pytest.skip("docker CLI is not available")
    try:
        resp = requests.get(f"{BASE_URL}/videos", timeout=2)
    except requests.RequestException:
        pytest.skip(f"Docker service is not running at {BASE_URL}")
    if resp.status_code != 200:
        pytest.skip(f"Docker service at {BASE_URL} returned HTTP {resp.status_code}")


def _cleanup_test_company() -> None:
    cleanup_py = (
        "import storage, pathlib; "
        f"rec = storage.get_video({TEST_COMPANY!r}); "
        "paths = [rec.get('file_path'), rec.get('sidecar_path'), rec.get('processed_file_path')] if rec else []; "
        "[pathlib.Path(p).unlink() for p in paths if p and pathlib.Path(p).is_file()]; "
        "conn = storage.get_db(); "
        f"cur = conn.cursor(); cur.execute('DELETE FROM videos WHERE company_name = %s', ({TEST_COMPANY!r},)); "
        "conn.commit(); conn.close()"
    )
    subprocess.run(
        ["docker", "compose", "exec", "-T", "web", "python3", "-c", cleanup_py],
        cwd=ROOT_DIR,
        capture_output=True,
        check=False,
    )


@pytest.mark.integration
def test_docker_container_has_ffmpeg():
    _require_docker_stack()
    ver = subprocess.run(
        ["docker", "compose", "exec", "-T", "web", "ffmpeg", "-version"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "ffmpeg version" in ver.stdout
    filters = subprocess.run(
        ["docker", "compose", "exec", "-T", "web", "ffmpeg", "-hide_banner", "-filters"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "zoompan" in filters.stdout


@pytest.mark.integration
def test_docker_upload_process_stores_result_and_serves_webm():
    _require_docker_stack()
    video_proc = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "web",
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1280x720:r=25:d=1.2",
            "-c:v",
            "libvpx",
            "-b:v",
            "500k",
            "-f",
            "webm",
            "pipe:1",
        ],
        cwd=ROOT_DIR,
        capture_output=True,
        check=True,
    )
    webm_bytes = video_proc.stdout
    assert webm_bytes.startswith(b"\x1a\x45\xdf\xa3")

    sidecar_bytes = json.dumps(
        {
            "viewport": {"width": 1280, "height": 720},
            "fps": 25,
            "zoom_factor": 2.0,
            "ramp_sec": 0.4,
            "events": [
                {"t": 0.2, "x": 640, "y": 360, "type": "in"},
                {"t": 0.9, "x": 640, "y": 360, "type": "out"},
            ],
        }
    ).encode("utf-8")

    try:
        upload_resp = requests.post(
            f"{BASE_URL}/upload?sync=1",
            data={"company_name": TEST_COMPANY},
            files={
                "file": ("integration.webm", webm_bytes, "video/webm"),
                "sidecar": ("integration.webm.zoom.json", sidecar_bytes, "application/json"),
            },
            timeout=60,
        )
        assert upload_resp.status_code == 200
        payload = upload_resp.json()
        assert payload["status"] == "done"
        processed_url = payload["processed_video_url"]
        assert processed_url and processed_url.endswith("_zoomed.webm")

        lookup = requests.get(f"{BASE_URL}/get_video_url", params={"company_name": TEST_COMPANY}, timeout=10)
        assert lookup.status_code == 200
        assert lookup.json()["processed_video_url"] == processed_url

        media = requests.get(processed_url, timeout=15)
        assert media.status_code == 200
        assert "webm" in media.headers.get("Content-Type", "").lower()
        assert media.content.startswith(b"\x1a\x45\xdf\xa3")
    finally:
        _cleanup_test_company()
