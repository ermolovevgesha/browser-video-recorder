from __future__ import annotations

import glob
import json
import logging
import os
import random
import shutil
import subprocess
import tempfile
import time
import zlib
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary

from playwright.sync_api import Page, sync_playwright

logger = logging.getLogger(__name__)

PATH_TO_VIDEO_FOLDER = "./video"
HEADLESS: bool = True

VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 720
RECORDING_FPS = 25
DEFAULT_ZOOM_FACTOR = 2.0
DEFAULT_RAMP_SEC = 0.5
USER_MOUSE_COOLDOWN_SEC = 2.0
SYNC_COLOUR = (255, 0, 255)

current_page: Page | None = None


class UserActivityTracker:
    def __init__(self, cooldown_sec: float = USER_MOUSE_COOLDOWN_SEC, enabled: bool = True) -> None:
        self.cooldown_sec = float(cooldown_sec)
        self.enabled = bool(enabled)
        self.last_user_activity: float | None = None

    def note_activity(self, now: float | None = None) -> None:
        if not self.enabled:
            return
        self.last_user_activity = time.monotonic() if now is None else float(now)

    def remaining_cooldown(self, now: float | None = None) -> float:
        if not self.enabled or self.last_user_activity is None:
            return 0.0
        current = time.monotonic() if now is None else float(now)
        return max(0.0, self.cooldown_sec - (current - self.last_user_activity))

    def is_paused(self, now: float | None = None) -> bool:
        return self.remaining_cooldown(now) > 0.0


_PAGE_TRACKERS: WeakKeyDictionary[Any, UserActivityTracker] = WeakKeyDictionary()


def bind_activity_tracker(page: Any, tracker: UserActivityTracker) -> None:
    if page is not None:
        _PAGE_TRACKERS[page] = tracker


def get_activity_tracker(page: Any) -> UserActivityTracker | None:
    return _PAGE_TRACKERS.get(page) if page is not None else None


