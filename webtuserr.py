from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("https://freecash.pythonanywhere.com")
    print("Press Ctrl+C to close...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nCtrl+C detected. Closing browser...")
    finally:
        # Try to close, ignore any connection errors
        try:
            browser.close()
        except Exception:
            pass    # browser already closed, nothing to worry about