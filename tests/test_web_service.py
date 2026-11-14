from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

import apply_zoom
import web_service


class FakeVideoStore:
    def __init__(self) -> None:
        self.videos: dict[str, dict] = {}
        self.jobs: dict[int, dict] = {}
        self.video_seq = 0
        self.job_seq = 0

    def get_company_by_path(self, file_path: str) -> str | None:
        for video in self.videos.values():
            if video["file_path"] == file_path:
                return video["company_name"]
        return None

    def get_video(self, company_name: str) -> dict | None:
        return self.videos.get(company_name)

    def get_all_videos(self) -> list[dict]:
        return list(self.videos.values())

    def save_video(
        self,
        company_name: str,
        file_path: str,
        video_url: str,
        sidecar_path: str | None = None,
        processing_status: str = "none",
    ) -> int:
        if company_name in self.videos:
            video_id = self.videos[company_name]["id"]
        else:
            self.video_seq += 1
            video_id = self.video_seq
        self.videos[company_name] = {
            "id": video_id,
            "company_name": company_name,
            "file_path": file_path,
            "video_url": video_url,
            "sidecar_path": sidecar_path,
            "processed_file_path": None,
            "processed_video_url": None,
            "processing_status": processing_status,
            "job_id": None,
            "updated_at": "2026-11-14T19:00:00Z",
        }
        return video_id

    def create_job(
        self,
        video_id: int,
        company_name: str,
        input_path: str,
        sidecar_path: str | None = None,
        base_url: str | None = None,
    ) -> dict:
        self.job_seq += 1
        job_id = self.job_seq
        job = {
            "id": job_id,
            "video_id": video_id,
            "company_name": company_name,
            "status": "pending",
            "input_path": input_path,
            "sidecar_path": sidecar_path,
            "output_path": None,
            "processed_video_url": None,
            "base_url": base_url,
            "error_message": None,
        }
        self.jobs[job_id] = job
        self.videos[company_name]["job_id"] = job_id
        return dict(job)

    def get_job(self, job_id: int) -> dict | None:
        return dict(self.jobs[job_id]) if job_id in self.jobs else None

    def claim_job(self, job_id: int, allow_rerun: bool = False) -> dict | None:
        job = self.jobs.get(job_id)
        if not job:
            return None
        job["status"] = "processing"
        return dict(job)

    def complete_job(self, job_id: int, output_path: str, processed_video_url: str) -> dict:
        job = self.jobs[job_id]
        job["status"] = "done"
        job["output_path"] = output_path
        job["processed_video_url"] = processed_video_url
        video = self.videos[job["company_name"]]
        video["processed_file_path"] = output_path
        video["processed_video_url"] = processed_video_url
        video["processing_status"] = "done"
        return dict(job)


def fake_process_video(
    video_path,
    output_path=None,
    sidecar_path=None,
    ffmpeg_path=None,
    dry_run: bool = False,
):
    out = Path(output_path) if output_path else apply_zoom.default_output_path(Path(video_path))
    out.write_bytes(b"ZOOMED_WEBM")
    return out, ["ffmpeg"]


def test_web_service_upload_process_and_gallery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(web_service, "UPLOAD_FOLDER", uploads)
    web_service.app.config["UPLOAD_FOLDER"] = str(uploads)

    store = FakeVideoStore()
    for method_name in (
        "get_company_by_path",
        "get_video",
        "get_all_videos",
        "save_video",
        "create_job",
        "get_job",
        "claim_job",
        "complete_job",
    ):
        monkeypatch.setattr(web_service, method_name, getattr(store, method_name))
    monkeypatch.setattr(apply_zoom, "process_video", fake_process_video)

    client = web_service.app.test_client()
    sidecar_bytes = json.dumps(
        {
            "viewport": {"width": 1280, "height": 720},
            "fps": 25,
            "zoom_factor": 2.0,
            "ramp_sec": 0.5,
            "events": [{"t": 1.0, "x": 640, "y": 360, "type": "in"}],
        }
    ).encode("utf-8")

    resp = client.post(
        "/upload",
        data={
            "company_name": "Acme Test",
            "auto_process": "0",
            "file": (io.BytesIO(b"RAW_WEBM"), "acme.webm"),
            "sidecar": (io.BytesIO(sidecar_bytes), "acme.webm.zoom.json"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "pending"

    proc_resp = client.post("/jobs/1/process")
    assert proc_resp.status_code == 200
    assert proc_resp.get_json()["status"] == "done"
    assert proc_resp.get_json()["processed_video_url"].endswith("/media/Acme_Test_zoomed.webm")

    lookup = client.get("/get_video_url?company_name=Acme+Test")
    assert lookup.status_code == 200
    assert lookup.get_json()["processed_video_url"].endswith("/media/Acme_Test_zoomed.webm")

    html = client.get("/").get_data(as_text=True)
    assert "/media/Acme_Test_zoomed.webm" in html
    assert "Original video" not in html
    assert "Zoomed video" not in html
