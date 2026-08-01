# x-monitor

เช็คโพสต์ใหม่จากบัญชี X (Twitter) ที่กำหนดทุก 5 นาที สรุป+แปลเป็นไทยด้วย Claude แล้วส่งเข้า Telegram ให้กดอนุมัติก่อนโพสต์เข้า Facebook Page

## สถาปัตยกรรม

```
GitHub Actions (ทุก 5 นาที)
  -> checker/check.py: สแครป X ด้วย Playwright -> เทียบ state.json -> สรุป+แปลด้วย Claude -> ส่ง Telegram (ปุ่ม Approve/Reject)

Vercel (webhook แบบ event-based, ฟรี)
  -> webhook/api/telegram.js: รับปุ่มที่กด -> ถ้า Approve โพสต์เข้า Facebook Page ผ่าน Graph API
```

## Setup

### 1. ติดตั้งเครื่องมือบนเครื่องตัวเอง (ทำครั้งเดียว)

```bash
cd checker
pip install -r requirements.txt
playwright install chromium
```

### 2. ดึง cookies ล็อกอิน X

```bash
python get_x_cookies.py
```

จะเปิดเบราว์เซอร์ขึ้นมา ล็อกอิน X ให้เสร็จ กด Enter ในเทอร์มินัล จะได้ไฟล์ `x-state.json`

แปลงเป็น base64 (เก็บไว้ใส่ GitHub Secret ในขั้นตอนถัดไป):

```bash
# macOS/Linux
base64 -w0 x-state.json > x-state.b64.txt

# Windows PowerShell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("x-state.json")) | Out-File x-state.b64.txt
```

⚠️ ห้าม commit `x-state.json` เข้า git — เป็น session ล็อกอินของคุณ

### 3. สร้าง Telegram Bot

1. คุยกับ [@BotFather](https://t.me/BotFather) → `/newbot` → ได้ **Bot Token**
2. ทักบอทของคุณ 1 ข้อความ แล้วเปิด `https://api.telegram.org/bot<TOKEN>/getUpdates` ในเบราว์เซอร์ → หา `"chat":{"id": ...}` → นั่นคือ **Chat ID**

### 4. เตรียม Anthropic API key

ไปที่ [console.anthropic.com](https://console.anthropic.com) สร้าง API key (ใช้เรียก Claude สรุป+แปลภาษา)

### 5. ใส่รายชื่อบัญชี X

แก้ไฟล์ `checker/accounts.txt` ใส่ username (ไม่ต้องมี @) บรรทัดละ 1 ชื่อ สูงสุดตามที่ต้องการ (ตัวอย่างนี้ตั้งไว้ 10)

### 6. สร้าง GitHub repo (public) แล้ว push

```bash
git init
git add .
git commit -m "initial scaffold"
```

สร้าง repo เปล่าบน [github.com/new](https://github.com/new) — **ต้องเป็น Public** เพื่อให้ได้ GitHub Actions minutes ฟรีไม่จำกัด — แล้ว:

```bash
git remote add origin https://github.com/<your-username>/x-monitor.git
git branch -M main
git push -u origin main
```

### 7. ใส่ GitHub Secrets

ไปที่ repo → Settings → Secrets and variables → Actions → New repository secret ใส่ทั้งหมดนี้:

| Secret | ค่า |
|---|---|
| `TELEGRAM_BOT_TOKEN` | จากขั้นตอน 3 |
| `TELEGRAM_CHAT_ID` | จากขั้นตอน 3 |
| `ANTHROPIC_API_KEY` | จากขั้นตอน 4 |
| `X_STORAGE_STATE_B64` | เนื้อหาในไฟล์ `x-state.b64.txt` จากขั้นตอน 2 |

หลังจากนี้ GitHub Actions (`.github/workflows/checker.yml`) จะรันทุก 5 นาทีอัตโนมัติ — เช็คได้ที่แท็บ Actions ของ repo

⚠️ ถ้า repo ไม่มี activity 60 วัน GitHub จะ auto-disable scheduled workflow ต้องเข้าไปกดเปิดใหม่เอง (แค่ push commit เล็กๆ ก็นับ)

### 8. สร้าง Facebook App + Page Access Token

1. ไปที่ [developers.facebook.com](https://developers.facebook.com) → สร้าง App (ประเภท Business)
2. เพิ่ม Page ที่คุณดูแลอยู่ ขอสิทธิ์ `pages_manage_posts` และ `pages_read_engagement`
3. ใช้ [Graph API Explorer](https://developers.facebook.com/tools/explorer/) generate **Page Access Token** แบบ long-lived (ไม่หมดอายุ — เลือก "Never Expire" ตอน extend token หรือใช้ System User Token ถ้าต้องการความเสถียรระยะยาว)
4. จด **Page ID** ไว้ด้วย

### 9. Deploy webhook ขึ้น Vercel

```bash
cd webhook
npm install -g vercel   # ถ้ายังไม่มี
vercel login
vercel --prod
```

ตั้งค่า Environment Variables บน Vercel (Dashboard → Project → Settings → Environment Variables):

| ตัวแปร | ค่า |
|---|---|
| `TELEGRAM_BOT_TOKEN` | เหมือนขั้นตอน 3 |
| `TELEGRAM_WEBHOOK_SECRET` | ตั้งค่าเองเป็น string สุ่มยาวๆ (ป้องกันคนอื่นยิง request ปลอมมาที่ webhook) |
| `FB_PAGE_ID` | จากขั้นตอน 8 |
| `FB_PAGE_TOKEN` | จากขั้นตอน 8 |

deploy ใหม่อีกครั้งหลังตั้ง env vars: `vercel --prod`

### 10. ตั้ง Telegram webhook ให้ชี้มาที่ Vercel

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=https://<your-vercel-app>.vercel.app/api/telegram&secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

ตรวจสอบว่าตั้งสำเร็จ:

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getWebhookInfo"
```

### 11. ทดสอบ

รันด้วยมือครั้งแรกเพื่อดูว่าทำงานถูกต้อง: ไปที่ repo → Actions → เลือก workflow "Check X accounts" → Run workflow

ถ้ามีโพสต์ใหม่จากบัญชีในลิสต์ จะมีข้อความเข้า Telegram พร้อมปุ่ม Approve/Reject — กด Approve แล้วเช็คว่าโพสต์เข้า Facebook Page จริง

## ข้อจำกัดที่รู้ไว้

- X อาจบล็อก/CAPTCHA ถ้า session cookies หมดอายุ — ต้องรัน `get_x_cookies.py` ใหม่แล้วอัปเดต secret `X_STORAGE_STATE_B64`
- GitHub Actions cron ทุก 5 นาทีอาจดีเลย์ได้ช่วง GitHub โหลดสูง
- Facebook Page Access Token แบบปกติหมดอายุใน 60 วัน ถ้าไม่ extend เป็น long-lived — เช็คให้ดีตอนสร้าง
