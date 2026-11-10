from __future__ import annotations

import os
import random
import shutil
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

PATH_TO_VIDEO_FOLDER = "./video"
HEADLESS: bool = True

current_page: Page | None = None

CURSOR_SCRIPT = """(() => {
  function initCursor() {
    if (document.getElementById('cursor-layer')) return;
    const target = document.head || document.documentElement || document.body;
    if (!target) return;

    if (!document.getElementById('cursor-style')) {
      const style = document.createElement('style');
      style.id = 'cursor-style';
      style.textContent = `
        #cursor-layer {
          position: fixed;
          top: 0;
          left: 0;
          width: 0;
          height: 0;
          pointer-events: none;
          z-index: 2147483647;
          overflow: visible;
        }
        #cursor-pointer {
          position: absolute;
          top: 0;
          left: 0;
          width: 64px;
          height: 64px;
          margin: -16px 0 0 -23px;
          pointer-events: none;
          transform: translate(0, 0);
          will-change: transform;
          opacity: 0;
          transition: opacity 0.15s ease;
        }
        .click-ripple {
          position: absolute;
          border-radius: 50%;
          border: 3px solid rgba(255, 193, 7, 0.9);
          background: rgba(255, 235, 59, 0.35);
          pointer-events: none;
          transform: translate(-50%, -50%) scale(0.1);
          animation: ripple-effect 0.55s cubic-bezier(0.1, 0.8, 0.3, 1) forwards;
        }
        @keyframes ripple-effect {
          0% { transform: translate(-50%, -50%) scale(0.1); opacity: 0.9; }
          100% { transform: translate(-50%, -50%) scale(1.4); opacity: 0; }
        }
        .callout-bubble {
          position: fixed;
          z-index: 2147483646;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          font-size: 16pt;
          font-weight: 600;
          padding: 12px 24px;
          border-radius: 8px;
          box-shadow: 0 4px 14px rgba(0,0,0,0.25);
          pointer-events: none;
          opacity: 0;
          max-width: 80vw;
          width: max-content;
          left: 50%;
          bottom: 40px;
          transform: translateX(-50%);
        }
      `;
      target.appendChild(style);
    }

    const container = document.createElement('div');
    container.id = 'cursor-layer';

    const cursor = document.createElement('div');
    cursor.id = 'cursor-pointer';
    cursor.innerHTML = `
      <svg width="64" height="64" viewBox="0 0 64 64" fill="none">
        <circle cx="32" cy="32" r="30" fill="rgba(255, 220, 40, 0.45)" />
        <g transform="translate(23, 16)">
          <path d="M0 0 L0 26 L6.5 19.5 L12 32 L15.5 30.5 L10 18 L18 18 Z" 
                fill="#ffffff" stroke="#000000" stroke-width="1.8" stroke-linejoin="round"/>
        </g>
      </svg>
    `;
    container.appendChild(cursor);

    const savedX = sessionStorage.getItem('cursor_x');
    const savedY = sessionStorage.getItem('cursor_y');
    if (savedX !== null && savedY !== null) {
      cursor.style.transform = `translate(${savedX}px, ${savedY}px)`;
      cursor.style.opacity = '1';
    }

    const bodyTarget = document.body || document.documentElement;
    if (bodyTarget) {
      bodyTarget.appendChild(container);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initCursor);
  } else {
    initCursor();
  }

  window.addEventListener('mousemove', (e) => {
    initCursor();
    const ptr = document.getElementById('cursor-pointer');
    if (ptr) {
      ptr.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
      ptr.style.opacity = '1';
    }
    try {
      sessionStorage.setItem('cursor_x', e.clientX);
      sessionStorage.setItem('cursor_y', e.clientY);
    } catch (err) {}
  }, true);

  window.addEventListener('mousedown', (e) => {
    initCursor();
    const layer = document.getElementById('cursor-layer');
    if (!layer) return;
    const ripple = document.createElement('div');
    ripple.className = 'click-ripple';
    ripple.style.left = `${e.clientX}px`;
    ripple.style.top = `${e.clientY}px`;
    ripple.style.width = '64px';
    ripple.style.height = '64px';
    layer.appendChild(ripple);
    setTimeout(() => ripple.remove(), 550);
  }, true);

  window.showCallout = (text, bgColor, textColor, fade) => {
    initCursor();
    const el = document.createElement('div');
    el.className = 'callout-bubble';
    el.style.backgroundColor = bgColor || '#ffeb3b';
    el.style.color = textColor || '#000000';
    el.style.transition = `opacity ${fade || 2}s ease`;
    el.textContent = text;
    (document.body || document.documentElement).appendChild(el);
    requestAnimationFrame(() => { el.style.opacity = '1'; });
    return el;
  };
})();"""


