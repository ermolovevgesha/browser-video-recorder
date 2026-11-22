from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from db import CompanyStatus

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 8000
HEAD_WINDOW = 1800
CLUSTER_GAP = 900
TAIL_MARGIN = 400

DEFAULT_DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

SYSTEM_PROMPT = """\
You extract legally required company information ("Impressum" / "Imprint") from
German-language website text.

Return ONLY a valid JSON object (no markdown fences, no commentary) that parses
with json.loads. The JSON must have exactly these keys:

{
  "company_name": "<string or null>",
  "postal_address": {
    "street": "<string or null>",
    "house_no": "<string or null>",
    "postcode": "<string or null>",
    "city": "<string or null>",
    "country": "<string or null>"
  },
  "handelsregister": "<string or null>",
  "vat_number": "<string or null>",
  "phone": "<string or null>",
  "email": "<string or null>",
  "responsible_person": "<string or null>"
}

Rules:
- Extract ONLY information actually present in the text; use null when absent.
- Do not invent or guess values.
- phone: keep the international format when possible (e.g. +49 ...).
- vat_number: German VAT ids look like DE followed by 9 digits (remove spaces, e.g. DE217733235).
- email: lowercase.
- responsible_person: the named person or role legally responsible for the
  content (e.g. Geschaeftsfuehrer, Vorstand, "inhaltlich verantwortlich"); join multiple with ", ".
- postal_address.house_no is only the building number; street is the name without it.
- Output valid JSON only.
"""

HEAD_ANCHOR_RE = re.compile(
    r"(?:angaben\s+gem[äa]ß\s*§\s*5\s*(?:tmg|ddg)|anbieterkennzeichnung|anbieterkennung"
    r"|diensteanbieter|\banbieter\b|impressum|\bimprint\b)",
    re.IGNORECASE,
)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

SIGNAL_RES = (
    re.compile(r"\bDE(?:\s*\d){9}(?!\d)", re.IGNORECASE),
    re.compile(r"\bHR[AB]\s*\d+\b", re.IGNORECASE),
    re.compile(r"\b(?:Registergericht|Amtsgericht)\b", re.IGNORECASE),
    EMAIL_RE,
    re.compile(
        r"\b(?:Inhaltlich\s+verantwortlich|Vertreten\s+durch|Gesch[äa]ftsf[üu]hr\w*"
        r"|Geschaeftsfuehr\w*|Gesellschafter|Verantwortlich)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(?<!\d)(?:\+|00)\s?49[\s/()\-]*\d"),
    re.compile(r"\b(?:Telefon|Tel\.?|Mobil|Fax)\b[:\s]*0\d{2,4}", re.IGNORECASE),
    re.compile(r"(?:^|\n)T\s+0\d{2,4}\b"),
)

BOILERPLATE_LINE_RE = re.compile(
    r"^(?:"
    r"datenschutz(?:erkl[äa]rung|richtlinie)?|agb|allgemeine\s+gesch[äa]ftsbedingungen"
    r"|men[üu](?:\s+schlie(?:ß|ss)en)?|menu|suche|search|warenkorb|cart|login|einloggen|anmelden|registrieren"
    r"|cookie(?:s|\s*[-]?\s*(?:einstellungen?|hinweis|richtlinie|policy|settings))?"
    r"|akzeptieren|alle\s+(?:cookies\s+)?akzeptieren|ablehnen|nur\s+(?:technisch\s+)?notwendige|konfigurieren"
    r"|©\s*\d{4}.*|\(c\)\s*\d{4}.*|copyright\s*©?\s*\d{4}.*"
    r"|deutsch|english|englisch|français|französisch|italiano|español|de|en|fr|it|es"
    r"|widerrufsbelehrung|versand(?:kosten)?|zahlung(?:sarten)?|kontakt|newsletter|start|home|zurück"
    r"|zum\s+hauptinhalt\s+springen|zur\s+suche\s+springen|zur\s+hauptnavigation\s+springen|impressum\s+anzeigen"
    r")$",
    re.IGNORECASE,
)

FOOTER_LINE_RE = re.compile(
    r"^(?:datenschutz|agb\b|cookie|©|\(c\)|allgemeine\s+gesch[äa]ftsbedingungen)",
    re.IGNORECASE,
)

HOUSE_NO_RE = re.compile(r"^(.*\D)\s+(\d+\s*[a-zA-Z]?)$")


class DeepSeekAPIError(RuntimeError):
    """Transport, HTTP, or configuration error while calling DeepSeek."""


class DeepSeekParseError(RuntimeError):
    """DeepSeek returned empty or unparseable JSON even after retries."""


