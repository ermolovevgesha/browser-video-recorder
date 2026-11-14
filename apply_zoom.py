from __future__ import annotations

import argparse
import json
import logging
import math
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_VIEWPORT_WIDTH = 1280
DEFAULT_VIEWPORT_HEIGHT = 720
DEFAULT_FPS = 25.0
DEFAULT_ZOOM_FACTOR = 2.0
DEFAULT_RAMP_SEC = 0.5


@dataclass(frozen=True)
class ZoomEvent:
    t: float
    x: float
    y: float
    type: str


@dataclass
class ZoomSegment:
    t_in: float
    x: float
    y: float
    t_out: float | None
    in_frame: int = 0
    hold_start_frame: int = 0
    out_frame: int | None = None
    end_frame: int | None = None


@dataclass(frozen=True)
class ZoomSidecar:
    viewport_width: int
    viewport_height: int
    fps: float
    zoom_factor: float
    ramp_sec: float
    events: list[ZoomEvent]


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: float
    duration: float | None = None


def _fmt_num(val: float) -> str:
    if float(val).is_integer():
        return str(int(val))
    return f"{val:.6f}".rstrip("0").rstrip(".")


def find_sidecar_path(video_path: Path, explicit_sidecar: Path | None = None) -> Path | None:
    if explicit_sidecar is not None:
        if not explicit_sidecar.is_file():
            raise FileNotFoundError(f"Sidecar file not found: {explicit_sidecar}")
        return explicit_sidecar

    primary = Path(f"{video_path}.zoom.json")
    if primary.is_file():
        return primary

    fallback = video_path.with_suffix(".zoom.json")
    if fallback.is_file():
        return fallback

    return None


def parse_sidecar(source: Path | str | dict[str, Any] | list[Any]) -> ZoomSidecar:
    if isinstance(source, Path):
        raw = json.loads(source.read_text(encoding="utf-8"))
    elif isinstance(source, str):
        raw = json.loads(source)
    else:
        raw = source

    if isinstance(raw, list):
        data: dict[str, Any] = {"events": raw}
    elif isinstance(raw, dict):
        data = raw
    else:
        raise ValueError("Sidecar JSON must be an object or list of events")

    viewport = data.get("viewport") or {}
    if not isinstance(viewport, dict):
        raise ValueError("Sidecar 'viewport' must be an object")

    viewport_width = int(viewport.get("width", DEFAULT_VIEWPORT_WIDTH))
    viewport_height = int(viewport.get("height", DEFAULT_VIEWPORT_HEIGHT))
    if viewport_width <= 0 or viewport_height <= 0:
        raise ValueError("Sidecar viewport dimensions must be positive")

    fps = float(data.get("fps", DEFAULT_FPS))
    if fps <= 0:
        raise ValueError("Sidecar 'fps' must be positive")

    zoom_factor = float(data.get("zoom_factor", DEFAULT_ZOOM_FACTOR))
    if zoom_factor < 1.0:
        raise ValueError("Sidecar 'zoom_factor' must be >= 1.0")

    ramp_sec = float(data.get("ramp_sec", DEFAULT_RAMP_SEC))
    if ramp_sec < 0:
        raise ValueError("Sidecar 'ramp_sec' must be >= 0")

    raw_events = data.get("events")
    if raw_events is None:
        raw_events = []
    if not isinstance(raw_events, list):
        raise ValueError("Sidecar 'events' must be a list")

    indexed_rows: list[tuple[float, int, dict[str, Any]]] = []
    for idx, item in enumerate(raw_events):
        if not isinstance(item, dict):
            raise ValueError(f"Event at index {idx} must be an object")
        if "t" not in item or "x" not in item or "y" not in item:
            raise ValueError(f"Event at index {idx} missing required keys (t, x, y)")
        t_val = max(0.0, float(item["t"]))
        indexed_rows.append((t_val, idx, item))

    indexed_rows.sort(key=lambda pair: (pair[0], pair[1]))

    events: list[ZoomEvent] = []
    inferred_active = False
    for t_val, _idx, item in indexed_rows:
        x_val = float(item["x"])
        y_val = float(item["y"])
        raw_type = item.get("type")
        if raw_type is None or str(raw_type).strip() == "":
            inferred_active = not inferred_active
            ev_type = "in" if inferred_active else "out"
        else:
            ev_type = str(raw_type).strip().lower()
            if ev_type not in ("in", "out"):
                raise ValueError(f"Invalid event type {raw_type!r}; expected 'in' or 'out'")
            inferred_active = ev_type == "in"

        events.append(ZoomEvent(t=t_val, x=x_val, y=y_val, type=ev_type))

    return ZoomSidecar(
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        fps=fps,
        zoom_factor=zoom_factor,
        ramp_sec=ramp_sec,
        events=events,
    )


