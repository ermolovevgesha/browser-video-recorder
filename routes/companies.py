from __future__ import annotations

import csv
import io
import logging
from typing import Any

from flask import Blueprint, jsonify, render_template, request

from storage.companies import get_all_companies, get_company, upsert_companies

logger = logging.getLogger(__name__)

companies_bp = Blueprint("companies", __name__)


def _opt_csv_field(row: dict[str, str], key: str) -> str | None:
    val = row.get(key)
    if val is None:
        return None
    cleaned = val.strip()
    return cleaned if cleaned else None


@companies_bp.route("/upload_companies", methods=["GET"])
def upload_companies_form():
    return render_template("upload_companies.html"), 200


@companies_bp.route("/upload_companies", methods=["POST"])
def upload_companies():
    if "file" not in request.files:
        return jsonify({"error": "Missing 'file' in multipart/form-data"}), 400

    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "Empty or missing file"}), 400

    try:
        raw_text = file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify({"error": "CSV file must be UTF-8 encoded"}), 400

    if not raw_text.strip():
        return jsonify({"error": "Uploaded CSV file is empty"}), 400

    reader = csv.DictReader(io.StringIO(raw_text), delimiter=";", quotechar='"')
    if not reader.fieldnames:
        return jsonify({"error": "CSV header row is missing"}), 400

    normalized_headers = [h.strip() for h in reader.fieldnames if h is not None]
    if "Company Name" not in normalized_headers:
        return jsonify({"error": "CSV must contain a 'Company Name' column"}), 400

    parsed_rows: list[dict[str, Any]] = []
    for line_no, raw_row in enumerate(reader, start=2):
        row = {
            (k.strip() if k is not None else ""): (v if isinstance(v, str) else "")
            for k, v in raw_row.items()
        }
        if not any(v.strip() for v in row.values()):
            continue

        company_name = _opt_csv_field(row, "Company Name")
        if not company_name:
            return jsonify({"error": f"Row {line_no}: missing 'Company Name'"}), 400

        parsed_rows.append(
            {
                "company_name": company_name,
                "language": _opt_csv_field(row, "Language"),
                "country": _opt_csv_field(row, "Country"),
                "postcode": _opt_csv_field(row, "Postcode"),
                "city": _opt_csv_field(row, "City"),
                "street": _opt_csv_field(row, "Street"),
                "house_no": _opt_csv_field(row, "HouseNo"),
                "web": _opt_csv_field(row, "Web"),
            }
        )

    result = upsert_companies(parsed_rows)
    logger.info(
        "CSV import finished: imported=%d updated=%d total_rows=%d",
        result["imported"],
        result["updated"],
        result["total_rows"],
    )
    return jsonify(result), 200


@companies_bp.route("/companies", methods=["GET"])
def list_companies():
    status_param = request.args.get("status")
    status_filter: int | None = None
    if status_param is not None and status_param.strip() != "":
        try:
            status_filter = int(status_param.strip())
        except ValueError:
            return jsonify({"error": "Query parameter 'status' must be an integer"}), 400

    return jsonify(get_all_companies(status=status_filter)), 200


@companies_bp.route("/companies/<path:company_name>", methods=["GET"])
def get_company_endpoint(company_name: str):
    company = get_company(company_name)
    if not company:
        return jsonify({"error": f"Company '{company_name}' not found"}), 404
    return jsonify(company), 200
