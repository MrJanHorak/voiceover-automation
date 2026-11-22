"""Capture the documentation screenshots from demo_server.py with a headless browser.

Needs Playwright (pip install playwright, then: playwright install chromium). Set CHROME to use an
installed Chrome instead of Playwright's Chromium.
"""
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8765"
OUT = str(Path(__file__).resolve().parent)
SCRIPT = ("Welcome to the Brightwave spring update. I'm glad you're here.\n\n"
          "On Mar. 3 at 10:30 a.m., Dr. Patel will open our new studio at 410 Main St. The first 250 guests get a free gift.\n\n"
          "The Pro plan now costs $129.99, about 40% less than last year. Questions? Call (555) 123-4567.\n\n"
          "Short and sweet. That's the goal.")
INIT = "try { localStorage.setItem('studio-welcome-dismissed', '1'); } catch (e) {}"

def context(browser, role):
    ctx = browser.new_context(viewport={"width": 1360, "height": 900}, device_scale_factor=1, color_scheme="dark")
    ctx.add_cookies([{"name": "role", "value": role, "url": BASE}])
    ctx.add_init_script(INIT)
    return ctx

def settle(page, ms=1200):
    page.wait_for_timeout(ms)

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("CHROME") or None)

    # Sign-up page
    page = context(browser, "customer").new_page()
    page.goto(f"{BASE}/signup"); settle(page, 500)
    page.screenshot(path=f"{OUT}/signup.png")

    # Customer: script, spoken preview, voice picker, recordings
    page = context(browser, "customer").new_page()
    page.goto(f"{BASE}/#create"); settle(page)
    page.fill("#recording-title", "Brightwave spring update")
    page.fill("#script", SCRIPT); page.dispatch_event("#script", "input"); settle(page, 500)
    page.screenshot(path=f"{OUT}/customer-script.png", full_page=True)
    page.click("#mode-preview"); settle(page)
    page.screenshot(path=f"{OUT}/customer-spoken-preview.png", full_page=True)
    page.click("#mode-edit"); page.click("#to-settings"); settle(page)
    page.screenshot(path=f"{OUT}/customer-voice-picker.png", full_page=True)
    page.goto(f"{BASE}/#recordings"); page.reload(); settle(page, 2000)
    page.screenshot(path=f"{OUT}/customer-recording.png", full_page=True)

    # Admin: voices, customers, training, recordings
    admin = context(browser, "admin")
    page = admin.new_page()
    page.goto(f"{BASE}/#voices"); settle(page, 1800)
    page.screenshot(path=f"{OUT}/admin-voices.png", full_page=True)
    page.goto(f"{BASE}/#customers"); page.reload(); settle(page, 1800)
    page.screenshot(path=f"{OUT}/admin-customers.png", full_page=True)
    page.goto(f"{BASE}/#training"); page.reload(); settle(page, 1800)
    page.screenshot(path=f"{OUT}/admin-training.png", full_page=True)
    page.goto(f"{BASE}/#recordings"); page.reload(); settle(page, 1800)
    page.click("#scope-customers"); settle(page, 800)
    page.click(".history-select"); settle(page, 1500)
    page.screenshot(path=f"{OUT}/admin-customer-recording.png", full_page=True)
    browser.close()
print(f"Screenshots written to {OUT}")
