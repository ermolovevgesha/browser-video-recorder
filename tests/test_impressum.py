from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import requests

import impressum
from storage.companies import CompanyStatus


def test_normalize_url():
    assert impressum.normalize_url(None) is None
    assert impressum.normalize_url("   ") is None
    assert impressum.normalize_url("nutrimmun.de") == "https://nutrimmun.de"
    assert impressum.normalize_url("https://example.de/path") == "https://example.de/path"
    assert impressum.normalize_url("http://example.de") == "http://example.de"


def test_find_impressum_url_text_priority_and_href_fallback():
    html_text = """
    <html><body>
      <a href="/about-imprint-info">About</a>
      <footer><a href="/legal/kontakt.html">  Impressum </a></footer>
    </body></html>
    """
    assert impressum.find_impressum_url(html_text, "https://example.de/home/") == "https://example.de/legal/kontakt.html"

    html_href = """
    <html><body>
      <a href="../rechtliches/imprint.html">Legal Notice</a>
    </body></html>
    """
    assert impressum.find_impressum_url(html_href, "https://example.de/en/index.html") == "https://example.de/rechtliches/imprint.html"

    html_none = '<html><body><a href="/contact">Contact</a></body></html>'
    assert impressum.find_impressum_url(html_none, "https://example.de") is None


def test_extract_visible_text_removes_noise_tags():
    html = """
    <html>
      <head>
        <title>Impressum – Shop</title>
        <style>.a { color: red; }</style>
        <script>alert(1)</script>
      </head>
      <body>
        <header><nav>Home Shop</nav></header>
        <main>
          <h1>Impressum</h1>
          <p>ACME   GmbH</p>
          <p>Musterstraße 12</p>
          <p>E-Mail: <a data-cfemail="2a43444c456a4b49474f044e4f">[email&#160;protected]</a></p>
        </main>
        <form><input name="q"></form>
        <footer>Copyright 2026</footer>
      </body>
    </html>
    """
    text = impressum.extract_visible_text(html)
    assert "Impressum – Shop" not in text
    assert "Impressum" in text
    assert "ACME GmbH" in text
    assert "Musterstraße 12" in text
    assert "info@acme.de" in text
    assert "alert(1)" not in text
    assert "Home Shop" not in text
    assert "Copyright 2026" not in text


def test_optimize_text_under_threshold_unchanged():
    short = "Impressum\nACME GmbH\nTel: +49 30 123456\nE-Mail: info@acme.de"
    out, stats = impressum.optimize_text(short, max_chars=8000)
    assert out == short
    assert stats["strategy"] == "unchanged"
    assert stats["before_chars"] == len(short)
    assert stats["after_chars"] == len(short)


def test_optimize_text_sandwich_cuts_head_and_tail():
    head_noise = ("Impressum\nNavigation item\nHero marketing copy about products.\n" * 120)
    legal_block = (
        "Impressum\n"
        "Angaben gemäß § 5 DDG\n"
        "ACME GmbH\n"
        "Hauptstraße 12\n"
        "10115 Berlin\n"
        "Telefon: +49 30 9876543\n"
        "E-Mail: info@acme-gmbh.de\n"
        "Handelsregister: Amtsgericht Charlottenburg, HRB 123456\n"
        "USt-IdNr.: DE 123 456 789\n"
        "Vertreten durch: Max Mustermann\n"
    )
    tail_noise = (
        ("Long legal disclaimer and product catalog text.\n" * 120)
        + "Kundenservice Hotline: +49 30 9999999\n"
    )
    full_text = head_noise + legal_block + tail_noise

    assert len(full_text) > 8000
    out, stats = impressum.optimize_text(full_text, max_chars=8000)
    assert stats["strategy"] == "sandwich"
    assert len(out) < 1200
    assert stats["after_tokens"] < stats["before_tokens"]
    assert "ACME GmbH" in out
    assert "HRB 123456" in out
    assert "DE 123 456 789" in out
    assert "Max Mustermann" in out
    assert "Kundenservice Hotline" not in out