def move_mouse(
    page: Page,
    start_x: float,
    start_y: float,
    target_x: float,
    target_y: float,
    steps: int = 35,
    delay: float = 0.012,
) -> tuple[float, float]:
    dist = ((target_x - start_x) ** 2 + (target_y - start_y) ** 2) ** 0.5
    steps = max(20, min(70, int(dist / 14)))
    bend = random.uniform(-0.15, 0.15) * dist
    nx, ny = (-(target_y - start_y) / dist, (target_x - start_x) / dist) if dist else (0.0, 0.0)
    cx, cy = (start_x + target_x) / 2 + nx * bend, (start_y + target_y) / 2 + ny * bend
    for i in range(1, steps + 1):
        t = i / steps
        ease = t * t * (3 - 2 * t)
        x = (1 - ease) ** 2 * start_x + 2 * (1 - ease) * ease * cx + ease * ease * target_x
        y = (1 - ease) ** 2 * start_y + 2 * (1 - ease) * ease * cy + ease * ease * target_y
        page.mouse.move(x, y)
        time.sleep(delay)
    return target_x, target_y


def type_text(page: Page, text: str) -> None:
    for ch in text:
        page.keyboard.type(ch)
        time.sleep(0.2 if ch == " " else random.uniform(0.07, 0.14))


def ShowCallout(
    text: str,
    bgColor: str = "#ffeb3b",
    textColor: str = "#000000",
    fade: float = 2.0,
    page: Page | None = None,
) -> None:
    target = page or current_page
    if not target:
        return
    try:
        target.evaluate(
            """([msg, bg, tc, f]) => {
                if (window.showCallout) {
                    window.showCallout(msg, bg, tc, f);
                }
            }""",
            [text, bgColor, textColor, fade],
        )
    except Exception:
        pass


