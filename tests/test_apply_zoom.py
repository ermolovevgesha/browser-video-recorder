from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from pathlib import Path

import pytest

import apply_zoom


def _eval_ffmpeg_expr(expr: str, **variables: float) -> float:
    py_expr = re.sub(r"\bif\(", "_if(", expr)
    ns = {
        "_if": lambda c, a, b: a if c else b,
        "between": lambda v, lo, hi: 1.0 if lo <= v <= hi else 0.0,
        "gte": lambda a, b: 1.0 if a >= b else 0.0,
        "clip": lambda v, lo, hi: min(max(v, lo), hi),
        "cos": math.cos,
        "PI": math.pi,
    }
    ns.update(variables)
    return float(eval(py_expr, {"__builtins__": {}}, ns))


def _extract_zoompan_params(vf: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for key in ("z", "x", "y"):
        m = re.search(rf"(?:zoompan=|:){key}='([^']+)'", vf)
        assert m, f"Missing {key}= expression in filter: {vf}"
        params[key] = m.group(1)
    return params


def test_zoom_geometry_and_easing():
    assert apply_zoom.compute_ramp_frames(0.5, 25.0) == 12
    assert apply_zoom.compute_ramp_frames(0.4, 25.0) == 10
    assert apply_zoom.compute_ramp_frames(0.01, 25.0) == 1
    assert apply_zoom.compute_ramp_frames(0.0, 25.0) == 0

    assert apply_zoom.scale_coordinates(640, 360, 1280, 720, 1280, 720) == (640.0, 360.0)
    assert apply_zoom.scale_coordinates(640, 360, 1280, 720, 1920, 1080) == (960.0, 540.0)
    assert apply_zoom.scale_coordinates(-20, 999, 1280, 720, 1280, 720) == (0.0, 720.0)

    assert apply_zoom.clamp_crop_origin(640, 360, 1280, 720, 2.0) == (320.0, 180.0)
    assert apply_zoom.clamp_crop_origin(50, 40, 1280, 720, 2.0) == (0.0, 0.0)
    assert apply_zoom.clamp_crop_origin(1250, 700, 1280, 720, 2.0) == (640.0, 360.0)
    assert apply_zoom.clamp_crop_origin(640, 360, 1280, 720, 1.0) == (0.0, 0.0)

    assert apply_zoom.ease_in_out(0.0) == pytest.approx(0.0)
    assert apply_zoom.ease_in_out(0.5) == pytest.approx(0.5)
    assert apply_zoom.ease_in_out(1.0) == pytest.approx(1.0)
    assert apply_zoom.ease_in_out(0.1) < 0.1
    assert apply_zoom.ease_in_out(0.9) > 0.9


def test_parse_sidecar_modern_and_legacy():
    modern = apply_zoom.parse_sidecar(
        {
            "viewport": {"width": 1280, "height": 720},
            "fps": 25,
            "zoom_factor": 2.0,
            "ramp_sec": 0.5,
            "events": [
                {"t": 8.0, "x": 640, "y": 360, "type": "out"},
                {"t": 3.0, "x": 640, "y": 360, "type": "in"},
            ],
        }
    )
    assert (modern.viewport_width, modern.viewport_height, modern.fps) == (1280, 720, 25.0)
    assert (modern.zoom_factor, modern.ramp_sec) == (2.0, 0.5)
    assert [(e.t, e.type) for e in modern.events] == [(3.0, "in"), (8.0, "out")]

    legacy_obj = apply_zoom.parse_sidecar(
        {
            "events": [
                {"t": 1.0, "x": 200, "y": 150},
                {"t": 4.0, "x": 200, "y": 150},
                {"t": 6.0, "x": 900, "y": 500},
            ]
        }
    )
    assert [e.type for e in legacy_obj.events] == ["in", "out", "in"]

    bare_list = apply_zoom.parse_sidecar(
        [{"t": 2.0, "x": 100, "y": 100}, {"t": 5.0, "x": 100, "y": 100}]
    )
    assert [(e.t, e.type) for e in bare_list.events] == [(2.0, "in"), (5.0, "out")]


def test_filter_segments_supersampling_and_expression_parity():
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
            ],
        }
    )
    segments = apply_zoom.build_segments(sidecar, 1280, 720, 25.0)
    assert len(segments) == 2
    assert (segments[0].in_frame, segments[0].hold_start_frame, segments[0].out_frame, segments[0].end_frame) == (
        50, 60, 100, 109
    )
    assert segments[1].in_frame == 150 and segments[1].end_frame is None

    vf = apply_zoom.build_zoompan_filter(sidecar, 1280, 720, 25.0)
    assert vf is not None
    assert vf.startswith("setpts=PTS-STARTPTS,fps=25,scale=2560:1440:flags=lanczos,zoompan=")
    assert ":d=1:s=1280x720:fps=25" in vf
    assert vf.endswith(",format=yuv420p")

    exprs = _extract_zoompan_params(vf)
    k = apply_zoom.DEFAULT_SUPERSAMPLE
    iw, ih = 1280 * k, 720 * k
    for frame in range(0, 220):
        z_ref, px_ref, py_ref = apply_zoom.eval_zoom_at_frame(segments, frame, 2.0, 10)
        z_expr = _eval_ffmpeg_expr(exprs["z"], on=frame)
        assert z_expr == pytest.approx(z_ref, abs=1e-6)
        if z_ref > 1.0 and px_ref is not None and py_ref is not None:
            x_ref, y_ref = apply_zoom.clamp_crop_origin(px_ref, py_ref, 1280, 720, z_ref)
            assert _eval_ffmpeg_expr(exprs["x"], on=frame, zoom=z_expr, iw=iw, ih=ih) == pytest.approx(x_ref * k, abs=0.05)
            assert _eval_ffmpeg_expr(exprs["y"], on=frame, zoom=z_expr, iw=iw, ih=ih) == pytest.approx(y_ref * k, abs=0.05)


def test_no_events_passthrough_and_dry_run_cli(tmp_path: Path):
    src = tmp_path / "sample.webm"
    src.write_bytes(b"FAKE_WEBM_BYTES")
    sidecar_path = Path(f"{src}.zoom.json")
    sidecar_path.write_text(json.dumps({"events": []}), encoding="utf-8")

    out = tmp_path / "out.webm"
    resolved, cmd = apply_zoom.process_video(src, output_path=out)
    assert cmd is None and resolved == out
    assert out.read_bytes() == b"FAKE_WEBM_BYTES"

    sidecar_path.unlink()
    out2 = tmp_path / "out2.webm"
    resolved2, cmd2 = apply_zoom.process_video(src, output_path=out2)
    assert cmd2 is None and resolved2 == out2
    assert out2.read_bytes() == b"FAKE_WEBM_BYTES"

    sidecar_path.write_text(
        json.dumps(
            {
                "viewport": {"width": 1280, "height": 720},
                "fps": 25,
                "zoom_factor": 2.0,
                "ramp_sec": 0.5,
                "events": [{"t": 1.0, "x": 640, "y": 360, "type": "in"}],
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
    assert "scale=2560:1440:flags=lanczos" in proc.stdout
    assert "zoompan=" in proc.stdout
    assert "libvpx-vp9" in proc.stdout
    assert not (tmp_path / "sample_zoomed.webm").exists()