def scale_coordinates(
    x: float,
    y: float,
    src_width: int,
    src_height: int,
    dst_width: int,
    dst_height: int,
) -> tuple[float, float]:
    if src_width > 0 and src_height > 0:
        px = x * (dst_width / src_width)
        py = y * (dst_height / src_height)
    else:
        px, py = x, y

    px = max(0.0, min(px, float(dst_width)))
    py = max(0.0, min(py, float(dst_height)))
    return px, py


def clamp_crop_origin(
    px: float,
    py: float,
    width: int,
    height: int,
    zoom: float,
) -> tuple[float, float]:
    if zoom <= 1.0:
        return 0.0, 0.0

    crop_w = width / zoom
    crop_h = height / zoom
    x = max(0.0, min(px - crop_w / 2.0, width - crop_w))
    y = max(0.0, min(py - crop_h / 2.0, height - crop_h))
    return x, y


def ease_in_out(progress: float) -> float:
    p = max(0.0, min(1.0, progress))
    return 0.5 * (1.0 - math.cos(math.pi * p))


def compute_ramp_frames(ramp_sec: float, fps: float) -> int:
    if ramp_sec <= 0.0 or fps <= 0.0:
        return 0
    return max(1, round(ramp_sec * fps))


def build_segments(
    sidecar: ZoomSidecar,
    video_width: int,
    video_height: int,
    fps: float | None = None,
) -> list[ZoomSegment]:
    eff_fps = float(fps if fps is not None else sidecar.fps)
    ramp_frames = compute_ramp_frames(sidecar.ramp_sec, eff_fps)

    paired: list[tuple[ZoomEvent, ZoomEvent | None]] = []
    open_in: ZoomEvent | None = None

    for ev in sidecar.events:
        if ev.type == "in":
            if open_in is None:
                open_in = ev
        elif ev.type == "out":
            if open_in is not None:
                paired.append((open_in, ev))
                open_in = None

    if open_in is not None:
        paired.append((open_in, None))

    segments: list[ZoomSegment] = []
    for ev_in, ev_out in paired:
        px, py = scale_coordinates(
            ev_in.x,
            ev_in.y,
            sidecar.viewport_width,
            sidecar.viewport_height,
            video_width,
            video_height,
        )
        in_frame = max(0, round(ev_in.t * eff_fps))
        hold_start = in_frame + ramp_frames

        if ev_out is None:
            segments.append(
                ZoomSegment(
                    t_in=ev_in.t,
                    x=px,
                    y=py,
                    t_out=None,
                    in_frame=in_frame,
                    hold_start_frame=hold_start,
                    out_frame=None,
                    end_frame=None,
                )
            )
            continue

        out_frame = max(in_frame, round(ev_out.t * eff_fps))
        if ramp_frames == 0:
            end_frame = out_frame
        elif out_frame >= hold_start:
            end_frame = out_frame + ramp_frames - 1
        else:
            actual_ramp = out_frame - in_frame
            if actual_ramp <= 0:
                continue
            end_frame = out_frame + actual_ramp - 1

        segments.append(
            ZoomSegment(
                t_in=ev_in.t,
                x=px,
                y=py,
                t_out=ev_out.t,
                in_frame=in_frame,
                hold_start_frame=hold_start,
                out_frame=out_frame,
                end_frame=end_frame,
            )
        )

    for i in range(len(segments) - 1):
        curr = segments[i]
        nxt = segments[i + 1]
        if curr.end_frame is None or curr.end_frame >= nxt.in_frame:
            curr.end_frame = max(curr.in_frame, nxt.in_frame - 1)
            if curr.out_frame is None or curr.out_frame >= nxt.in_frame:
                curr.out_frame = max(curr.in_frame, nxt.in_frame - 1)

    return segments


