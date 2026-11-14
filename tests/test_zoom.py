from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

import apply_zoom
import qscript
import web_service


def test_compute_ramp_frames():
    assert apply_zoom.compute_ramp_frames(0.5, 25.0) == 12
    assert apply_zoom.compute_ramp_frames(0.4, 25.0) == 10
    assert apply_zoom.compute_ramp_frames(0.01, 25.0) == 1
    assert apply_zoom.compute_ramp_frames(0.0, 25.0) == 0


def test_scale_coordinates_one_to_one_and_scaled():
    assert apply_zoom.scale_coordinates(640, 360, 1280, 720, 1280, 720) == (640.0, 360.0)
    assert apply_zoom.scale_coordinates(640, 360, 1280, 720, 1920, 1080) == (960.0, 540.0)
    assert apply_zoom.scale_coordinates(-20, 999, 1280, 720, 1280, 720) == (0.0, 720.0)


def test_clamp_crop_origin_bounds():
    assert apply_zoom.clamp_crop_origin(640, 360, 1280, 720, 2.0) == (320.0, 180.0)
    assert apply_zoom.clamp_crop_origin(50, 40, 1280, 720, 2.0) == (0.0, 0.0)
    assert apply_zoom.clamp_crop_origin(1250, 700, 1280, 720, 2.0) == (640.0, 360.0)
    assert apply_zoom.clamp_crop_origin(640, 360, 1280, 720, 1.0) == (0.0, 0.0)


def test_easing_curve_properties():
    assert apply_zoom.ease_in_out(0.0) == pytest.approx(0.0)
    assert apply_zoom.ease_in_out(0.5) == pytest.approx(0.5)
    assert apply_zoom.ease_in_out(1.0) == pytest.approx(1.0)
    assert apply_zoom.ease_in_out(0.1) < 0.1
    assert apply_zoom.ease_in_out(0.9) > 0.9


def test_parse_sidecar_modern():
    data = {
        "viewport": {"width": 1280, "height": 720},
        "fps": 25,
        "zoom_factor": 2.0,
        "ramp_sec": 0.5,
        "events": [
            {"t": 8.0, "x": 640, "y": 360, "type": "out"},
            {"t": 3.0, "x": 640, "y": 360, "type": "in"},
        ],
    }
    sidecar = apply_zoom.parse_sidecar(data)
    assert sidecar.viewport_width == 1280
    assert sidecar.viewport_height == 720
    assert sidecar.fps == 25.0
    assert sidecar.zoom_factor == 2.0
    assert sidecar.ramp_sec == 0.5
    assert [(e.t, e.type) for e in sidecar.events] == [(3.0, "in"), (8.0, "out")]


def test_parse_sidecar_legacy_without_type():
    legacy_obj = {
        "events": [
            {"t": 1.0, "x": 200, "y": 150},
            {"t": 4.0, "x": 200, "y": 150},
            {"t": 6.0, "x": 900, "y": 500},
        ]
    }
    sc = apply_zoom.parse_sidecar(legacy_obj)
    assert [e.type for e in sc.events] == ["in", "out", "in"]
    assert sc.viewport_width == 1280
    assert sc.zoom_factor == 2.0

    bare_list = [
        {"t": 2.0, "x": 100, "y": 100},
        {"t": 5.0, "x": 100, "y": 100},
    ]
    sc_list = apply_zoom.parse_sidecar(bare_list)
    assert [(e.t, e.type) for e in sc_list.events] == [(2.0, "in"), (5.0, "out")]


