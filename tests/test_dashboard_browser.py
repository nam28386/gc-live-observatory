import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

import httpx2 as httpx
from playwright.sync_api import expect, sync_playwright
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app_url(tmp_path, mode):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with (tmp_path / "app.log").open("w+", encoding="utf-8") as log:
        process = subprocess.Popen([
            sys.executable, "-m", "observatory", "mock", "--port", str(port),
            "--interval", "0.025", "--disconnect-after", "25", "--mode", mode,
            "--capture", str(tmp_path / "browser.sqlite"),
        ], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            with httpx.Client(trust_env=False, timeout=0.5) as client:
                while process.poll() is None and time.monotonic() < deadline:
                    try:
                        if client.get(f"{url}/api/status").status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(0.025)
                else:
                    log.seek(0)
                    pytest.fail(f"MOCK launcher failed to start:\n{log.read()}")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.browser
@pytest.mark.parametrize("mode", ["A", "C"])
def test_every_canvas_updates_from_mock_websocket_and_gap_is_visible(app_url, mode):
    with sync_playwright() as playwright:
        executable = os.environ.get("PR00_CHROMIUM") or shutil.which("chromium")
        if not executable and not Path(playwright.chromium.executable_path).exists():
            message = "Chromium is unavailable. Run python -m playwright install chromium."
            if os.environ.get("PR00_REQUIRE_BROWSER") == "1":
                pytest.fail(message)
            pytest.skip(message)
        browser = playwright.chromium.launch(executable_path=executable)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(app_url, wait_until="domcontentloaded")
        expect(page.get_by_text("MOCK / SYNTHETIC")).to_be_visible()
        expect(page.locator("#instrument")).to_contain_text("COMEX · GCZ6")
        expect(page.locator("#spread")).to_have_text("2 ticks")
        expect(page.locator("#ack")).to_have_text(f"Acknowledged #{3 if mode == 'C' else 2}", timeout=10_000)
        expect(page.locator("#gaps")).to_have_text("1 · historical gaps retained")
        expect(page.locator("#recorder")).to_contain_text("durable records")
        expect(page.locator("#timeline-entries")).to_contain_text("Capture interrupted")
        expect(page.get_by_label("Microstructure measurements")).to_be_visible()
        if mode == "C":
            expect(page.locator("#depth-health")).to_contain_text("VALID_BOOK")
            expect(page.locator("#micro-mbo")).to_contain_text("SYNTHETIC")
            expect(page.locator("#timeline-entries")).to_contain_text("MBP snapshot recovered")
            expect(page.locator("#micro-ofi")).to_contain_text("VALID")
        else:
            expect(page.locator("#micro-mbo")).to_have_text("DISABLED · DISABLED")
        canvases = page.locator("canvas")
        expect(canvases).to_have_count(4)
        page.wait_for_function(
            "() => [...document.querySelectorAll('canvas')].every(c => c.dataset.latestCandle)"
        )
        before = canvases.evaluate_all("nodes => nodes.map(canvas => canvas.dataset.latestCandle)")
        page.wait_for_function(
            "(before) => [...document.querySelectorAll('canvas')].every("
            "(canvas, i) => canvas.dataset.latestCandle !== before[i])",
            arg=before,
        )
        expect(page.locator(".chart output").first).to_contain_text("Forming")
        drawn = canvases.evaluate_all("""nodes => nodes.every(canvas => {
          const image = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height);
          let filledPixels = 0;
          for (let i = 3; i < image.data.length; i += 4) if (image.data[i]) filledPixels++;
          return filledPixels > 100;
        })""")
        assert drawn
        assert not errors
        browser.close()