def eval_zoom_at_frame(
    segments: list[ZoomSegment],
    frame_idx: int,
    zoom_factor: float,
    ramp_frames: int,
) -> tuple[float, float | None, float | None]:
    if zoom_factor <= 1.0 or not segments:
        return 1.0, None, None

    delta = zoom_factor - 1.0
    for seg in segments:
        if frame_idx < seg.in_frame:
            continue
        if seg.end_frame is not None and frame_idx > seg.end_frame:
            continue

        if seg.out_frame is None:
            if ramp_frames > 0 and frame_idx < seg.hold_start_frame:
                p = (frame_idx - seg.in_frame) / ramp_frames
                return 1.0 + delta * ease_in_out(p), seg.x, seg.y
            return zoom_factor, seg.x, seg.y

        if ramp_frames == 0:
            return zoom_factor, seg.x, seg.y

        if seg.out_frame >= seg.hold_start_frame:
            if frame_idx < seg.hold_start_frame:
                p = (frame_idx - seg.in_frame) / ramp_frames
                return 1.0 + delta * ease_in_out(p), seg.x, seg.y
            if frame_idx <= seg.out_frame:
                return zoom_factor, seg.x, seg.y
            p = (frame_idx - seg.out_frame) / ramp_frames
            return 1.0 + delta * (1.0 - ease_in_out(p)), seg.x, seg.y

        actual_in = max(1, seg.out_frame - seg.in_frame)
        if frame_idx < seg.out_frame:
            p = (frame_idx - seg.in_frame) / ramp_frames
            return 1.0 + delta * ease_in_out(p), seg.x, seg.y
        peak = delta * ease_in_out(actual_in / ramp_frames)
        out_span = max(1, (seg.end_frame or seg.out_frame) - seg.out_frame + 1)
        p = (frame_idx - seg.out_frame) / out_span
        return 1.0 + peak * (1.0 - ease_in_out(p)), seg.x, seg.y

    return 1.0, None, None


def _build_segment_delta_expr(
    seg: ZoomSegment,
    zoom_factor: float,
    ramp_frames: int,
) -> str:
    delta = zoom_factor - 1.0
    delta_s = _fmt_num(delta)

    if seg.out_frame is None:
        if ramp_frames > 0:
            ramp_in = (
                f"between(on,{seg.in_frame},{seg.hold_start_frame - 1})"
                f"*{delta_s}*(0.5-0.5*cos(PI*(on-{seg.in_frame})/{ramp_frames}))"
            )
            hold = f"gte(on,{seg.hold_start_frame})*{delta_s}"
            return f"({ramp_in}+{hold})"
        return f"gte(on,{seg.in_frame})*{delta_s}"

    if ramp_frames == 0:
        return f"between(on,{seg.in_frame},{seg.out_frame})*{delta_s}"

    if seg.out_frame >= seg.hold_start_frame:
        terms = [
            (
                f"between(on,{seg.in_frame},{seg.hold_start_frame - 1})"
                f"*{delta_s}*(0.5-0.5*cos(PI*(on-{seg.in_frame})/{ramp_frames}))"
            ),
            f"between(on,{seg.hold_start_frame},{seg.out_frame})*{delta_s}",
        ]
        if seg.end_frame is not None and seg.end_frame >= seg.out_frame + 1:
            terms.append(
                f"between(on,{seg.out_frame + 1},{seg.end_frame})"
                f"*{delta_s}*(0.5+0.5*cos(PI*(on-{seg.out_frame})/{ramp_frames}))"
            )
        return "(" + "+".join(terms) + ")"

    actual_in = max(1, seg.out_frame - seg.in_frame)
    peak_delta = delta * ease_in_out(actual_in / ramp_frames)
    peak_s = _fmt_num(peak_delta)
    out_end = seg.end_frame if seg.end_frame is not None else seg.out_frame
    out_span = max(1, out_end - seg.out_frame + 1)
    terms = [
        (
            f"between(on,{seg.in_frame},{seg.out_frame - 1})"
            f"*{delta_s}*(0.5-0.5*cos(PI*(on-{seg.in_frame})/{ramp_frames}))"
        ),
        (
            f"between(on,{seg.out_frame},{out_end})"
            f"*{peak_s}*(0.5+0.5*cos(PI*(on-{seg.out_frame})/{out_span}))"
        ),
    ]
    return "(" + "+".join(terms) + ")"


