"""Check X accounts for new posts, summarize+translate to Thai, send to Telegram for approval."""

import base64
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import google.generativeai as genai
import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
ACCOUNTS_FILE = ROOT / "accounts.txt"
STATE_FILE = ROOT / "state.json"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
X_STORAGE_STATE_B64 = os.environ["X_STORAGE_STATE_B64"]

genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-flash-latest")

IMAGE_URL_RE = re.compile(r"https?://\S+\.(?:jpg|jpeg|png|webp)(?:\?\S*)?", re.IGNORECASE)


def load_accounts() -> list[str]:
    accounts = []
    for line in ACCOUNTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            accounts.append(line)
    return accounts


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_image_url(image_url: str | None) -> str | None:
    if not image_url or not isinstance(image_url, str) or not IMAGE_URL_RE.search(image_url):
        return None
    try:
        head = requests.head(image_url, timeout=10, allow_redirects=True)
        if not head.headers.get("content-type", "").startswith("image/"):
            return None
    except requests.RequestException:
        return None
    return image_url


def summarize_translate_and_find_image(username: str, tweet_text: str) -> tuple[str, str | None]:
    # Combined into a single Gemini call (translate + image lookup) so each new post
    # only costs one request against the free-tier per-minute quota instead of two.
    prompt = (
        f"แปลและเรียบเรียงโพสต์ X (Twitter) ของ @{username} ต่อไปนี้เป็นภาษาไทย "
        "ให้อ่านลื่น เป็นธรรมชาติ เหมือนนักข่าวเขียนสรุปข่าว ไม่ใช่แปลคำต่อคำแบบแข็งๆ "
        "ความยาว 2-4 ประโยค เก็บใจความและรายละเอียดสำคัญให้ครบ "
        "ห้ามใส่ความเห็นส่วนตัว คำนำ หรือทางเลือกหลายแบบ ตอบเวอร์ชันเดียวเท่านั้น "
        "ห้ามใช้ Markdown หรือสัญลักษณ์จัดรูปแบบใดๆ ในข้อความสรุป (ห้ามมี **, *, #, -) "
        "ห้ามระบุชื่อผู้โพสต์ในข้อความสรุป (จะใส่ท้ายข้อความเองแยกต่างหาก)\n\n"
        "นอกจากนี้ ให้พิจารณาว่าข่าวนี้เกี่ยวกับนักฟุตบอลคนไหนเป็นหลัก ถ้าระบุตัวได้ชัดเจน "
        "และคุณรู้จักลิงก์รูปถ่ายจริงของนักฟุตบอลคนนั้นที่มีอยู่จริงบนอินเทอร์เน็ต "
        "(เช่นจาก Wikipedia / Wikimedia Commons) ที่ลงท้ายด้วย .jpg .jpeg .png หรือ .webp "
        "ให้ใส่ลิงก์นั้นมาด้วย ห้ามเดา URL มั่วๆ ถ้าไม่แน่ใจว่ามีอยู่จริงให้ใส่ null\n\n"
        'ตอบกลับเป็น JSON เท่านั้น รูปแบบ: {"summary": "ข้อความสรุปภาษาไทย", "image_url": "ลิงก์รูปภาพ หรือ null"}\n\n'
        "โพสต์ต้นฉบับ:\n" + tweet_text
    )
    response = gemini_model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(response_mime_type="application/json"),
    )
    data = json.loads(response.text)
    summary_th = (data.get("summary") or "").strip()
    image_url = validate_image_url(data.get("image_url"))
    return summary_th, image_url


def send_telegram_draft(username: str, summary_th: str, tweet_url: str, image_url: str | None) -> None:
    lines = [summary_th, "", f"— ข่าวจาก @{username}", f"ต้นฉบับ: {tweet_url}"]
    if image_url:
        lines.append(f"รูป: {image_url}")
    text = "\n".join(lines)

    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": "approve"},
            {"text": "❌ Reject", "callback_data": "reject"},
        ]]
    }

    # Telegram photo captions are capped at 1024 chars (vs 4096 for text messages),
    # so only attach the photo if the whole draft still fits.
    if image_url and len(text) <= 1024:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "photo": image_url,
                "caption": text,
                "reply_markup": keyboard,
            },
            timeout=15,
        )
        if resp.ok:
            return
        print(f"sendPhoto failed ({resp.status_code}): {resp.text} — falling back to text message")

    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "reply_markup": keyboard,
        },
        timeout=15,
    )
    resp.raise_for_status()


def scrape_latest_tweet(page, username: str) -> dict | None:
    page.goto(f"https://x.com/{username}", wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
    except Exception:
        print(f"[{username}] no tweets found (timeout) — skipping")
        return None

    articles = page.locator('article[data-testid="tweet"]')
    count = min(articles.count(), 5)

    for i in range(count):
        article = articles.nth(i)

        # Skip pinned tweets — they stay at the top regardless of new posts,
        # which would otherwise make every run see the same "latest" tweet forever.
        social_context = article.locator('[data-testid="socialContext"]')
        if social_context.count() > 0 and "pinned" in social_context.inner_text().lower():
            continue

        link = article.locator('a[href*="/status/"]').first
        href = link.get_attribute("href")
        if not href:
            continue
        tweet_id = href.rstrip("/").split("/")[-1]

        text_locator = article.locator('div[data-testid="tweetText"]').first
        tweet_text = text_locator.inner_text() if text_locator.count() > 0 else "(โพสต์นี้ไม่มีข้อความ อาจเป็นรูป/วิดีโอ)"

        return {"id": tweet_id, "text": tweet_text, "url": f"https://x.com{href}"}

    return None


def main() -> None:
    accounts = load_accounts()
    if not accounts:
        print("accounts.txt is empty — nothing to check")
        return

    state = load_state()

    storage_state_json = base64.b64decode(X_STORAGE_STATE_B64).decode("utf-8")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        f.write(storage_state_json)
        storage_state_path = f.name

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=storage_state_path)
        page = context.new_page()

        for username in accounts:
            try:
                latest = scrape_latest_tweet(page, username)
            except Exception as e:
                print(f"[{username}] error: {e}")
                continue

            if latest is None:
                continue

            last_seen = state.get(username)
            if latest["id"] == last_seen:
                print(f"[{username}] no new post")
                continue

            print(f"[{username}] new post {latest['id']} — summarizing")
            try:
                summary_th, image_url = summarize_translate_and_find_image(username, latest["text"])
                send_telegram_draft(username, summary_th, latest["url"], image_url)
            except Exception as e:
                # A failure here (e.g. quota) just skips this account for now; state
                # isn't saved, so it's retried on the next scheduled run instead of
                # crashing the whole job.
                print(f"[{username}] error while summarizing/sending: {e} — will retry next run")
                continue

            state[username] = latest["id"]
            save_state(state)  # save after each account so partial progress isn't lost

        browser.close()

    os.unlink(storage_state_path)


if __name__ == "__main__":
    main()
