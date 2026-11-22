from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from routes import companies_bp, videos_bp
from storage.connection import close_db, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"

mimetypes.add_type("video/webm", ".webm")


def create_app() -> Flask:
    flask_app = Flask(__name__)
    flask_app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
    flask_app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
    flask_app.json.sort_keys = False
    flask_app.json.ensure_ascii = False
    flask_app.teardown_appcontext(close_db)

    @flask_app.errorhandler(HTTPException)
    def handle_http_exception(e: HTTPException):
        return jsonify({"error": e.description}), e.code

    flask_app.register_blueprint(videos_bp)
    flask_app.register_blueprint(companies_bp)
    return flask_app


app = create_app()


def main() -> None:
    init_db()
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", "8000"))
    logger.info("Starting web service on %s:%d", host, port)
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
