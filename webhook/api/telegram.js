const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const TELEGRAM_WEBHOOK_SECRET = process.env.TELEGRAM_WEBHOOK_SECRET; // optional but recommended
const FB_PAGE_ID = process.env.FB_PAGE_ID;
const FB_PAGE_TOKEN = process.env.FB_PAGE_TOKEN;
const FB_GRAPH_VERSION = "v21.0";

async function telegramApi(method, payload) {
  const res = await fetch(`https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

async function postToFacebookPage(message) {
  const url = `https://graph.facebook.com/${FB_GRAPH_VERSION}/${FB_PAGE_ID}/feed`;
  const params = new URLSearchParams({ message, access_token: FB_PAGE_TOKEN });
  const res = await fetch(url, { method: "POST", body: params });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(`Facebook API error: ${JSON.stringify(data)}`);
  }
  return data;
}

export default async function handler(req, res) {
  if (req.method !== "POST") {
    res.status(200).send("ok");
    return;
  }

  if (TELEGRAM_WEBHOOK_SECRET) {
    const incoming = req.headers["x-telegram-bot-api-secret-token"];
    if (incoming !== TELEGRAM_WEBHOOK_SECRET) {
      res.status(401).send("unauthorized");
      return;
    }
  }

  const update = req.body;
  const callback = update?.callback_query;

  if (!callback) {
    res.status(200).send("ignored");
    return;
  }

  const chatId = callback.message.chat.id;
  const messageId = callback.message.message_id;
  const originalText = callback.message.text || "";

  try {
    if (callback.data === "approve") {
      // Telegram draft ends with "\nต้นฉบับ: <url>" — drop that line so the
      // link isn't posted to the Page, but keep the "— ข่าวจาก @user" credit above it.
      const draftText = originalText.replace(/\nต้นฉบับ: \S+$/, "").trim();
      await postToFacebookPage(draftText);
      await telegramApi("editMessageText", {
        chat_id: chatId,
        message_id: messageId,
        text: `${originalText}\n\n✅ โพสต์เข้า Facebook Page แล้ว`,
      });
      await telegramApi("answerCallbackQuery", {
        callback_query_id: callback.id,
        text: "โพสต์แล้ว",
      });
    } else if (callback.data === "reject") {
      await telegramApi("editMessageText", {
        chat_id: chatId,
        message_id: messageId,
        text: `${originalText}\n\n❌ ถูก reject`,
      });
      await telegramApi("answerCallbackQuery", {
        callback_query_id: callback.id,
        text: "ยกเลิกแล้ว",
      });
    } else {
      await telegramApi("answerCallbackQuery", { callback_query_id: callback.id });
    }
  } catch (err) {
    console.error(err);
    await telegramApi("answerCallbackQuery", {
      callback_query_id: callback.id,
      text: "เกิดข้อผิดพลาด โปรดดู log",
      show_alert: true,
    });
  }

  res.status(200).send("ok");
}
