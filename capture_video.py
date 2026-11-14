from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

import qscript

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def preview_video(video_url: str, view_seconds: float) -> None:
    with sync_playwright() as pw:
        launch_args = {"headless": False}
        custom_bin = None
        for candidate in (
            "/run/current-system/sw/bin/chromium",
            "/usr/bin/chromium",
            "/usr/bin/google-chrome",
        ):
            if Path(candidate).is_file():
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

        page = browser.new_page()
        _ = page.goto(video_url, wait_until="domcontentloaded")

        try:
            _ = page.wait_for_function(
                "() => { const v = document.querySelector('video'); return !!v && v.readyState >= 2; }",
                timeout=12000,
            )
            logger.info("Preview: video is ready and playing")
        except Exception:
            logger.warning("Preview: video did not reach ready state within timeout, displaying anyway")

        if view_seconds > 0:
            time.sleep(view_seconds)
        else:
            try:
                _ = input("Press Enter to continue to next company...")
            except EOFError:
                logger.warning("No interactive terminal available to read Enter; continuing...")

        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture company videos and optionally upload/preview them.")
    _ = parser.add_argument("--csv", default="input.csv", help="Path to input CSV file (default: input.csv)")
    _ = parser.add_argument("--server", help="Base URL of web service (e.g. http://localhost:8000)")
    _ = parser.add_argument(
        "--view-seconds",
        type=float,
        default=5.0,
        help="Seconds to preview video in browser (0 = wait for Enter, default: 5.0)",
    )
    _ = parser.add_argument(
        "--zoom",
        action="store_true",
        help="Trigger programmatic zoom-in/out events during recording via window.__zoomAt",
    )
    _ = parser.add_argument(
        "--headed",
        action="store_true",
        help="Open a visible browser window so user can move the cursor and press F5 manually",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        logger.error("File not found: %s", csv_path)
        sys.exit(1)

    video_client = None
    if args.server:
        try:
            import video_client
        except ImportError as e:
            logger.error("Option --server requires 'requests' to be installed: %s", e)
            sys.exit(1)

    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";", quotechar='"')
        rows = [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in reader]

    if args.headed:
        logger.info(
            "Headed mode enabled: move mouse to aim cursor and press F5 to zoom "
            "(script pauses while active + %.1fs cooldown)",
            qscript.USER_MOUSE_COOLDOWN_SEC,
        )

    ok_count = 0
    failed_count = 0
    uploaded_count = 0

    for i, row in enumerate(rows, start=1):
        company = row.get("Company Name", f"Company_{i}")
        logger.info("[%d/%d] Processing: %s...", i, len(rows), company)

        capture_kwargs = {
            "url": row.get("URL", "https://duckduckgo.com"),
            "company_name": company,
            "language": row.get("Language", "en"),
            "country": row.get("Country", ""),
            "postcode": row.get("Postcode", ""),
            "city": row.get("City", ""),
            "street": row.get("Street", ""),
            "house_no": row.get("HouseNo", ""),
        }
        if args.zoom:
            capture_kwargs["zoom"] = True
        if args.headed:
            capture_kwargs["headless"] = False

        video_path = qscript.CaptureVideo_Magento(**capture_kwargs)

        if video_path:
            ok_count += 1
            logger.info("Saved: %s", video_path)
            if args.server and video_client is not None:
                try:
                    video_url = video_client.UploadVideo(args.server, video_path, company)
                    uploaded_count += 1
                    logger.info("Uploaded: %s", video_url)
                    try:
                        preview_video(video_url, args.view_seconds)
                    except Exception as e:
                        logger.error("Failed to preview video: %s", e)
                except Exception as e:
                    logger.error("Failed to upload video for %s: %s", company, e)
        else:
            failed_count += 1
            logger.error("Failed to record for %s", company)

        time.sleep(1.5)

    summary = f"Done: {ok_count} ok, {failed_count} failed"
    if args.server:
        summary += f"; uploaded: {uploaded_count}"
    logger.info(summary)

    if failed_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
