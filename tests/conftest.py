from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from playwright.sync_api import Browser, sync_playwright


def _find_chromium_executable() -> str | None:
    for candidate in (
        "/run/current-system/sw/bin/chromium",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
    ):
        if Path(candidate).exists():
            return candidate
    return None


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    launch_args: dict[str, object] = {"headless": True, "args": ["--no-sandbox"]}
    exe = _find_chromium_executable()
    if exe:
        launch_args["executable_path"] = exe

    with sync_playwright() as pw:
        br = pw.chromium.launch(**launch_args)
        yield br
        br.close()
