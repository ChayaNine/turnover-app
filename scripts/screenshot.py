"""Play the demo day in a headless browser and capture the dashboard at key moments.

    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python3 scripts/screenshot.py http://localhost:8000 out/ [width]
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
out = Path(sys.argv[2] if len(sys.argv) > 2 else "shots")
width = int(sys.argv[3]) if len(sys.argv) > 3 else 1440
out.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": width, "height": 960}, device_scale_factor=1)
    page.goto(base + "/#/today")
    page.wait_for_timeout(900)
    page.click("#reset")
    page.wait_for_timeout(600)
    page.screenshot(path=str(out / "00-start.png"), full_page=True)
    page.click('.seg button[data-speed="0.05"]')
    page.click("#play")
    shot, acted = 1, False
    deadline = time.time() + 150
    while time.time() < deadline:
        page.wait_for_timeout(400)
        label = page.inner_text("#playLabel")
        if label == "Resume":  # paused because something needs the host
            page.wait_for_timeout(500)
            page.screenshot(path=str(out / f"{shot:02d}-paused.png"), full_page=True)
            shot += 1
            btn = page.query_selector(".need .acts button.primary")
            if btn and not acted:
                btn.click()  # act on the first alert: text the backup cleaner
                acted = True
                page.wait_for_timeout(900)
                page.screenshot(path=str(out / f"{shot:02d}-acted.png"), full_page=True)
                shot += 1
                if page.inner_text("#playLabel") == "Resume":
                    page.click("#play")
            else:
                page.click("#play")
        if label == "Play again":
            break
    page.wait_for_timeout(900)
    page.screenshot(path=str(out / f"{shot:02d}-end.png"), full_page=True)
    for name, h in [("home-lakeview", "#/homes/lakeview"), ("home-harbor", "#/homes/harbor"), ("homes", "#/homes"), ("activity", "#/activity")]:
        shot += 1
        page.goto(base + "/" + h)
        page.wait_for_timeout(700)
        page.screenshot(path=str(out / f"{shot:02d}-{name}.png"), full_page=True)
    b.close()
print("saved to", out)