def test_optimize_text_head_tail_window_fallback():
    legal_start = "Impressum\nACME GmbH\nHauptstraße 1\n10115 Berlin\nTel: +49 30 111222\n"
    huge_middle = ("Random paragraph text in the middle. " * 15 + "\nE-Mail: a@b.de\n") * 8
    legal_end = "USt-IdNr.: DE123456789\n"
    text = legal_start + huge_middle + legal_end

    out, stats = impressum.optimize_text(text, max_chars=2000)
    assert len(out) <= 2000
    assert "head_tail_window" in stats["strategy"]
    assert "ACME GmbH" in out
    assert "DE123456789" in out


def test_call_deepseek_retries_on_blank_and_succeeds():
    session = MagicMock()
    blank_resp = MagicMock(status_code=200)
    blank_resp.raise_for_status.return_value = None
    blank_resp.json.return_value = {"choices": [{"message": {"content": "   "}}]}

    ok_payload = {"company_name": "ACME GmbH", "postal_address": {}}
    ok_resp = MagicMock(status_code=200)
    ok_resp.raise_for_status.return_value = None
    ok_resp.json.return_value = {"choices": [{"message": {"content": json.dumps(ok_payload)}}]}

    session.post.side_effect = [blank_resp, ok_resp]
    result = impressum.call_deepseek(
        "Impressum text",
        api_key="test-key",
        session=session,
        max_retries=3,
        backoff_base=0.0,
    )
    assert result == ok_payload
    assert session.post.call_count == 2


def test_call_deepseek_raises_parse_error_after_retries():
    session = MagicMock()
    bad_resp = MagicMock(status_code=200)
    bad_resp.raise_for_status.return_value = None
    bad_resp.json.return_value = {"choices": [{"message": {"content": "not json"}}]}
    session.post.return_value = bad_resp

    with pytest.raises(impressum.DeepSeekParseError):
        impressum.call_deepseek(
            "Impressum text",
            api_key="test-key",
            session=session,
            max_retries=2,
            backoff_base=0.0,
        )


def test_call_deepseek_raises_api_error_on_http_failure():
    session = MagicMock()
    session.post.side_effect = requests.ConnectionError("network down")

    with pytest.raises(impressum.DeepSeekAPIError):
        impressum.call_deepseek("Impressum text", api_key="test-key", session=session)


def test_classify_and_map_all_statuses():
    # Status 2: empty / all-null JSON must NOT fall back to csv_company_name before checking
    status_empty, mapped_empty = impressum.classify_and_map({}, "ACME GmbH")
    assert status_empty == CompanyStatus.PARSING_FAILED
    assert mapped_empty["legal_name"] is None

    status_nulls, _ = impressum.classify_and_map(
        {
            "company_name": None,
            "postal_address": {"street": None, "house_no": None, "postcode": None, "city": None, "country": None},
            "handelsregister": None,
            "vat_number": None,
            "phone": None,
            "email": None,
            "responsible_person": None,
        },
        "ACME GmbH",
    )
    assert status_nulls == CompanyStatus.PARSING_FAILED

    # Status 3: some fields detected, but house_no missing
    status_inc, mapped_inc = impressum.classify_and_map(
        {
            "company_name": None,
            "postal_address": {"street": "Hauptstraße", "house_no": None, "postcode": "10115", "city": "Berlin", "country": None},
            "email": "INFO@ACME.DE",
        },
        "ACME GmbH",
    )
    assert status_inc == CompanyStatus.ADDRESS_INCOMPLETE
    assert mapped_inc["legal_name"] == "ACME GmbH"  # fallback applied
    assert mapped_inc["email"] == "info@acme.de"

    # Status 0: address complete + normalization of house_no, phone, VAT, and duplicate names
    status_ok, mapped_ok = impressum.classify_and_map(
        {
            "company_name": "nutrimmun GmbH",
            "postal_address": {
                "street": "Alter Steinweg 5",
                "house_no": None,
                "postcode": "48143",
                "city": "Münster",
                "country": None,
            },
            "vat_number": "DE 217 733 235",
            "phone": "0251 135 66-0",
            "email": "info@nutrimmun.de",
            "responsible_person": "Sabine Harders, Tobias Eiling, Sabine Harders",
        },
        "nutrimmun GmbH",
    )
    assert status_ok == CompanyStatus.OK
    assert mapped_ok["postal_address"] == "Alter Steinweg 5, 48143 Münster"
    assert mapped_ok["vat_number"] == "DE217733235"
    assert mapped_ok["phone"] == "+49 251 135 66-0"
    assert mapped_ok["responsible_person"] == "Sabine Harders, Tobias Eiling"
