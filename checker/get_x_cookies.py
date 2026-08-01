"""One-off local script: opens a real browser, you log into X manually,
then this saves the login session (cookies) to x-state.json.

Run once on your own machine:
    pip install playwright
    playwright install chromium
    python get_x_cookies.py
"""

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://x.com/login")

    input("ล็อกอิน X ในหน้าต่างเบราว์เซอร์ที่เปิดขึ้นให้เสร็จ แล้วกด Enter ตรงนี้...")

    context.storage_state(path="x-state.json")
    browser.close()
    print("บันทึกแล้ว: x-state.json")
    print("ขั้นตอนถัดไป: base64 encode ไฟล์นี้แล้วเก็บเป็น GitHub secret X_STORAGE_STATE_B64")
