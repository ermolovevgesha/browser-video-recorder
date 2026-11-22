from __future__ import annotations

import io
from urllib.parse import quote

import pytest

import web_service


class FakeCompanyStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.seq = 0

    def upsert_companies(self, rows: list[dict]) -> dict[str, int]:
        imported = 0
        updated = 0
        for row in rows:
            name = row["company_name"]
            if name in self.rows:
                existing = self.rows[name]
                for col in ("language", "country", "postcode", "city", "street", "house_no", "web"):
                    existing[col] = row.get(col)
                updated += 1
            else:
                self.seq += 1
                self.rows[name] = {
                    "id": self.seq,
                    "company_name": name,
                    "language": row.get("language"),
                    "country": row.get("country"),
                    "postcode": row.get("postcode"),
                    "city": row.get("city"),
                    "street": row.get("street"),
                    "house_no": row.get("house_no"),
                    "web": row.get("web"),
                    "status": None,
                    "impressum_url": None,
                    "legal_name": None,
                    "postal_address": None,
                    "handelsregister": None,
                    "vat_number": None,
                    "phone": None,
                    "email": None,
                    "responsible_person": None,
                    "raw_json": None,
                }
                imported += 1
        return {"imported": imported, "updated": updated, "total_rows": imported + updated}

    def get_all_companies(self, status: int | None = None) -> list[dict]:
        items = list(self.rows.values())
        if status is not None:
            items = [r for r in items if r["status"] == status]
        return items

    def get_company(self, company_name: str) -> dict | None:
        return self.rows.get(company_name)


def test_companies_csv_upload_idempotent_and_endpoints(monkeypatch: pytest.MonkeyPatch):
    store = FakeCompanyStore()
    monkeypatch.setattr(web_service, "upsert_companies", store.upsert_companies)
    monkeypatch.setattr(web_service, "get_all_companies", store.get_all_companies)
    monkeypatch.setattr(web_service, "get_company", store.get_company)

    client = web_service.app.test_client()

    # GET /upload_companies renders minimal form
    form_resp = client.get("/upload_companies")
    assert form_resp.status_code == 200
    html = form_resp.get_data(as_text=True)
    assert 'action="/upload_companies"' in html
    assert 'href="/companies"' in html

    # POST /upload_companies initial import
    csv_bytes = (
        'URL;Company Name;Language;Country;Postcode;City;Street;HouseNo;Web\n'
        '"";"nutrimmun GmbH";"German";"Germany";"";"";"";"";"nutrimmun.de"\n'
        '"";"Barolo & Champagne GbR";"German";"Germany";"";"";"";"";"baroloandchampagne.com"\n'
    ).encode("utf-8")

    resp1 = client.post(
        "/upload_companies",
        data={"file": (io.BytesIO(csv_bytes), "companies.csv")},
        content_type="multipart/form-data",
    )
    assert resp1.status_code == 200
    assert resp1.get_json() == {"imported": 2, "updated": 0, "total_rows": 2}

    # Simulate enrichment on one row, then re-import CSV -> enrichment must survive
    store.rows["nutrimmun GmbH"]["status"] = 0
    store.rows["nutrimmun GmbH"]["legal_name"] = "nutrimmun GmbH"
    store.rows["nutrimmun GmbH"]["vat_number"] = "DE123456789"

    resp2 = client.post(
        "/upload_companies",
        data={"file": (io.BytesIO(csv_bytes), "companies.csv")},
        content_type="multipart/form-data",
    )
    assert resp2.status_code == 200
    assert resp2.get_json() == {"imported": 0, "updated": 2, "total_rows": 2}
    assert store.rows["nutrimmun GmbH"]["status"] == 0
    assert store.rows["nutrimmun GmbH"]["vat_number"] == "DE123456789"

    # GET /companies returns a JSON array + supports ?status=N
    list_all = client.get("/companies")
    assert list_all.status_code == 200
    data = list_all.get_json()
    assert isinstance(data, list)
    assert len(data) == 2

    list_ok = client.get("/companies?status=0")
    assert list_ok.status_code == 200
    assert [c["company_name"] for c in list_ok.get_json()] == ["nutrimmun GmbH"]

    bad_status = client.get("/companies?status=abc")
    assert bad_status.status_code == 400

    # GET /companies/<company_name> handles special chars like '&'
    encoded = quote("Barolo & Champagne GbR", safe="")
    one = client.get(f"/companies/{encoded}")
    assert one.status_code == 200
    assert one.get_json()["web"] == "baroloandchampagne.com"

    missing = client.get("/companies/Unknown%20Corp")
    assert missing.status_code == 404
