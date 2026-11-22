from __future__ import annotations

import pytest
import requests

import enrich_companies
from db import CompanyStatus
from impressum import DeepSeekAPIError, DeepSeekParseError


def test_enrich_company_all_status_paths(monkeypatch: pytest.MonkeyPatch):
    status_writes: list[tuple] = []
    enrichment_writes: list[dict] = []

    monkeypatch.setattr(
        enrich_companies,
        "update_company_status",
        lambda cid, st, impressum_url=None: status_writes.append((cid, st, impressum_url)),
    )
    monkeypatch.setattr(
        enrich_companies,
        "update_company_enrichment",
        lambda **kwargs: enrichment_writes.append(kwargs),
    )

    # 5: NO_WEBSITE
    st = enrich_companies.enrich_company({"id": 1, "company_name": "NoWeb", "web": "  "}, use_playwright_fallback=False)
    assert st == CompanyStatus.NO_WEBSITE
    assert status_writes[-1] == (1, CompanyStatus.NO_WEBSITE, None)

    # 1: NO_IMPRESSUM_LINK_FOUND
    monkeypatch.setattr(enrich_companies, "fetch_page", lambda url, session=None: ("<html><a href='/home'>Home</a></html>", url))
    st = enrich_companies.enrich_company({"id": 2, "company_name": "NoImp", "web": "example.de"}, use_playwright_fallback=False)
    assert st == CompanyStatus.NO_IMPRESSUM_LINK_FOUND
    assert status_writes[-1] == (2, CompanyStatus.NO_IMPRESSUM_LINK_FOUND, None)

    # 4: Transport ERROR
    def fail_fetch(url, session=None):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(enrich_companies, "fetch_page", fail_fetch)
    st = enrich_companies.enrich_company({"id": 3, "company_name": "Broken", "web": "broken.de"}, use_playwright_fallback=False)
    assert st == CompanyStatus.ERROR
    assert status_writes[-1] == (3, CompanyStatus.ERROR, None)

    # 0: OK
    def ok_fetch(url, session=None):
        if url.endswith("/impressum"):
            return ("<html><body><main>Impressum ACME GmbH Hauptstr 1 10115 Berlin</main></body></html>", url)
        return ("<html><body><a href='/impressum'>Impressum</a></body></html>", "https://acme.de")

    monkeypatch.setattr(enrich_companies, "fetch_page", ok_fetch)
    monkeypatch.setattr(
        enrich_companies,
        "call_deepseek",
        lambda text, session=None: {
            "company_name": "ACME GmbH",
            "postal_address": {
                "street": "Hauptstr",
                "house_no": "1",
                "postcode": "10115",
                "city": "Berlin",
                "country": "Germany",
            },
            "handelsregister": "HRB 12345",
            "vat_number": "DE123456789",
            "phone": "+49 30 12345",
            "email": "info@acme.de",
            "responsible_person": "Max Mustermann",
        },
    )
    st = enrich_companies.enrich_company({"id": 4, "company_name": "ACME", "web": "acme.de"}, use_playwright_fallback=False)
    assert st == CompanyStatus.OK
    assert enrichment_writes[-1]["status"] == CompanyStatus.OK
    assert enrichment_writes[-1]["impressum_url"] == "https://acme.de/impressum"

    # 2: PARSING_FAILED on DeepSeekParseError
    monkeypatch.setattr(
        enrich_companies,
        "call_deepseek",
        lambda text, session=None: (_ for _ in ()).throw(DeepSeekParseError("blank")),
    )
    st = enrich_companies.enrich_company({"id": 5, "company_name": "BadParse", "web": "acme.de"}, use_playwright_fallback=False)
    assert st == CompanyStatus.PARSING_FAILED
    assert enrichment_writes[-1]["status"] == CompanyStatus.PARSING_FAILED

    # 4: ERROR on DeepSeekAPIError (preserves resolved impressum_url)
    monkeypatch.setattr(
        enrich_companies,
        "call_deepseek",
        lambda text, session=None: (_ for _ in ()).throw(DeepSeekAPIError("500")),
    )
    st = enrich_companies.enrich_company({"id": 6, "company_name": "ApiErr", "web": "acme.de"}, use_playwright_fallback=False)
    assert st == CompanyStatus.ERROR
    assert status_writes[-1] == (6, CompanyStatus.ERROR, "https://acme.de/impressum")
