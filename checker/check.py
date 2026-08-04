"""Check X accounts for new posts, summarize+translate to Thai, send to Telegram for approval.

Runs hourly. Scrapes every account first, then makes a single batched Gemini
call for all new posts found in that run (not one call per post) to stay
within the free-tier request quota.
"""

import base64
import json
import os
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


def summarize_and_translate_batch(posts: list[dict]) -> list[str]:
    """One Gemini call for every new post found this run, instead of one call per post."""
    items = [{"index": i, "text": p["text"]} for i, p in enumerate(posts)]
    prompt = (
        f"ต่อไปนี้คือโพสต์ X (Twitter) จำนวน {len(items)} โพสต์ ในรูปแบบ JSON array "
        "แต่ละอันมี index กับ text:\n\n"
        + json.dumps(items, ensure_ascii=False)
        + "\n\nแปลและเรียบเรียงแต่ละโพสต์เป็นภาษาไทย ให้อ่านลื่นเป็นธรรมชาติ "
        "เหมือนนักข่าวเขียนสรุปข่าว ไม่ใช่แปลคำต่อคำแบบแข็งๆ "
        "ความยาว 2-4 ประโยคต่อโพสต์ เก็บใจความและรายละเอียดสำคัญให้ครบ "
        "ห้ามใส่ความเห็นส่วนตัว คำนำ หรือชื่อผู้โพสต์ในเนื้อหา "
        "ห้ามใช้ Markdown หรือสัญลักษณ์จัดรูปแบบใดๆ (ห้ามมี **, *, #, -) "
        "ตอบกลับเป็น JSON array ของสตริงเท่านั้น ความยาวเท่ากับจำนวนโพสต์ที่ให้ไปเป๊ะ "
        "เรียงลำดับตาม index เดิม ห้ามมีข้อความอื่นนอกเหนือจาก JSON array"
    )
    response = gemini_model.generate_content(
        prompt,
        generation_config={"response_mime_type": "application/json"},
    )
    summaries = json.loads(response.text)
    if len(summaries) != len(posts):
        raise ValueError(f"expected {len(posts)} summaries, got {len(summaries)}")
    return summaries


def send_telegram_draft(username: str, summary_th: str, tweet_url: str) -> None:
    text = f"{summary_th}\n\n— ข่าวจาก @{username}\nต้นฉบับ: {tweet_url}"

    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": "approve"},
            {"text": "❌ Reject", "callback_data": "reject"},
        ]]
    }

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

    # Phase 1: scrape every account first, collect all new posts. No Gemini
    # calls yet — we want exactly one batched call at the end, not one per post.
    new_posts = []
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

            if latest["id"] == state.get(username):
                print(f"[{username}] no new post")
                continue

            print(f"[{username}] new post {latest['id']}")
            new_posts.append({"username": username, **latest})

        browser.close()

    os.unlink(storage_state_path)

    if not new_posts:
        print("no new posts across any account this run")
        return

    # Phase 2: one Gemini call for all new posts found this run.
    try:
        summaries = summarize_and_translate_batch(new_posts)
    except Exception as e:
        # Don't touch state — every post here gets retried as "new" next run.
        print(f"batch summarization failed: {e} — will retry next run")
        return

    for post, summary_th in zip(new_posts, summaries):
        try:
            send_telegram_draft(post["username"], summary_th, post["url"])
        except Exception as e:
            print(f"[{post['username']}] failed to send to Telegram: {e} — will retry next run")
            continue
        state[post["username"]] = post["id"]

    save_state(state)


if __name__ == "__main__":
    main()