def test_multiple_pairs_filter_and_curve():
    sidecar = apply_zoom.parse_sidecar(
        {
            "viewport": {"width": 1280, "height": 720},
            "fps": 25,
            "zoom_factor": 2.0,
            "ramp_sec": 0.4,
            "events": [
                {"t": 2.0, "x": 200, "y": 150, "type": "in"},
                {"t": 4.0, "x": 200, "y": 150, "type": "out"},
                {"t": 6.0, "x": 1000, "y": 550, "type": "in"},
                {"t": 8.0, "x": 1000, "y": 550, "type": "out"},
            ],
        }
    )
    segments = apply_zoom.build_segments(sidecar, 1280, 720, 25.0)
    assert len(segments) == 2
    assert (segments[0].in_frame, segments[0].hold_start_frame, segments[0].out_frame, segments[0].end_frame) == (
        50,
        60,
        100,
        109,
    )
    assert (segments[1].in_frame, segments[1].hold_start_frame, segments[1].out_frame, segments[1].end_frame) == (
        150,
        160,
        200,
        209,
    )

    assert apply_zoom.eval_zoom_at_frame(segments, 49, 2.0, 10)[0] == 1.0
    assert apply_zoom.eval_zoom_at_frame(segments, 50, 2.0, 10)[0] == 1.0
    mid_in, px, py = apply_zoom.eval_zoom_at_frame(segments, 55, 2.0, 10)
    assert mid_in == pytest.approx(1.5)
    assert (px, py) == (200.0, 150.0)
    assert apply_zoom.eval_zoom_at_frame(segments, 60, 2.0, 10)[0] == pytest.approx(2.0)
    assert apply_zoom.eval_zoom_at_frame(segments, 100, 2.0, 10)[0] == pytest.approx(2.0)
    mid_out, _, _ = apply_zoom.eval_zoom_at_frame(segments, 105, 2.0, 10)
    assert mid_out == pytest.approx(1.5)
    assert apply_zoom.eval_zoom_at_frame(segments, 110, 2.0, 10)[0] == 1.0

    z2, px2, py2 = apply_zoom.eval_zoom_at_frame(segments, 175, 2.0, 10)
    assert z2 == pytest.approx(2.0)
    assert (px2, py2) == (1000.0, 550.0)

    vf = apply_zoom.build_zoompan_filter(sidecar, 1280, 720, 25.0)
    assert vf is not None
    assert vf.startswith("zoompan=z='1+")
    assert "between(on,50,59)" in vf
    assert "between(on,60,100)" in vf
    assert "between(on,101,109)" in vf
    assert "between(on,150,159)" in vf
    assert "between(on,160,200)" in vf
    assert "clip(if(between(on,50,109),200,if(between(on,150,209),1000,iw/2))-iw/(2*zoom),0,iw-iw/zoom)" in vf
    assert ":d=1:s=1280x720:fps=25" in vf


def test_open_ended_zoom_holds_to_end():
    sidecar = apply_zoom.parse_sidecar(
        {
            "viewport": {"width": 1280, "height": 720},
            "fps": 25,
            "zoom_factor": 2.5,
            "ramp_sec": 0.4,
            "events": [
                {"t": 3.0, "x": 640, "y": 360, "type": "in"},
            ],
        }
    )
    segments = apply_zoom.build_segments(sidecar, 1280, 720, 25.0)
    assert len(segments) == 1
    assert segments[0].out_frame is None
    assert segments[0].end_frame is None

    assert apply_zoom.eval_zoom_at_frame(segments, 74, 2.5, 10)[0] == 1.0
    assert apply_zoom.eval_zoom_at_frame(segments, 85, 2.5, 10)[0] == pytest.approx(2.5)
    assert apply_zoom.eval_zoom_at_frame(segments, 5000, 2.5, 10)[0] == pytest.approx(2.5)

    vf = apply_zoom.build_zoompan_filter(sidecar, 1280, 720, 25.0)
    assert vf is not None
    assert "gte(on,85)" in vf
    assert "between(on,75,84)" in vf
    assert "clip(640-iw/(2*zoom),0,iw-iw/zoom)" in vf


def test_no_events_copies_unchanged(tmp_path: Path):
    src = tmp_path / "sample.webm"
    src.write_bytes(b"FAKE_WEBM_BYTES_12345")
    sidecar_path = tmp_path / "sample.webm.zoom.json"
    sidecar_path.write_text(
        json.dumps({"viewport": {"width": 1280, "height": 720}, "fps": 25, "events": []}),
        encoding="utf-8",
    )
    out = tmp_path / "out.webm"

    resolved, cmd = apply_zoom.process_video(src, output_path=out)
    assert cmd is None
    assert resolved == out
    assert out.read_bytes() == b"FAKE_WEBM_BYTES_12345"

    out2 = tmp_path / "out2.webm"
    sidecar_path.unlink()
    resolved2, cmd2 = apply_zoom.process_video(src, output_path=out2)
    assert cmd2 is None
    assert out2.read_bytes() == b"FAKE_WEBM_BYTES_12345"