def normalize_url(web: str | None) -> str | None:
    if not web:
        return None
    cleaned = web.strip()
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if not parsed.scheme:
        return f"https://{cleaned}"
    return cleaned


def _make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def fetch_page(
    url: str,
    session: requests.Session | None = None,
    timeout: int = 20,
    attempts: int = 3,
) -> tuple[str, str]:
    client = session or requests
    last_exc: requests.RequestException | None = None
    for attempt in range(attempts):
        try:
            resp = client.get(
                url,
                headers={
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
                },
                timeout=timeout,
                allow_redirects=True,
            )
            resp.raise_for_status()
            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text, resp.url
        except requests.RequestException as exc:
            last_exc = exc
            if attempt + 1 < attempts:
                time.sleep(1 + attempt)
    assert last_exc is not None
    raise last_exc


def fetch_page_playwright(url: str, timeout_ms: int = 20000) -> tuple[str, str]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page(user_agent=DEFAULT_USER_AGENT)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1500)
            return page.content(), page.url
        finally:
            browser.close()


def find_impressum_url(html: str, base_url: str) -> str | None:
    soup = _make_soup(html)
    anchors = soup.find_all("a", href=True)

    # Pass 1: exact visible text match ("impressum" or "imprint")
    for tag in anchors:
        href = (tag.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        visible = " ".join(tag.get_text(" ", strip=True).split()).casefold()
        if visible in ("impressum", "imprint"):
            return urljoin(base_url, href)

    # Pass 2: fallback to href containing "impressum" or "imprint"
    for tag in anchors:
        href = (tag.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        lowered = href.casefold()
        if "impressum" in lowered or "imprint" in lowered:
            return urljoin(base_url, href)

    return None


def _decode_cfemail(encoded: str) -> str | None:
    hex_str = (encoded or "").strip()
    if len(hex_str) < 4 or len(hex_str) % 2:
        return None
    try:
        raw = bytes.fromhex(hex_str)
        key = raw[0]
        decoded = unquote(bytes(b ^ key for b in raw[1:]).decode("utf-8")).strip()
        return decoded if EMAIL_RE.fullmatch(decoded) else None
    except (ValueError, UnicodeDecodeError):
        return None


def _reveal_cf_emails(root: BeautifulSoup) -> None:
    for cf_tag in root.select("[data-cfemail]"):
        decoded = _decode_cfemail(str(cf_tag.get("data-cfemail") or ""))
        if decoded:
            cf_tag.replace_with(decoded)
    for link in root.find_all("a", href=True):
        href = str(link["href"])
        if "email-protection#" not in href:
            continue
        encoded = href.split("email-protection#", 1)[1].split("&", 1)[0]
        decoded = _decode_cfemail(encoded)
        if decoded:
            link.replace_with(decoded)


def _html_to_text(html: str, drop_chrome: bool) -> str:
    soup = _make_soup(html)
    root = soup.body or soup
    _reveal_cf_emails(root)

    drop_tags = ["script", "style", "noscript", "svg"]
    if drop_chrome:
        drop_tags.extend(["nav", "header", "footer", "form"])
    for tag in root(drop_tags):
        tag.decompose()

    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in root.get_text(separator="\n").splitlines()]
    return "\n".join(line for line in lines if line)


def _signal_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for rx in SIGNAL_RES:
        for match in rx.finditer(text):
            spans.append((match.start(), match.end()))
    spans.sort()
    return spans


def extract_visible_text(html: str) -> str:
    trimmed = _html_to_text(html, drop_chrome=True)
    if _signal_spans(trimmed):
        return trimmed
    # Fallback if a site placed its entire Impressum inside <footer>/<header>
    full = _html_to_text(html, drop_chrome=False)
    return full if _signal_spans(full) else trimmed


def estimate_tokens(text: str) -> int:
    return len(text) // 4


def strip_boilerplate_lines(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        low = stripped.casefold()
        if BOILERPLATE_LINE_RE.match(stripped):
            continue
        if "cookie" in low and len(low) < 200:
            continue
        kept.append(stripped)
    return "\n".join(kept)


def _choose_head(text: str, spans: list[tuple[int, int]]) -> int | None:
    best_start: int | None = None
    best_score = 0
    for head in HEAD_ANCHOR_RE.finditer(text):
        window_end = head.end() + HEAD_WINDOW
        score = sum(1 for start, _ in spans if head.start() <= start <= window_end)
        if score > best_score or (score == best_score and score > 0 and best_start is not None and head.start() > best_start):
            best_start = head.start()
            best_score = score
    return best_start


def _cluster_end(spans: list[tuple[int, int]], head_start: int) -> int | None:
    in_block = [(s, e) for s, e in spans if s >= head_start]
    if not in_block:
        return None
    last_end = in_block[0][1]
    for start, end in in_block[1:]:
        if start <= last_end + CLUSTER_GAP:
            last_end = max(last_end, end)
        else:
            break
    return last_end


def _start_on_heading(text: str, head_start: int) -> int:
    line_start = text.rfind("\n", 0, head_start) + 1
    prev_start = text.rfind("\n", 0, max(line_start - 1, 0)) + 1
    prev_line = text[prev_start:line_start].strip()
    if prev_line and len(prev_line) <= 24 and "http" not in prev_line.casefold():
        return prev_start
    return line_start


def _end_on_line(text: str, cluster_end: int, margin: int) -> int:
    limit = min(len(text), cluster_end + margin)
    line_break = text.rfind("\n", cluster_end, limit)
    end = line_break if line_break != -1 else limit

    # Stop early if a footer link list (Datenschutz / AGB / ©) starts inside the margin
    pos = cluster_end
    while pos < end:
        nxt = text.find("\n", pos, end)
        line = text[pos:end] if nxt == -1 else text[pos:nxt]
        if pos > cluster_end + 40 and FOOTER_LINE_RE.match(line.strip()):
            return pos
        if nxt == -1:
            break
        pos = nxt + 1
    return end


def optimize_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> tuple[str, dict[str, Any]]:
    before_chars = len(text)
    before_tokens = estimate_tokens(text)

    if before_chars <= max_chars:
        return text, {
            "before_chars": before_chars,
            "after_chars": before_chars,
            "before_tokens": before_tokens,
            "after_tokens": before_tokens,
            "strategy": "unchanged",
        }

    cleaned = strip_boilerplate_lines(text)
    spans = _signal_spans(cleaned)
    head_start = _choose_head(cleaned, spans)
    cluster_end = _cluster_end(spans, head_start) if head_start is not None else None

    candidate: str | None = None
    strategy = "relaxed_full"

    if head_start is not None and cluster_end is not None:
        start = _start_on_heading(cleaned, head_start)
        end = _end_on_line(cleaned, cluster_end, TAIL_MARGIN)
        sliced = cleaned[start:end].strip()
        if HEAD_ANCHOR_RE.search(sliced) and _signal_spans(sliced):
            candidate = sliced
            strategy = "sandwich"

    if candidate is None:
        if HEAD_ANCHOR_RE.search(cleaned) and spans:
            candidate = cleaned
            strategy = "relaxed_cleaned"
        else:
            candidate = text
            strategy = "relaxed_full"

    if len(candidate) > max_chars:
        sep = "\n...\n"
        half = (max_chars - len(sep)) // 2
        candidate = f"{candidate[:half].rstrip()}{sep}{candidate[-half:].lstrip()}"
        strategy = f"{strategy}+head_tail_window"

    return candidate, {
        "before_chars": before_chars,
        "after_chars": len(candidate),
        "before_tokens": before_tokens,
        "after_tokens": estimate_tokens(candidate),
        "strategy": strategy,
    }


def _parse_json_object(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def call_deepseek(
    optimized_text: str,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    max_retries: int = 3,
    timeout: int = 60,
    session: requests.Session | None = None,
    backoff_base: float = 1.0,
) -> dict[str, Any]:
    key = api_key or os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise DeepSeekAPIError("DEEPSEEK_API_KEY is not set")

    model_name = model or os.environ.get("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)
    endpoint = base_url or os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_URL)
    client = session or requests
    last_parse_err: str | None = None
    last_api_err: str | None = None

    for attempt in range(1, max_retries + 1):
        try:
            resp = client.post(
                endpoint,
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                "Extract the Impressum data from the text below and return JSON:\n\n"
                                f"{optimized_text}"
                            ),
                        },
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 1024,
                    "temperature": 0,
                },
                timeout=timeout,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                last_api_err = f"HTTP {resp.status_code}"
                logger.warning("DeepSeek %s (attempt %d/%d)", last_api_err, attempt, max_retries)
                if attempt < max_retries:
                    time.sleep(backoff_base * (2 ** (attempt - 1)))
                    continue
                raise DeepSeekAPIError(f"DeepSeek request failed: {last_api_err}")
            resp.raise_for_status()
            payload = resp.json()
        except DeepSeekAPIError:
            raise
        except requests.RequestException as exc:
            raise DeepSeekAPIError(f"DeepSeek request failed: {exc}") from exc
        except ValueError as exc:
            raise DeepSeekAPIError(f"DeepSeek returned invalid HTTP JSON envelope: {exc}") from exc

        usage = payload.get("usage")
        if isinstance(usage, dict) and usage:
            logger.info(
                "DeepSeek API usage: prompt_tokens=%s completion_tokens=%s",
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
            )

        choices = payload.get("choices") or []
        content = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                content = message["content"]

        parsed = _parse_json_object(content)
        if parsed is not None:
            return parsed

        last_parse_err = "Empty or non-JSON content in DeepSeek response"
        logger.warning("DeepSeek JSON parse attempt %d/%d failed: %s", attempt, max_retries, last_parse_err)
        if attempt < max_retries:
            time.sleep(backoff_base * (2 ** (attempt - 1)))

    raise DeepSeekParseError(last_parse_err or "DeepSeek returned unparseable output")


def _clean_str(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned if cleaned else None


def _split_house_no(street: str | None, house_no: str | None) -> tuple[str | None, str | None]:
    if house_no or not street:
        return street, house_no
    match = HOUSE_NO_RE.match(street)
    if not match:
        return street, house_no
    name = match.group(1).strip(" ,")
    num = match.group(2).strip()
    if len(name) < 3:
        return street, house_no
    return name, num


def _normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("+") or value.startswith("00"):
        return value
    digits = re.sub(r"\D", "", value)
    if value.startswith("0") and len(digits) >= 6:
        return "+49 " + re.sub(r"^0\s*", "", value)
    return value


def _normalize_vat(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"DE(?:\s*\d){9}(?!\d)", value, re.IGNORECASE)
    if not match:
        return value
    return "DE" + re.sub(r"\D", "", match.group(0))


def _unique_names(value: str | None) -> str | None:
    if not value:
        return None
    seen: set[str] = set()
    result: list[str] = []
    for part in re.split(r"\s*,\s*", value):
        key = part.casefold()
        if not part or key in seen:
            continue
        seen.add(key)
        result.append(part)
    return ", ".join(result) or None


def format_postal_address(
    street: str | None,
    house_no: str | None,
    postcode: str | None,
    city: str | None,
    country: str | None,
) -> str | None:
    street_line = " ".join(p for p in (street, house_no) if p)
    city_line = " ".join(p for p in (postcode, city) if p)
    parts = [p for p in (street_line, city_line, country) if p]
    return ", ".join(parts) if parts else None


def classify_and_map(
    raw_json: dict[str, Any] | None,
    csv_company_name: str,
) -> tuple[CompanyStatus, dict[str, Any]]:
    if not isinstance(raw_json, dict) or not raw_json:
        return CompanyStatus.PARSING_FAILED, {
            "legal_name": None,
            "postal_address": None,
            "handelsregister": None,
            "vat_number": None,
            "phone": None,
            "email": None,
            "responsible_person": None,
            "raw_json": raw_json if isinstance(raw_json, dict) else None,
        }

    addr = raw_json.get("postal_address")
    addr_dict = addr if isinstance(addr, dict) else {}

    extracted_name = _clean_str(raw_json.get("company_name"))
    street, house_no = _split_house_no(_clean_str(addr_dict.get("street")), _clean_str(addr_dict.get("house_no")))
    postcode = _clean_str(addr_dict.get("postcode"))
    city = _clean_str(addr_dict.get("city"))
    country = _clean_str(addr_dict.get("country"))
    handelsregister = _clean_str(raw_json.get("handelsregister"))
    vat_number = _normalize_vat(_clean_str(raw_json.get("vat_number")))
    phone = _normalize_phone(_clean_str(raw_json.get("phone")))
    email = _clean_str(raw_json.get("email"))
    if email:
        email = email.casefold()
    responsible_person = _unique_names(_clean_str(raw_json.get("responsible_person")))

    all_values = (
        extracted_name,
        street,
        house_no,
        postcode,
        city,
        country,
        handelsregister,
        vat_number,
        phone,
        email,
        responsible_person,
    )
    if not any(all_values):
        return CompanyStatus.PARSING_FAILED, {
            "legal_name": None,
            "postal_address": None,
            "handelsregister": None,
            "vat_number": None,
            "phone": None,
            "email": None,
            "responsible_person": None,
            "raw_json": raw_json,
        }

    address_complete = all((postcode, city, street, house_no))
    status = CompanyStatus.OK if address_complete else CompanyStatus.ADDRESS_INCOMPLETE

    return status, {
        "legal_name": extracted_name or csv_company_name,
        "postal_address": format_postal_address(street, house_no, postcode, city, country),
        "handelsregister": handelsregister,
        "vat_number": vat_number,
        "phone": phone,
        "email": email,
        "responsible_person": responsible_person,
        "raw_json": raw_json,
    }
