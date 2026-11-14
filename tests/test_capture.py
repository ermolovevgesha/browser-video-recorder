from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
from playwright.sync_api import Browser

import qscript


def test_zoom_recorder_sidecar_only_when_non_empty(tmp_path: Path):
    vid = tmp_path / "demo.webm"
    stale = Path(f"{vid}.zoom.json")
    stale.write_text("{}", encoding="utf-8")

    rec = qscript.ZoomEventRecorder()
    assert rec.write_sidecar(vid) is None
    assert not stale.exists()

    assert rec.toggle(1500, -10, timestamp=1.25) == "in"
    assert rec.toggle(640, 360, timestamp=3.5) == "out"
    sc = rec.write_sidecar(vid)
    assert sc == stale
    payload = json.loads(sc.read_text(encoding="utf-8"))
    assert payload["viewport"] == {"width": 1280, "height": 720}
    assert payload["events"] == [
        {"t": 1.25, "x": 1280, "y": 0, "type": "in"},
        {"t": 3.5, "x": 640, "y": 360, "type": "out"},
    ]


def test_playwright_f5_and_zoom_at_toggle(browser: Browser, tmp_path: Path):
    iframe_file = tmp_path / "frame.html"
    iframe_file.write_text("<html><body><p>Subframe</p></body></html>", encoding="utf-8")
    page_file = tmp_path / "index.html"
    page_file.write_text(
        f"<html><body><h1>Zoom Test</h1><iframe src='{iframe_file.name}'></iframe></body></html>",
        encoding="utf-8",
    )

    context = browser.new_context(viewport={"width": 1280, "height": 720})
    try:
        recorder = qscript.setup_zoom_capture(context)
        page = context.new_page()
        recorder.reset_start_time()
        page.goto(page_file.as_uri())

        assert page.frames[1].locator("#cursor-layer").count() == 0

        page.mouse.move(320, 240)
        page.keyboard.press("F5")
        assert page.locator(".zoom-indicator-ring--in").count() == 1
        assert recorder.is_zoomed

        assert qscript.ZoomAt(500, 300, page, state="in") is None
        assert recorder.is_zoomed

        time.sleep(0.12)
        page.mouse.move(330, 245)
        page.keyboard.press("F5")
        assert page.locator(".zoom-indicator-ring--out").count() == 1
        assert not recorder.is_zoomed

        assert page.evaluate("window.__zoomAt(800, 400)") == "in"
        assert page.evaluate("window.__zoomAt(800, 400)") == "out"
    finally:
        context.close()

    assert [(e["x"], e["y"], e["type"]) for e in recorder.events] == [
        (320, 240, "in"),
        (330, 245, "out"),
        (800, 400, "in"),
        (800, 400, "out"),
    ]


def test_sync_marker_aligns_delayed_video_start(browser: Browser, tmp_path: Path):
    ffmpeg_bin = qscript.find_ffmpeg()
    if not ffmpeg_bin:
        pytest.skip("Playwright/system ffmpeg binary is not available")

    page_file = tmp_path / "sync_page.html"
    page_file.write_text(
        "<html><body style='background:white;margin:0'><h1>Sync Test</h1></body></html>",
        encoding="utf-8",
    )

    context = browser.new_context(
        record_video_dir=str(tmp_path),
        record_video_size={"width": 1280, "height": 720},
        viewport={"width": 1280, "height": 720},
    )
    try:
        recorder = qscript.setup_zoom_capture(context)
        page = context.new_page()
        recorder.reset_start_time()
        page.goto(page_file.as_uri())

        assert recorder.start_epoch_ms is not None
        recorder.start_epoch_ms -= 2000.0
        page.wait_for_timeout(1000)

        page.evaluate(
            "() => { document.body.style.background = 'rgb(255, 0, 0)'; return window.__zoomAt(640, 360, 'in'); }"
        )
        page.wait_for_timeout(1000)
        page.evaluate("() => window.__zoomAt(640, 360, 'out')")
        page.wait_for_timeout(500)

        raw_video = Path(page.video.path())
    finally:
        context.close()

    offset = recorder.align_to_video(raw_video, ffmpeg_path=ffmpeg_bin)
    assert offset is not None and offset <= -1.8
    assert recorder.sync is not None and recorder.sync["aligned"] is True
    assert recorder.sync["markers"] >= 2

    frames_dir = tmp_path / "red_frames"
    frames_dir.mkdir()
    subprocess.run(
        [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(raw_video),
            "-r",
            str(recorder.fps),
            "-vf",
            "crop=4:4:600:600,scale=1:1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "image2",
            str(frames_dir / "%06d.png"),
        ],
        check=True,
    )
    red_t = next(
        idx / float(recorder.fps)
        for idx, name in enumerate(sorted(frames_dir.iterdir()))
        if qscript._png_rgb(name.read_bytes())[0] > 150 and qscript._png_rgb(name.read_bytes())[1] < 80
    )
    assert abs(recorder.events[0]["t"] - red_t) <= 0.10