def test_dry_run_cli_prints_command_without_encoding(tmp_path: Path):
    src = tmp_path / "demo.webm"
    src.write_bytes(b"FAKE")
    Path(f"{src}.zoom.json").write_text(
        json.dumps(
            {
                "viewport": {"width": 1280, "height": 720},
                "fps": 25,
                "zoom_factor": 2.0,
                "ramp_sec": 0.5,
                "events": [
                    {"t": 1.0, "x": 640, "y": 360, "type": "in"},
                    {"t": 3.0, "x": 640, "y": 360, "type": "out"},
                ],
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, "apply_zoom.py", "--video", str(src), "--dry-run"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "zoompan=" in proc.stdout
    assert "libvpx-vp9" in proc.stdout
    assert not (tmp_path / "demo_zoomed.webm").exists()


def test_zoom_event_recorder_only_writes_when_non_empty(tmp_path: Path):
    rec = qscript.ZoomEventRecorder()
    vid = tmp_path / "normal.webm"
    assert rec.write_sidecar(vid) is None
    assert not Path(f"{vid}.zoom.json").exists()

    assert rec.toggle(1500, -10, timestamp=1.25) == "in"
    assert rec.toggle(640, 360, timestamp=3.5) == "out"
    sc = rec.write_sidecar(vid)
    assert sc == Path(f"{vid}.zoom.json")
    payload = json.loads(sc.read_text(encoding="utf-8"))
    assert payload["viewport"] == {"width": 1280, "height": 720}
    assert payload["events"] == [
        {"t": 1.25, "x": 1280, "y": 0, "type": "in"},
        {"t": 3.5, "x": 640, "y": 360, "type": "out"},
    ]


def test_playwright_f5_and_zoom_at_hook(tmp_path: Path):
    import time

    iframe_file = tmp_path / "frame.html"
    iframe_file.write_text("<html><body><p>Subframe</p></body></html>", encoding="utf-8")
    page_file = tmp_path / "index.html"
    page_file.write_text(
        f"<html><body><h1>Zoom Test</h1><iframe src='{iframe_file.name}'></iframe></body></html>",
        encoding="utf-8",
    )

    with sync_playwright() as pw:
        launch_args = {"headless": True, "args": ["--no-sandbox"]}
        for candidate in ("/run/current-system/sw/bin/chromium", "/usr/bin/chromium", "/usr/bin/google-chrome"):
            if Path(candidate).exists():
                launch_args["executable_path"] = candidate
                break
        browser = pw.chromium.launch(**launch_args)
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        recorder = qscript.setup_zoom_capture(context)
        page = context.new_page()
        recorder.reset_start_time()
        page.goto(page_file.as_uri())

        assert page.frames[1].locator("#cursor-layer").count() == 0

        page.mouse.move(320, 240)
        page.keyboard.press("F5")
        assert page.locator(".zoom-indicator-ring--in").count() == 1

        page.keyboard.press("F5")
        assert len(recorder.events) == 1

        time.sleep(0.12)
        page.mouse.move(330, 245)
        page.keyboard.press("F5")
        assert page.locator(".zoom-indicator-ring--out").count() == 1

        assert page.evaluate("window.__zoomAt(800, 400)") == "in"
        assert page.evaluate("window.__zoomAt(800, 400)") == "out"

        context.close()
        browser.close()

    assert [(e["x"], e["y"], e["type"]) for e in recorder.events] == [
        (320, 240, "in"),
        (330, 245, "out"),
        (800, 400, "in"),
        (800, 400, "out"),
    ]


def test_web_service_upload_and_process_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import io

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(web_service, "UPLOAD_FOLDER", uploads)
    web_service.app.config["UPLOAD_FOLDER"] = str(uploads)

    fake_videos: dict[str, dict] = {}
    fake_jobs: dict[int, dict] = {}
    seq = {"vid": 0, "job": 0}

    def _get_company_by_path(fp: str):
        for v in fake_videos.values():
            if v["file_path"] == fp:
                return v["company_name"]
        return None

    def _get_video(name: str):
        return fake_videos.get(name)

    def _save_video(company_name, file_path, video_url, sidecar_path=None, processing_status="none"):
        if company_name not in fake_videos:
            seq["vid"] += 1
            vid = seq["vid"]
        else:
            vid = fake_videos[company_name]["id"]
        fake_videos[company_name] = {
            "id": vid,
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
        return vid

    def _create_job(video_id, company_name, input_path, sidecar_path=None, base_url=None):
        seq["job"] += 1
        jid = seq["job"]
        job = {
            "id": jid,
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
        fake_jobs[jid] = job
        fake_videos[company_name]["job_id"] = jid
        return dict(job)

    def _get_job(jid: int):
        return dict(fake_jobs[jid]) if jid in fake_jobs else None

    def _claim_job(jid: int, allow_rerun: bool = False):
        job = fake_jobs.get(jid)
        if not job:
            return None
        job["status"] = "processing"
        return dict(job)

    def _complete_job(jid: int, output_path: str, processed_video_url: str):
        job = fake_jobs[jid]
        job["status"] = "done"
        job["output_path"] = output_path
        job["processed_video_url"] = processed_video_url
        v = fake_videos[job["company_name"]]
        v["processed_file_path"] = output_path
        v["processed_video_url"] = processed_video_url
        v["processing_status"] = "done"
        return dict(job)

    def _fake_process_video(video_path, output_path=None, sidecar_path=None, ffmpeg_path=None, dry_run=False):
        out = Path(output_path) if output_path else apply_zoom.default_output_path(Path(video_path))
        out.write_bytes(b"ZOOMED_WEBM")
        return out, ["ffmpeg"]

    monkeypatch.setattr(web_service, "get_company_by_path", _get_company_by_path)
    monkeypatch.setattr(web_service, "get_video", _get_video)
    monkeypatch.setattr(web_service, "get_all_videos", lambda: list(fake_videos.values()))
    monkeypatch.setattr(web_service, "save_video", _save_video)
    monkeypatch.setattr(web_service, "create_job", _create_job)
    monkeypatch.setattr(web_service, "get_job", _get_job)
    monkeypatch.setattr(web_service, "claim_job", _claim_job)
    monkeypatch.setattr(web_service, "complete_job", _complete_job)
    monkeypatch.setattr(apply_zoom, "process_video", _fake_process_video)

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
    up_data = resp.get_json()
    assert up_data["job_id"] == 1
    assert up_data["status"] == "pending"

    proc_resp = client.post("/jobs/1/process")
    assert proc_resp.status_code == 200
    proc_data = proc_resp.get_json()
    assert proc_data["status"] == "done"
    assert proc_data["processed_video_url"].endswith("/media/Acme_Test_zoomed.webm")

    lookup = client.get("/get_video_url?company_name=Acme+Test")
    assert lookup.status_code == 200
    assert lookup.get_json()["processed_video_url"].endswith("/media/Acme_Test_zoomed.webm")

    html = client.get("/").get_data(as_text=True)
    assert "/media/Acme_Test_zoomed.webm" in html
    assert "Original video" not in html
    assert "Zoomed video" not in html


def test_user_activity_tracker_cooldown():
    assert qscript.USER_MOUSE_COOLDOWN_SEC == 2.0
    tracker = qscript.UserActivityTracker(cooldown_sec=2.0, enabled=True)
    assert not tracker.is_paused(now=10.0)
    assert tracker.remaining_cooldown(now=10.0) == 0.0

    tracker.note_activity(now=10.0)
    assert tracker.is_paused(now=10.5)
    assert tracker.remaining_cooldown(now=10.5) == pytest.approx(1.5)
    assert not tracker.is_paused(now=12.0)

    disabled = qscript.UserActivityTracker(cooldown_sec=2.0, enabled=False)
    disabled.note_activity(now=10.0)
    assert not disabled.is_paused(now=10.1)


def test_capture_video_cli_headed_flag():
    proc = subprocess.run(
        [sys.executable, "capture_video.py", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--headed" in proc.stdout


def test_script_distinguishes_user_mouse_pauses_and_resumes_from_user_pos(tmp_path: Path):
    import time

    page_file = tmp_path / "headed_sim.html"
    page_file.write_text("<html><body><h1>Headed Mouse Test</h1></body></html>", encoding="utf-8")

    with sync_playwright() as pw:
        launch_args = {"headless": True, "args": ["--no-sandbox"]}
        for candidate in ("/run/current-system/sw/bin/chromium", "/usr/bin/chromium", "/usr/bin/google-chrome"):
            if Path(candidate).exists():
                launch_args["executable_path"] = candidate
                break
        browser = pw.chromium.launch(**launch_args)
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        recorder = qscript.setup_zoom_capture(context, pause_on_user_mouse=True, cooldown_sec=0.35)
        tracker = recorder.activity_tracker
        assert tracker is not None

        page = context.new_page()
        qscript.bind_activity_tracker(page, tracker)
        recorder.reset_start_time()
        page.goto(page_file.as_uri())

        x, y = qscript.move_mouse(page, 100.0, 100.0, 240.0, 220.0)
        assert (x, y) == (240.0, 220.0)
        assert tracker.last_user_activity is None

        assert page.evaluate("window.__zoomAt(240, 220)") == "in"
        assert tracker.last_user_activity is None
        assert page.evaluate("window.__zoomAt(240, 220)") == "out"

        page.evaluate("window.__moves = []; window.addEventListener('mousemove', e => window.__moves.push([Math.round(e.clientX), Math.round(e.clientY)]), true);")
        page.mouse.move(900, 140)
        page.wait_for_timeout(40)
        assert tracker.is_paused()

        page.evaluate("window.__moves = []")
        t0 = time.monotonic()
        x, y = qscript.move_mouse(page, 240.0, 220.0, 700.0, 450.0)
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.28
        assert (x, y) == (700.0, 450.0)

        moves = page.evaluate("window.__moves")
        assert len(moves) > 0
        assert moves[0][0] > 780

        page.wait_for_timeout(400)
        assert not tracker.is_paused()
        page.keyboard.press("F5")
        page.wait_for_timeout(40)
        assert tracker.is_paused()
        assert recorder.events[-1]["type"] == "in"

        context.close()
        browser.close()


def test_script_zoom_state_does_not_invert_after_single_f5(tmp_path: Path):
    rec = qscript.ZoomEventRecorder()
    assert rec.toggle(100, 100, timestamp=1.0) == "in"
    assert rec.toggle(200, 200, timestamp=2.0, target_state="in") is None
    assert rec.toggle(200, 200, timestamp=3.0, target_state="out") == "out"
    assert rec.toggle(200, 200, timestamp=4.0, target_state="out") is None
    assert [(e["t"], e["type"]) for e in rec.events] == [(1.0, "in"), (3.0, "out")]

    page_file = tmp_path / "sync_zoom.html"
    page_file.write_text("<html><body><h1>Zoom Sync Test</h1></body></html>", encoding="utf-8")

    with sync_playwright() as pw:
        launch_args = {"headless": True, "args": ["--no-sandbox"]}
        for candidate in ("/run/current-system/sw/bin/chromium", "/usr/bin/chromium", "/usr/bin/google-chrome"):
            if Path(candidate).exists():
                launch_args["executable_path"] = candidate
                break
        browser = pw.chromium.launch(**launch_args)
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        recorder = qscript.setup_zoom_capture(context, pause_on_user_mouse=True, cooldown_sec=0.2)
        page = context.new_page()
        qscript.bind_activity_tracker(page, recorder.activity_tracker)
        recorder.reset_start_time()
        page.goto(page_file.as_uri())

        page.mouse.move(300, 200)
        page.keyboard.press("F5")
        assert recorder.is_zoomed
        assert qscript.ZoomAt(500, 300, page, state="in") is None
        assert qscript.ZoomAt(500, 300, page, state="out") == "out"
        assert not recorder.is_zoomed

        assert qscript.ZoomAt(400, 250, page, state="in") == "in"
        page.wait_for_timeout(120)
        page.keyboard.press("F5")
        assert not recorder.is_zoomed
        assert qscript.ZoomAt(400, 250, page, state="out") is None
        assert not recorder.is_zoomed

        context.close()
        browser.close()

    assert [(e["x"], e["y"], e["type"]) for e in recorder.events] == [
        (300, 200, "in"),
        (500, 300, "out"),
        (400, 250, "in"),
        (300, 200, "out"),
    ]