def build_zoompan_filter(
    sidecar: ZoomSidecar,
    width: int,
    height: int,
    fps: float | None = None,
) -> str | None:
    eff_fps = float(fps if fps is not None else sidecar.fps)
    segments = build_segments(sidecar, width, height, eff_fps)
    if not segments or sidecar.zoom_factor <= 1.0:
        return None

    ramp_frames = compute_ramp_frames(sidecar.ramp_sec, eff_fps)
    delta_exprs = [_build_segment_delta_expr(seg, sidecar.zoom_factor, ramp_frames) for seg in segments]
    z_expr = "1+" + "+".join(delta_exprs)

    if len(segments) == 1:
        px_expr = _fmt_num(segments[0].x)
        py_expr = _fmt_num(segments[0].y)
    else:
        px_expr = "iw/2"
        py_expr = "ih/2"
        for seg in reversed(segments):
            if seg.end_frame is None:
                cond = f"gte(on,{seg.in_frame})"
            else:
                cond = f"between(on,{seg.in_frame},{seg.end_frame})"
            px_expr = f"if({cond},{_fmt_num(seg.x)},{px_expr})"
            py_expr = f"if({cond},{_fmt_num(seg.y)},{py_expr})"

    x_expr = f"clip({px_expr}-iw/(2*zoom),0,iw-iw/zoom)"
    y_expr = f"clip({py_expr}-ih/(2*zoom),0,ih-ih/zoom)"
    fps_s = _fmt_num(eff_fps)
    return f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d=1:s={width}x{height}:fps={fps_s}"


def resolve_ffmpeg(ffmpeg_arg: str | None = None, require: bool = True) -> str:
    candidate = ffmpeg_arg or os.environ.get("FFMPEG_PATH")
    if candidate:
        cand_path = Path(candidate)
        if cand_path.is_file():
            return str(cand_path)
        found = shutil.which(candidate)
        if found:
            return found
        if require:
            raise FileNotFoundError(f"ffmpeg binary not found: {candidate}")
        return candidate

    found = shutil.which("ffmpeg")
    if found:
        return found
    if require:
        raise FileNotFoundError("ffmpeg not found on PATH; install ffmpeg or pass --ffmpeg <path>")
    return "ffmpeg"


def resolve_ffprobe(ffmpeg_bin: str) -> str | None:
    ff_path = Path(ffmpeg_bin)
    if ff_path.parent != Path("."):
        for name in ("ffprobe", "ffprobe.exe"):
            sibling = ff_path.parent / name
            if sibling.is_file():
                return str(sibling)
    return shutil.which("ffprobe")


def probe_video(video_path: Path, ffprobe_bin: str | None) -> VideoInfo | None:
    if not ffprobe_bin or not video_path.is_file():
        return None

    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.warning("ffprobe failed for %s: %s", video_path, result.stderr.strip())
        return None

    try:
        payload = json.loads(result.stdout)
        streams = payload.get("streams") or []
        if not streams:
            return None
        stream = streams[0]
        width = int(stream["width"])
        height = int(stream["height"])
        rate_str = stream.get("r_frame_rate") or stream.get("avg_frame_rate") or "25/1"
        fps = float(Fraction(rate_str)) if "/" in str(rate_str) else float(rate_str)
        if fps <= 0:
            fps = DEFAULT_FPS
        raw_dur = (payload.get("format") or {}).get("duration")
        duration = float(raw_dur) if raw_dur not in (None, "N/A", "") else None
        return VideoInfo(width=width, height=height, fps=fps, duration=duration)
    except Exception as exc:
        logger.warning("Could not parse ffprobe output for %s: %s", video_path, exc)
        return None


def default_output_path(video_path: Path) -> Path:
    suffix = video_path.suffix or ".webm"
    return video_path.with_name(f"{video_path.stem}_zoomed{suffix}")


