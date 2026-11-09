import csv
import sys
import time
from pathlib import Path

import qscript


def main() -> None:
    csv_path = Path("input.csv")
    if not csv_path.exists():
        print(f"File not found: {csv_path}", file=sys.stderr)
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
            else:
                print(f"Failed to record for {company}")

            time.sleep(1.5)


if __name__ == "__main__":
    main()
