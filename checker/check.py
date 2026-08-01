"""Check X accounts for new posts, summarize+translate to Thai, send to Telegram for approval."""

import base64
import json
import os
import sys
import tempfile
from pathlib import Path

import requests
from anthropic import Anthropic
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
ACCOUNTS_FILE = ROOT / "accounts.txt"
STATE_FILE = ROOT / "state.json"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
X_STORAGE_STATE_B64 = os.environ["X_STORAGE_STATE_B64"]

anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)


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


def summarize_and_translate(username: str, tweet_text: str) -> str:
    response = anthropic_client.messages.create(
        model="claude-opus-5",
        max_tokens=500,
        output_config={"effort": "low"},
        messages=[{
            "role": "user",
            "content": (
                f"สรุปโพสต์ X (Twitter) ของ @{username} ต่อไปนี้เป็นภาษาไทย "
                "กระชับ 2-4 ประโยค เก็บใจความสำคัญ ห้ามใส่ความเห็นหรือคำนำเพิ่มเติม "
                "ตอบแค่เนื้อหาสรุปเท่านั้น:\n\n" + tweet_text
            ),
        }],
    )
    for block in response.content:
        if block.type == "text":
            return block.text.strip()
    return "(สรุปไม่สำเร็จ)"


def send_telegram_draft(username: str, summary_th: str, tweet_url: str) -> None:
    text = f"🆕 โพสต์ใหม่จาก @{username}\n\n{summary_th}\n\nต้นฉบับ: {tweet_url}"
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "✅ Approve", "callback_data": "approve"},
                    {"text": "❌ Reject", "callback_data": "reject"},
                ]]
            },
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

    article = page.locator('article[data-testid="tweet"]').first
    link = article.locator('a[href*="/status/"]').first
    href = link.get_attribute("href")
    if not href:
        return None
    tweet_id = href.rstrip("/").split("/")[-1]

    text_locator = article.locator('div[data-testid="tweetText"]').first
    tweet_text = text_locator.inner_text() if text_locator.count() > 0 else "(โพสต์นี้ไม่มีข้อความ อาจเป็นรูป/วิดีโอ)"

    return {"id": tweet_id, "text": tweet_text, "url": f"https://x.com{href}"}


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
            summary_th = summarize_and_translate(username, latest["text"])
            send_telegram_draft(username, summary_th, latest["url"])

            state[username] = latest["id"]
            save_state(state)  # save after each account so partial progress isn't lost

        browser.close()

    os.unlink(storage_state_path)


if __name__ == "__main__":
    main()