def build_ffmpeg_command(
    ffmpeg_bin: str,
    input_path: Path,
    output_path: Path,
    vf_filter: str,
) -> list[str]:
    return [
        ffmpeg_bin,
        "-y",
        "-i",
        str(input_path),
        "-vf",
        vf_filter,
        "-c:v",
        "libvpx-vp9",
        "-crf",
        "30",
        "-b:v",
        "0",
        "-deadline",
        "good",
        "-cpu-used",
        "4",
        "-row-mt",
        "1",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(output_path),
    ]


def _copy_unchanged(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dst.resolve():
        return dst
    fd, tmp_file = tempfile.mkstemp(dir=str(dst.parent), prefix=".copy_", suffix=dst.suffix or ".webm")
    os.close(fd)
    tmp_path = Path(tmp_file)
    try:
        _ = shutil.copy2(src, tmp_path)
        os.chmod(tmp_path, 0o644)
        _ = tmp_path.replace(dst)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return dst


def process_video(
    video_path: str | Path,
    output_path: str | Path | None = None,
    sidecar_path: str | Path | None = None,
    ffmpeg_path: str | None = None,
    dry_run: bool = False,
) -> tuple[Path, list[str] | None]:
    src = Path(video_path)
    if not dry_run and not src.is_file():
        raise FileNotFoundError(f"Video file not found: {src}")

    dst = Path(output_path) if output_path is not None else default_output_path(src)
    explicit_sidecar = Path(sidecar_path) if sidecar_path is not None else None
    found_sidecar = find_sidecar_path(src, explicit_sidecar)

    if found_sidecar is None:
        if dry_run:
            return dst, None
        return _copy_unchanged(src, dst), None

    sidecar = parse_sidecar(found_sidecar)
    if (
        sidecar.zoom_factor <= 1.0
        or not sidecar.events
        or not build_segments(sidecar, sidecar.viewport_width, sidecar.viewport_height, sidecar.fps)
    ):
        if dry_run:
            return dst, None
        return _copy_unchanged(src, dst), None

    ffmpeg_bin = resolve_ffmpeg(ffmpeg_path, require=not dry_run)
    ffprobe_bin = resolve_ffprobe(ffmpeg_bin)
    info = probe_video(src, ffprobe_bin)

    width = info.width if info else sidecar.viewport_width
    height = info.height if info else sidecar.viewport_height
    fps = info.fps if info else sidecar.fps

    vf_filter = build_zoompan_filter(sidecar, width=width, height=height, fps=fps)
    if vf_filter is None:
        if dry_run:
            return dst, None
        return _copy_unchanged(src, dst), None

    cmd = build_ffmpeg_command(ffmpeg_bin, src, dst, vf_filter)
    if dry_run:
        return dst, cmd

    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_file = tempfile.mkstemp(dir=str(dst.parent), prefix=".zoom_", suffix=dst.suffix or ".webm")
    os.close(fd)
    tmp_path = Path(tmp_file)
    try:
        run_cmd = build_ffmpeg_command(ffmpeg_bin, src, tmp_path, vf_filter)
        proc = subprocess.run(run_cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg exited with code {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        os.chmod(tmp_path, 0o644)
        _ = tmp_path.replace(dst)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return dst, cmd


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Apply Camtasia-style zoompan post-processing to a recorded video.")
    _ = parser.add_argument("--video", required=True, help="Path to input .webm video")
    _ = parser.add_argument("--output", help="Output .webm path (default: <video_stem>_zoomed.webm)")
    _ = parser.add_argument("--sidecar", help="Explicit path to .zoom.json sidecar")
    _ = parser.add_argument("--ffmpeg", help="Path to ffmpeg executable when not on PATH")
    _ = parser.add_argument("--dry-run", action="store_true", help="Print the ffmpeg command without encoding")
    args = parser.parse_args()

    try:
        out_path, cmd = process_video(
            video_path=args.video,
            output_path=args.output,
            sidecar_path=args.sidecar,
            ffmpeg_path=args.ffmpeg,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        logger.error("%s", exc)
        sys.exit(1)

    if args.dry_run:
        if cmd is None:
            print(f"# No zoom events; would copy {args.video} -> {out_path} unchanged")
        else:
            print(shlex.join(cmd))
    else:
        logger.info("Saved processed video: %s", out_path)


if __name__ == "__main__":
    main()