def find_ffmpeg(ffmpeg_path: str | None = None) -> str | None:
    candidate = ffmpeg_path or os.environ.get("FFMPEG_PATH")
    if candidate:
        cand_path = Path(candidate)
        if cand_path.is_file() and (os.name == "nt" or os.access(cand_path, os.X_OK)):
            return str(cand_path)
        found = shutil.which(candidate)
        if found:
            return found

    exe = shutil.which("ffmpeg")
    if exe:
        return exe

    roots = [
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
        os.path.expanduser("~/Library/Caches/ms-playwright"),
        os.path.expanduser("~/.cache/ms-playwright"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
    ]
    for root in filter(None, roots):
        for path in sorted(glob.glob(os.path.join(root, "ffmpeg-*", "ffmpeg*")), reverse=True):
            name = Path(path).name
            if not name.startswith("ffmpeg"):
                continue
            if os.path.isfile(path) and (os.name == "nt" or os.access(path, os.X_OK)):
                return path
    return None


def _png_rgb(data: bytes) -> tuple[int, int, int]:
    pos, idat, colour_type = 8, bytearray(), 2
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        body_start = pos + 8
        body_end = body_start + length
        if body_end + 4 > len(data):
            break
        body = data[body_start:body_end]
        if kind == b"IHDR" and len(body) >= 10:
            colour_type = body[9]
        elif kind == b"IDAT":
            idat.extend(body)
        elif kind == b"IEND":
            break
        pos = body_end + 4
    row = zlib.decompress(bytes(idat))
    if colour_type in (2, 6):
        return int(row[1]), int(row[2]), int(row[3])
    gray = int(row[1])
    return gray, gray, gray


def sync_marker_times(
    video_path: str | Path,
    ffmpeg_bin: str,
    fps: float = RECORDING_FPS,
) -> list[float]:
    with tempfile.TemporaryDirectory(prefix="qsync_") as tmp_dir:
        pattern = os.path.join(tmp_dir, "%06d.png")
        subprocess.run(
            [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-i",
                str(video_path),
                "-r",
                str(fps),
                "-vf",
                "crop=2:2:2:2,scale=1:1",
                "-pix_fmt",
                "rgb24",
                "-f",
                "image2",
                pattern,
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )
        frames = sorted(os.listdir(tmp_dir))
        times: list[float] = []
        prev_hit = False
        for idx, name in enumerate(frames):
            rgb = _png_rgb(Path(tmp_dir, name).read_bytes())
            hit = all(abs(c - s) < 70 for c, s in zip(rgb, SYNC_COLOUR))
            if hit and not prev_hit:
                times.append(idx / float(fps))
            prev_hit = hit
    return times


def estimate_offset(
    event_times: list[float],
    marker_times: list[float],
    window: tuple[float, float] = (-15.0, 2.0),
    tol: float = 0.12,
) -> float | None:
    diffs = sorted(
        m - e
        for e in event_times
        for m in marker_times
        if window[0] <= m - e <= window[1]
    )
    if not diffs:
        return None
    best = max(diffs, key=lambda d: (sum(abs(x - d) <= tol for x in diffs), -abs(d)))
    close = sorted(x for x in diffs if abs(x - best) <= tol)
    return close[len(close) // 2]


class ZoomEventRecorder:
    def __init__(
        self,
        width: int = VIEWPORT_WIDTH,
        height: int = VIEWPORT_HEIGHT,
        fps: int = RECORDING_FPS,
        zoom_factor: float = DEFAULT_ZOOM_FACTOR,
        ramp_sec: float = DEFAULT_RAMP_SEC,
        start_time: float | None = None,
        start_epoch_ms: float | None = None,
        activity_tracker: UserActivityTracker | None = None,
    ) -> None:
        self.width = width
        self.height = height
        self.fps = fps
        self.zoom_factor = zoom_factor
        self.ramp_sec = ramp_sec
        self.start_time = time.monotonic() if start_time is None else start_time
        self.start_epoch_ms = (time.time() * 1000.0) if start_epoch_ms is None else start_epoch_ms
        self.activity_tracker = activity_tracker
        self.events: list[dict[str, Any]] = []
        self._raw_times: list[float] = []
        self._marked_times: list[float] = []
        self.sync: dict[str, Any] | None = None
        self.is_zoomed = False

    def reset_start_time(
        self,
        start_time: float | None = None,
        start_epoch_ms: float | None = None,
    ) -> None:
        self.start_time = time.monotonic() if start_time is None else start_time
        self.start_epoch_ms = (time.time() * 1000.0) if start_epoch_ms is None else start_epoch_ms

    def toggle(
        self,
        x: float | None = None,
        y: float | None = None,
        timestamp: float | None = None,
        client_ms: float | None = None,
        target_state: str | None = None,
        from_page: bool = False,
    ) -> str | None:
        if target_state == "in" and self.is_zoomed:
            return None
        if target_state == "out" and not self.is_zoomed:
            return None

        raw_x = self.width / 2.0 if x is None else float(x)
        raw_y = self.height / 2.0 if y is None else float(y)
        px = int(round(max(0.0, min(raw_x, float(self.width)))))
        py = int(round(max(0.0, min(raw_y, float(self.height)))))

        if timestamp is not None:
            elapsed = float(timestamp)
        elif client_ms is not None and self.start_epoch_ms is not None:
            elapsed = (float(client_ms) - self.start_epoch_ms) / 1000.0
        else:
            elapsed = time.monotonic() - self.start_time
        raw_t = max(0.0, elapsed)
        t_sec = round(raw_t, 3)

        self.is_zoomed = not self.is_zoomed
        event_type = "in" if self.is_zoomed else "out"
        self.events.append({"t": t_sec, "x": px, "y": py, "type": event_type})
        self._raw_times.append(raw_t)
        if from_page or client_ms is not None:
            self._marked_times.append(raw_t)
        return event_type

    def align_to_video(self, video_path: str | Path, ffmpeg_path: str | None = None) -> float | None:
        if not self.events or not self._marked_times:
            return None
        exe = find_ffmpeg(ffmpeg_path)
        if not exe:
            logger.warning("Zoom timeline alignment skipped: ffmpeg not found")
            self.sync = {"aligned": False, "reason": "ffmpeg not found"}
            return None
        try:
            markers = sync_marker_times(video_path, exe, fps=self.fps)
        except (OSError, subprocess.SubprocessError, ValueError, zlib.error) as exc:
            logger.warning("Zoom timeline alignment failed: %s", exc)
            self.sync = {"aligned": False, "reason": str(exc)[:200]}
            return None
        offset = estimate_offset(self._marked_times, markers)
        if offset is None:
            logger.warning("Zoom timeline alignment skipped: no matching sync markers (%d found)", len(markers))
            self.sync = {"aligned": False, "reason": "no markers", "markers": len(markers)}
            return None
        for ev, raw_t in zip(self.events, self._raw_times):
            ev["t"] = round(max(0.0, raw_t + offset), 3)
        self.sync = {
            "aligned": True,
            "offset": round(offset, 3),
            "markers": len(markers),
            "toggles": len(self._marked_times),
        }
        logger.info("Zoom timeline aligned by %+.3fs (%d markers)", offset, len(markers))
        return offset

    def sidecar_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "viewport": {"width": self.width, "height": self.height},
            "fps": self.fps,
            "zoom_factor": self.zoom_factor,
            "ramp_sec": self.ramp_sec,
            "events": list(self.events),
        }
        if self.sync is not None:
            payload["sync"] = dict(self.sync)
        return payload

    def write_sidecar(self, video_path: str | Path) -> Path | None:
        sidecar_path = Path(f"{video_path}.zoom.json")
        if not self.events:
            if sidecar_path.is_file():
                sidecar_path.unlink()
            return None
        _ = sidecar_path.write_text(json.dumps(self.sidecar_payload(), indent=2), encoding="utf-8")
        return sidecar_path


CURSOR_SCRIPT = Path(__file__).with_name("cursor_overlay.js").read_text(encoding="utf-8")


def read_cursor_position(page: Page, fallback_x: float, fallback_y: float) -> tuple[float, float]:
    try:
        pos = page.evaluate(
            "() => (typeof window.__getCursorPos === 'function' ? window.__getCursorPos() : null)"
        )
        if isinstance(pos, dict) and "x" in pos and "y" in pos:
            return float(pos["x"]), float(pos["y"])
    except Exception:
        pass
    return fallback_x, fallback_y


def wait_while_user_active(page: Page, tracker: UserActivityTracker | None) -> None:
    if not tracker or not tracker.enabled or page.is_closed():
        return
    announced = False
    while tracker.is_paused() and not page.is_closed():
        if not announced:
            print(
                f"[headed] User mouse/F5 active — pausing script ({tracker.cooldown_sec:.1f}s cooldown after idle)..."
            )
            announced = True
        wait_ms = max(25, min(50, int(tracker.remaining_cooldown() * 1000) + 5))
        page.wait_for_timeout(wait_ms)
    if announced and not page.is_closed():
        print("[headed] Resuming script...")


def pause_execution(page: Page, seconds: float, tracker: UserActivityTracker | None = None) -> None:
    if tracker is None:
        tracker = get_activity_tracker(page)
    remaining = max(0.0, float(seconds))
    while remaining > 0 and not page.is_closed():
        if tracker and tracker.is_paused():
            wait_while_user_active(page, tracker)
            continue
        step_s = min(0.05, remaining)
        t0 = time.monotonic()
        page.wait_for_timeout(max(1, int(round(step_s * 1000))))
        elapsed = time.monotonic() - t0
        if not (tracker and tracker.is_paused()):
            remaining -= elapsed


def run_with_script_mouse_target(page: Page, x: float, y: float, fn: Any) -> None:
    try:
        page.evaluate(
            "([tx, ty]) => { window.__scriptMouseTarget = { x: Math.round(tx), y: Math.round(ty) }; }",
            [x, y],
        )
    except Exception:
        pass
    try:
        fn()
    finally:
        try:
            page.evaluate("() => { window.__scriptMouseTarget = null; }")
        except Exception:
            pass


def script_mouse_move(page: Page, x: float, y: float) -> None:
    run_with_script_mouse_target(page, x, y, lambda: page.mouse.move(x, y))


def script_mouse_click(
    page: Page,
    x: float,
    y: float,
    tracker: UserActivityTracker | None = None,
) -> tuple[float, float]:
    if tracker is None:
        tracker = get_activity_tracker(page)
    if tracker and tracker.is_paused():
        x, y = move_mouse(page, x, y, x, y, steps=20)
    run_with_script_mouse_target(page, x, y, lambda: page.mouse.click(x, y))
    return x, y


def move_mouse(
    page: Page,
    start_x: float,
    start_y: float,
    target_x: float,
    target_y: float,
    steps: int = 35,
    delay: float = 0.012,
) -> tuple[float, float]:
    tracker = get_activity_tracker(page)
    while not page.is_closed():
        if tracker and tracker.is_paused():
            wait_while_user_active(page, tracker)
            continue

        cur_x, cur_y = read_cursor_position(page, start_x, start_y) if tracker else (start_x, start_y)
        dist = ((target_x - cur_x) ** 2 + (target_y - cur_y) ** 2) ** 0.5
        if dist < 1.0:
            script_mouse_move(page, target_x, target_y)
            if tracker and tracker.is_paused():
                continue
            return target_x, target_y

        step_count = max(20, min(70, int(dist / 14)))
        bend = random.uniform(-0.15, 0.15) * dist
        nx, ny = -(target_y - cur_y) / dist, (target_x - cur_x) / dist
        cx, cy = (cur_x + target_x) / 2 + nx * bend, (cur_y + target_y) / 2 + ny * bend

        interrupted = False
        for i in range(1, step_count + 1):
            if tracker and tracker.is_paused():
                interrupted = True
                break
            t = i / step_count
            ease = t * t * (3 - 2 * t)
            x = (1 - ease) ** 2 * cur_x + 2 * (1 - ease) * ease * cx + ease * ease * target_x
            y = (1 - ease) ** 2 * cur_y + 2 * (1 - ease) * ease * cy + ease * ease * target_y
            script_mouse_move(page, x, y)
            if tracker and tracker.is_paused():
                interrupted = True
                break
            page.wait_for_timeout(max(1, int(round(delay * 1000))))
            if tracker and tracker.is_paused():
                interrupted = True
                break

        if interrupted or (tracker and tracker.is_paused()):
            continue
        return target_x, target_y
    return target_x, target_y


def type_text(page: Page, text: str) -> None:
    tracker = get_activity_tracker(page)
    for ch in text:
        if tracker and tracker.is_paused():
            wait_while_user_active(page, tracker)
        page.keyboard.type(ch)
        pause_execution(page, 0.2 if ch == " " else random.uniform(0.07, 0.14), tracker)


def ShowCallout(
    text: str,
    bgColor: str = "#ffeb3b",
    textColor: str = "#000000",
    fade: float = 2.0,
    page: Page | None = None,
) -> None:
    target = page or current_page
    if not target:
        return
    try:
        target.evaluate(
            """([msg, bg, tc, f]) => {
                if (window.showCallout) {
                    window.showCallout(msg, bg, tc, f);
                }
            }""",
            [text, bgColor, textColor, fade],
        )
    except Exception:
        pass


def ZoomAt(
    x: float | None = None,
    y: float | None = None,
    page: Page | None = None,
    state: str | None = None,
) -> str | None:
    target = page or current_page
    if not target:
        return None
    try:
        return target.evaluate(
            """([zx, zy, targetState]) => {
                if (typeof window.__zoomAt === 'function') {
                    return window.__zoomAt(zx, zy, 'api', targetState);
                }
                return null;
            }""",
            [x, y, state],
        )
    except Exception:
        return None


def setup_zoom_capture(
    context: Any,
    width: int = VIEWPORT_WIDTH,
    height: int = VIEWPORT_HEIGHT,
    fps: int = RECORDING_FPS,
    zoom_factor: float = DEFAULT_ZOOM_FACTOR,
    ramp_sec: float = DEFAULT_RAMP_SEC,
    pause_on_user_mouse: bool = False,
    cooldown_sec: float = USER_MOUSE_COOLDOWN_SEC,
) -> ZoomEventRecorder:
    tracker = UserActivityTracker(cooldown_sec=cooldown_sec, enabled=pause_on_user_mouse)
    recorder = ZoomEventRecorder(
        width=width,
        height=height,
        fps=fps,
        zoom_factor=zoom_factor,
        ramp_sec=ramp_sec,
        activity_tracker=tracker,
    )
    def record_zoom(
        x: Any = None,
        y: Any = None,
        client_ms: Any = None,
        source: str = "api",
        target_state: str | None = None,
    ) -> str | None:
        if source == "f5":
            tracker.note_activity()
        return recorder.toggle(x, y, client_ms=client_ms, target_state=target_state, from_page=True)

    context.expose_function("__pyRecordZoom", record_zoom)
    if pause_on_user_mouse:
        context.expose_function("__pyUserActivity", tracker.note_activity)
    context.add_init_script(CURSOR_SCRIPT)
    return recorder


def launch_browser_and_context(
    pw: Any,
    output_dir: Path,
    headless: bool,
    language: str = "en",
) -> tuple[Any, Any, Any, ZoomEventRecorder]:
    launch_args: dict[str, Any] = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
        "ignore_default_args": ["--enable-automation"],
    }
    custom_bin = os.environ.get("CHROMIUM_PATH")
    if not custom_bin:
        for candidate in ["/usr/bin/chromium", "/usr/bin/google-chrome", "/run/current-system/sw/bin/chromium"]:
            if os.path.exists(candidate):
                custom_bin = candidate
                break
    if custom_bin:
        launch_args["executable_path"] = custom_bin
    else:
        launch_args["channel"] = "chromium"

    try:
        browser = pw.chromium.launch(**launch_args)
    except Exception:
        launch_args.pop("channel", None)
        browser = pw.chromium.launch(**launch_args)

    probe_page = browser.new_page()
    native_ua = probe_page.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
    probe_page.close()

    locale_val = "en-US"
    if language:
        clean_lang = language.strip().replace("_", "-")
        locale_val = clean_lang if "-" in clean_lang else f"{clean_lang}-US"

    context = browser.new_context(
        record_video_dir=str(output_dir),
        record_video_size={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        user_agent=native_ua,
        locale=locale_val,
        bypass_csp=True,
    )
    recorder = setup_zoom_capture(
        context,
        pause_on_user_mouse=not headless,
        cooldown_sec=USER_MOUSE_COOLDOWN_SEC,
    )
    page = context.new_page()
    if recorder.activity_tracker is not None:
        bind_activity_tracker(page, recorder.activity_tracker)
    recorder.reset_start_time()
    return browser, context, page, recorder


def save_recorded_video(
    video: Any,
    company_name: str,
    output_dir: Path,
    recorder: ZoomEventRecorder,
) -> str:
    raw_path = video.path()
    safe_name = "".join(c for c in company_name if c.isalnum() or c in (" ", "-", "_")).strip()
    final_path = output_dir / f"{safe_name or 'video'}.webm"
    if final_path.exists():
        final_path.unlink()
    _ = shutil.move(raw_path, final_path)
    _ = recorder.align_to_video(final_path)
    _ = recorder.write_sidecar(final_path)
    return str(final_path.resolve())


def CaptureVideo_Magento(
    url,
    company_name,
    language,
    country,
    postcode,
    city,
    street,
    house_no,
    zoom: bool | None = None,
    headless: bool | None = None,
):
    global current_page

    enable_zoom = (
        zoom
        if zoom is not None
        else os.environ.get("ENABLE_ZOOM", "").strip().lower() in ("1", "true", "yes")
    )

    output_dir = Path(PATH_TO_VIDEO_FOLDER).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    browser = None
    context = None
    page = None

    try:
        with sync_playwright() as pw:
            if headless is not None:
                headless_val = bool(headless)
            elif os.environ.get("HEADLESS") is not None:
                headless_val = os.environ.get("HEADLESS", "1") not in ("0", "false", "False", "no")
            else:
                headless_val = HEADLESS

            browser, context, page, recorder = launch_browser_and_context(
                pw, output_dir, headless=headless_val, language=language
            )
            tracker = recorder.activity_tracker
            current_page = page

            page.goto(url, wait_until="domcontentloaded", timeout=40000)

            x, y = 300.0, 350.0
            script_mouse_move(page, x, y)
            pause_execution(page, 0.5, tracker)

            ShowCallout(f"Searching Magento services for {company_name} ({city})", fade=1.5)
            pause_execution(page, 1.5, tracker)

            search_input = page.wait_for_selector('textarea[name="q"]', state="visible", timeout=15000)
            box = search_input.bounding_box()
            if not box:
                return None

            target_input_x = box["x"] + box["width"] * 0.35
            target_input_y = box["y"] + box["height"] * 0.5
            x, y = move_mouse(page, x, y, target_input_x, target_input_y, steps=35)
            x, y = script_mouse_click(page, x, y, tracker)
            pause_execution(page, 0.4, tracker)

            if enable_zoom:
                _ = ZoomAt(x, y, page, state="in")
                pause_execution(page, 0.4, tracker)

            type_text(page, f"Magento developer in {city}")
            pause_execution(page, 0.6, tracker)

            if enable_zoom:
                _ = ZoomAt(x, y, page, state="out")
                pause_execution(page, 0.5, tracker)

            submit_btn = page.locator(
                'form button[type="submit"]:visible, button[aria-label="Search"]:visible'
            ).first
            if submit_btn.count() == 0:
                try:
                    page.wait_for_selector(
                        'form button[type="submit"]:visible, button[aria-label="Search"]:visible',
                        state="visible",
                        timeout=3000,
                    )
                    submit_btn = page.locator(
                        'form button[type="submit"]:visible, button[aria-label="Search"]:visible'
                    ).first
                except Exception:
                    pass

            btn_box = submit_btn.bounding_box() if submit_btn.count() > 0 else None
            if btn_box:
                x, y = move_mouse(
                    page,
                    x,
                    y,
                    btn_box["x"] + btn_box["width"] * 0.5,
                    btn_box["y"] + btn_box["height"] * 0.5,
                    steps=25,
                )
                x, y = script_mouse_click(page, x, y, tracker)
            else:
                page.keyboard.press("Enter")

            first_result_selector = 'a[data-testid="result-title-a"]'
            for attempt_wait in range(3):
                try:
                    page.wait_for_selector(first_result_selector, state="visible", timeout=15000)
                    break
                except Exception as e:
                    if "context was destroyed" in str(e).lower() or "navigation" in str(e).lower():
                        pause_execution(page, 1.0, tracker)
                        continue
                    if attempt_wait == 1:
                        try:
                            page.reload(wait_until="domcontentloaded", timeout=15000)
                        except Exception:
                            pass
                    else:
                        page.keyboard.press("Enter")
                    pause_execution(page, 1.0, tracker)
            pause_execution(page, 1.0, tracker)

            first_result = page.locator(first_result_selector).first
            res_box = first_result.bounding_box()
            if res_box:
                try:
                    first_result.evaluate("el => { el.target = '_self'; el.removeAttribute('target'); }")
                except Exception:
                    pass

                target_click_x = res_box["x"] + min(50.0, res_box["width"] * 0.3)
                target_click_y = res_box["y"] + res_box["height"] * 0.5
                x, y = move_mouse(page, x, y, target_click_x, target_click_y, steps=35)
                if enable_zoom:
                    _ = ZoomAt(x, y, page, state="in")
                    pause_execution(page, 0.6, tracker)
                x, y = script_mouse_click(page, x, y, tracker)
                try:
                    pause_execution(page, 0.25, tracker)
                    if "duckduckgo.com" in (page.url or ""):
                        first_result.evaluate("el => el.click()")
                except Exception:
                    pass
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=7000)
                except Exception:
                    pass
                pause_execution(page, 3.0, tracker)
                if enable_zoom:
                    _ = ZoomAt(x, y, page, state="out")
                    pause_execution(page, 0.6, tracker)

            wait_while_user_active(page, tracker)
            try:
                page.evaluate("() => true")
            except Exception:
                pass

            video = page.video
            context.close()
            browser.close()
            browser = None
            context = None

            return save_recorded_video(video, company_name, output_dir, recorder)

    except Exception as e:
        print(f"Error processing {company_name}: {e}")
        safe_name = "".join(c for c in company_name if c.isalnum() or c in (" ", "-", "_")).strip()
        failed_path = output_dir / f"{safe_name or 'video'}_FAILED.webm"
        if page:
            try:
                page.screenshot(path=str(output_dir / f"debug_error_{safe_name or 'unknown'}.png"))
            except Exception:
                pass
        if context:
            try:
                context.close()
            except Exception:
                pass
        if page and page.video:
            try:
                raw = page.video.path()
                if os.path.exists(raw):
                    if failed_path.exists():
                        failed_path.unlink()
                    _ = shutil.move(raw, failed_path)
                    print(f"Preserved failed attempt video: {failed_path}")
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        return None
    finally:
        current_page = None
