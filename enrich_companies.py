from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

from db import (
    CompanyStatus,
    get_companies_for_enrichment,
    init_db,
    update_company_enrichment,
    update_company_status,
)
from impressum import (
    DeepSeekAPIError,
    DeepSeekParseError,
    call_deepseek,
    classify_and_map,
    extract_visible_text,
    fetch_page,
    fetch_page_playwright,
    find_impressum_url,
    normalize_url,
    optimize_text,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or (Path(__file__).resolve().parent / ".env")
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def enrich_company(
    company: dict[str, Any],
    session: requests.Session | None = None,
    use_playwright_fallback: bool = True,
    dry_run: bool = False,
) -> CompanyStatus:
    company_id = int(company["id"])
    company_name = str(company["company_name"])
    web_url = normalize_url(company.get("web"))

    if not web_url:
        logger.info("[%s] Empty web URL -> %s (%d)", company_name, CompanyStatus.NO_WEBSITE.name, CompanyStatus.NO_WEBSITE)
        if not dry_run:
            update_company_status(company_id, CompanyStatus.NO_WEBSITE)
        return CompanyStatus.NO_WEBSITE

    # 1. Fetch home page and locate Impressum/Imprint link
    try:
        home_html, home_final_url = fetch_page(web_url, session=session)
    except requests.RequestException as exc:
        logger.error("[%s] Failed to fetch home page %s: %s -> %s (%d)", company_name, web_url, exc, CompanyStatus.ERROR.name, CompanyStatus.ERROR)
        if not dry_run:
            update_company_status(company_id, CompanyStatus.ERROR)
        return CompanyStatus.ERROR

    impressum_url = find_impressum_url(home_html, home_final_url)

    # Optional Playwright fallback if home page rendered its footer via JS
    if not impressum_url and use_playwright_fallback:
        try:
            logger.info("[%s] No Impressum link in static HTML; trying Playwright fallback on %s", company_name, home_final_url)
            pw_html, pw_url = fetch_page_playwright(home_final_url)
            impressum_url = find_impressum_url(pw_html, pw_url)
        except Exception as exc:
            logger.warning("[%s] Playwright fallback failed on %s: %s", company_name, home_final_url, exc)

    if not impressum_url:
        logger.warning(
            "[%s] No Impressum/Imprint link found on %s -> %s (%d)",
            company_name,
            home_final_url,
            CompanyStatus.NO_IMPRESSUM_LINK_FOUND.name,
            CompanyStatus.NO_IMPRESSUM_LINK_FOUND,
        )
        if not dry_run:
            update_company_status(company_id, CompanyStatus.NO_IMPRESSUM_LINK_FOUND)
        return CompanyStatus.NO_IMPRESSUM_LINK_FOUND

    # 2. Fetch Impressum page and extract visible text
    try:
        imp_html, resolved_imp_url = fetch_page(impressum_url, session=session)
    except requests.RequestException as exc:
        logger.error(
            "[%s] Failed to fetch Impressum %s: %s -> %s (%d)",
            company_name,
            impressum_url,
            exc,
            CompanyStatus.ERROR.name,
            CompanyStatus.ERROR,
        )
        if not dry_run:
            update_company_status(company_id, CompanyStatus.ERROR, impressum_url=impressum_url)
        return CompanyStatus.ERROR

    visible_text = extract_visible_text(imp_html)
    if not visible_text and use_playwright_fallback:
        try:
            pw_imp_html, resolved_imp_url = fetch_page_playwright(resolved_imp_url)
            visible_text = extract_visible_text(pw_imp_html)
        except Exception as exc:
            logger.warning("[%s] Playwright fallback failed on Impressum %s: %s", company_name, resolved_imp_url, exc)

    # 3. Optimize text for token budget
    optimized_text, stats = optimize_text(visible_text)
    logger.info(
        "[%s] Impressum: %s | chars: %d -> %d | est. tokens: %d -> %d | strategy: %s",
        company_name,
        resolved_imp_url,
        stats["before_chars"],
        stats["after_chars"],
        stats["before_tokens"],
        stats["after_tokens"],
        stats["strategy"],
    )

    if dry_run:
        logger.info("[%s] --dry-run enabled; skipping DeepSeek call and DB write.", company_name)
        print(f"\n=== {company_name} ({resolved_imp_url}) ===")
        print(optimized_text)
        return CompanyStatus.OK

    # 4. Call DeepSeek for structured extraction
    raw_json: dict[str, Any] | None = None
    try:
        raw_json = call_deepseek(optimized_text, session=session)
    except DeepSeekParseError as exc:
        logger.warning(
            "[%s] DeepSeek returned unparseable output: %s -> %s (%d)",
            company_name,
            exc,
            CompanyStatus.PARSING_FAILED.name,
            CompanyStatus.PARSING_FAILED,
        )
        update_company_enrichment(
            company_id=company_id,
            status=CompanyStatus.PARSING_FAILED,
            impressum_url=resolved_imp_url,
            legal_name=None,
            postal_address=None,
            handelsregister=None,
            vat_number=None,
            phone=None,
            email=None,
            responsible_person=None,
            raw_json=None,
        )
        return CompanyStatus.PARSING_FAILED
    except DeepSeekAPIError as exc:
        logger.error(
            "[%s] DeepSeek API error: %s -> %s (%d)",
            company_name,
            exc,
            CompanyStatus.ERROR.name,
            CompanyStatus.ERROR,
        )
        update_company_status(company_id, CompanyStatus.ERROR, impressum_url=resolved_imp_url)
        return CompanyStatus.ERROR

    # 5. Classify status and write back to PostgreSQL
    status, mapped = classify_and_map(raw_json, company_name)
    update_company_enrichment(
        company_id=company_id,
        status=status,
        impressum_url=resolved_imp_url,
        legal_name=mapped["legal_name"],
        postal_address=mapped["postal_address"],
        handelsregister=mapped["handelsregister"],
        vat_number=mapped["vat_number"],
        phone=mapped["phone"],
        email=mapped["email"],
        responsible_person=mapped["responsible_person"],
        raw_json=mapped["raw_json"],
    )
    logger.info("[%s] Stored enrichment result -> %s (%d)", company_name, status.name, status)
    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich companies in PostgreSQL with Impressum data via DeepSeek."
    )
    _ = parser.add_argument("--company", help="Process only this company_name.")
    _ = parser.add_argument("--limit", type=int, help="Process at most N rows.")
    _ = parser.add_argument(
        "--status",
        help="Process only rows with this status code (0-5, or 'null'/'pending').",
    )
    _ = parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Polite delay in seconds between companies (default: 0.5).",
    )
    _ = parser.add_argument(
        "--no-playwright",
        action="store_true",
        help="Disable Playwright fallback for JS-rendered pages.",
    )
    _ = parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and optimize Impressum text without calling DeepSeek or updating DB.",
    )
    return parser.parse_args(argv)


def _parse_status_arg(value: str | None) -> int | str | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in ("null", "none", "pending"):
        return "null"
    try:
        return int(lowered)
    except ValueError as exc:
        raise SystemExit(f"Invalid --status value '{value}': expected integer 0-5 or 'null'") from exc


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    status_filter = _parse_status_arg(args.status)

    init_db()
    companies = get_companies_for_enrichment(
        company_name=args.company,
        status=status_filter,
        limit=args.limit,
    )
    if not companies:
        logger.info("No matching companies to process.")
        return 0

    logger.info("Selected %d compan%s for enrichment.", len(companies), "y" if len(companies) == 1 else "ies")
    session = requests.Session()
    try:
        for idx, company in enumerate(companies):
            if idx > 0 and args.delay > 0:
                time.sleep(args.delay)
            _ = enrich_company(
                company,
                session=session,
                use_playwright_fallback=not args.no_playwright,
                dry_run=args.dry_run,
            )
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