def CaptureVideo_Magento(
    url,
    company_name,
    language,
    country,
    postcode,
    city,
    street,
    house_no,
):
    """Record a browser automation scenario into a .webm video.

    Args:
        url (str): Target start URL (e.g. DuckDuckGo).
        company_name (str): Name of the company for branding and filename.
        language (str): Locale language code.
        country (str): Target country name.
        postcode (str): Postal code.
        city (str): City name used in the search query.
        street (str): Street address.
        house_no (str): House number.

    Returns:
        Optional[str]: Absolute path to the recorded .webm file, or None on failure.
    """
    global current_page

    output_dir = Path(PATH_TO_VIDEO_FOLDER).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    browser = None
    context = None

    try:
        with sync_playwright() as pw:
            headless_val = HEADLESS
            if os.environ.get("HEADLESS") is not None:
                headless_val = os.environ.get("HEADLESS", "1") not in ("0", "false", "False", "no")

            launch_args = {
                "headless": headless_val,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
                "ignore_default_args": ["--enable-automation"],
            }
            custom_bin = os.environ.get("CHROMIUM_PATH")
            if not custom_bin:
                for candidate in ["/usr/bin/chromium", "/usr/bin/google-chrome", "/run/current-system/sw/bin/chromium"]:
                    if os.path.exists(candidate):
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

            probe_page = browser.new_page()
            native_ua = probe_page.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
            probe_page.close()

            locale_val = "en-US"
            if language:
                clean_lang = language.strip().replace("_", "-")
                locale_val = clean_lang if "-" in clean_lang else f"{clean_lang}-US"

            context = browser.new_context(
                record_video_dir=str(output_dir),
                record_video_size={"width": 1280, "height": 720},
                viewport={"width": 1280, "height": 720},
                user_agent=native_ua,
                locale=locale_val,
                bypass_csp=True,
            )
            context.add_init_script(CURSOR_SCRIPT)

            page = context.new_page()
            current_page = page

            page.goto(url, wait_until="domcontentloaded", timeout=40000)

            x, y = 300.0, 350.0
            page.mouse.move(x, y)
            time.sleep(0.5)

            ShowCallout(f"Searching Magento services for {company_name} ({city})", fade=1.5)
            time.sleep(1.5)

            search_input = page.wait_for_selector('textarea[name="q"]', state="visible", timeout=15000)
            box = search_input.bounding_box()
            if not box:
                return None

            target_input_x = box["x"] + box["width"] * 0.35
            target_input_y = box["y"] + box["height"] * 0.5
            x, y = move_mouse(page, x, y, target_input_x, target_input_y, steps=35)
            page.mouse.click(x, y)
            time.sleep(0.4)

            type_text(page, f"Magento developer in {city}")
            time.sleep(0.6)

            submit_btn = page.locator(
                'form button[type="submit"]:visible, button[aria-label="Search"]:visible'
            ).first
            if submit_btn.count() == 0:
                try:
                    page.wait_for_selector(
                        'form button[type="submit"]:visible, button[aria-label="Search"]:visible',
                        state="visible",
                        timeout=3000,
                    )
                    submit_btn = page.locator(
                        'form button[type="submit"]:visible, button[aria-label="Search"]:visible'
                    ).first
                except Exception:
                    pass

            btn_box = submit_btn.bounding_box() if submit_btn.count() > 0 else None
            if btn_box:
                x, y = move_mouse(
                    page,
                    x,
                    y,
                    btn_box["x"] + btn_box["width"] * 0.5,
                    btn_box["y"] + btn_box["height"] * 0.5,
                    steps=25,
                )
                page.mouse.click(x, y)
            else:
                page.keyboard.press("Enter")

            first_result_selector = 'a[data-testid="result-title-a"]'
            for attempt_wait in range(3):
                try:
                    page.wait_for_selector(first_result_selector, state="visible", timeout=15000)
                    break
                except Exception as e:
                    if "context was destroyed" in str(e).lower() or "navigation" in str(e).lower():
                        time.sleep(1.0)
                        continue
                    if attempt_wait == 1:
                        # DuckDuckGo sometimes loads a blank page; reload it
                        try:
                            page.reload(wait_until="domcontentloaded", timeout=15000)
                        except Exception:
                            pass
                    else:
                        page.keyboard.press("Enter")
                    time.sleep(1.0)
            time.sleep(1.0)

            first_result = page.locator(first_result_selector).first
            res_box = first_result.bounding_box()
            if res_box:
                try:
                    first_result.evaluate("el => { el.target = '_self'; el.removeAttribute('target'); }")
                except Exception:
                    pass

                target_click_x = res_box["x"] + min(50.0, res_box["width"] * 0.3)
                target_click_y = res_box["y"] + res_box["height"] * 0.5
                x, y = move_mouse(page, x, y, target_click_x, target_click_y, steps=35)
                page.mouse.click(x, y)
                try:
                    first_result.click(timeout=3000)
                except Exception:
                    pass
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=7000)
                except Exception:
                    pass
                time.sleep(3.0)

            video = page.video
            context.close()
            browser.close()
            browser = None
            context = None

            raw_path = video.path()
            safe_name = "".join(c for c in company_name if c.isalnum() or c in (" ", "-", "_")).strip()
            final_path = output_dir / f"{safe_name or 'video'}.webm"

            if final_path.exists():
                final_path.unlink()

            shutil.move(raw_path, final_path)
            return str(final_path.resolve())

    except Exception as e:
        print(f"Error processing {company_name}: {e}")
        safe_name = "".join(c for c in company_name if c.isalnum() or c in (" ", "-", "_")).strip()
        failed_path = output_dir / f"{safe_name or 'video'}_FAILED.webm"
        if page:
            try:
                page.screenshot(path=str(output_dir / f"debug_error_{safe_name or 'unknown'}.png"))
            except Exception:
                pass
        if context:
            try:
                context.close()
            except Exception:
                pass
        if page and page.video:
            try:
                raw = page.video.path()
                if os.path.exists(raw):
                    if failed_path.exists():
                        failed_path.unlink()
                    shutil.move(raw, failed_path)
                    print(f"Preserved failed attempt video: {failed_path}")
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        return None
    finally:
        current_page = None
