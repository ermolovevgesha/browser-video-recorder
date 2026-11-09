from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import qscript


def preview_video(video_url: str, view_seconds: float) -> None:
    from playwright.sync_api import sync_playwright

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
        page.goto(video_url)

        if view_seconds > 0:
            time.sleep(view_seconds)
        else:
            input("Press Enter to continue to next company...")

        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture company videos and optionally upload/preview them.")
    parser.add_argument("--csv", default="input.csv", help="Path to input CSV file (default: input.csv)")
    parser.add_argument("--server", help="Base URL of web service (e.g. http://localhost:8000)")
    parser.add_argument(
        "--view-seconds",
        type=float,
        default=5.0,
        help="Seconds to preview video in browser (0 = wait for Enter, default: 5.0)",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"File not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    video_client = None
    if args.server:
        try:
            import video_client as vc

            video_client = vc
        except ImportError as e:
            print(f"Failed to import video_client: {e}", file=sys.stderr)
            sys.exit(1)

    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";", quotechar='"')
        for i, row in enumerate(reader, start=1):
            company = row.get("Company Name", f"Company_{i}")
            print(f"[{i}] Processing: {company}...")

            video_path = qscript.CaptureVideo_Magento(
                url=row.get("URL", "https://duckduckgo.com"),
                company_name=company,
                language=row.get("Language", "en"),
                country=row.get("Country", ""),
                postcode=row.get("Postcode", ""),
                city=row.get("City", ""),
                street=row.get("Street", ""),
                house_no=row.get("HouseNo", ""),
            )

            if video_path:
                print(f"Saved: {video_path}")
                if video_client and args.server:
                    try:
                        video_url = video_client.UploadVideo(args.server, video_path, company)
                        print(f"Uploaded: {video_url}")
                        try:
                            preview_video(video_url, args.view_seconds)
                        except Exception as e:
                            print(f"Failed to preview video: {e}")
                    except Exception as e:
                        print(f"Failed to upload video for {company}: {e}")
            else:
                print(f"Failed to record for {company}")

            time.sleep(1.5)


if __name__ == "__main__":
    main()
