import os
import json
import asyncio
import logging
from typing import Set

import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ChatMemberHandler,
)

# ============ إعدادات عامة ============
BOT_TOKEN = os.getenv("BOT_TOKEN")  # ← لا تضع التوكن هنا
TIKTOK_USERNAME = os.getenv("TIKTOK_USERNAME", "d.shakertawfiqalaroury")

TIKTOK_URL = f"https://www.tiktok.com/@{TIKTOK_USERNAME}"

DATA_FILE = "subscribers.json"
CHECK_INTERVAL = 30

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

subscribers: Set[int] = set()
last_is_live = False


def load_subscribers():
    global subscribers
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                subscribers = set(data.get("chats", []))
        except:
            subscribers = set()
    else:
        subscribers = set()


def save_subscribers():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"chats": list(subscribers)}, f, ensure_ascii=False, indent=2)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers.add(chat_id)
    save_subscribers()

    text = (
        "🎥 أهلاً بك في بوت تنبيهات البث المباشر للدكتور شاكر توفيق العاروري.\n\n"
        "✅ تم تفعيل التنبيهات لهذا الحساب.\n"
        f"حساب تيك توك:\nhttps://www.tiktok.com/@{TIKTOK_USERNAME}"
    )
    await update.message.reply_text(text)


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in subscribers:
        subscribers.remove(chat_id)
        save_subscribers()
        await update.message.reply_text("❌ تم إلغاء التنبيهات.")
    else:
        await update.message.reply_text("⚠️ لم تكن مشتركاً أصلاً.")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📊 عدد المشتركين في التنبيهات: {len(subscribers)}"
    )


def check_tiktok_live() -> bool:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(TIKTOK_URL, headers=headers, timeout=10)
        html = resp.text
        return '"isLive":true' in html or '"liveRoomId"' in html
    except:
        return False


async def live_checker_job(context: ContextTypes.DEFAULT_TYPE):
    global last_is_live
    loop = asyncio.get_event_loop()
    is_live = await loop.run_in_executor(None, check_tiktok_live)

    if is_live and not last_is_live:
        last_is_live = True

        msg = (
            "🔴 *الدكتور شاكر بدأ البث الآن!*\n"
            f"📲 شاهد الآن:\nhttps://www.tiktok.com/@{TIKTOK_USERNAME}/live"
        )

        for chat_id in subscribers:
            try:
                await context.bot.send_message(chat_id, msg, parse_mode="Markdown")
            except:
                pass

    elif not is_live and last_is_live:
        last_is_live = False


async def chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in subscribers:
        subscribers.add(chat_id)
        save_subscribers()
        await context.bot.send_message(chat_id, "✅ تم تفعيل التنبيهات هنا.")


async def main():
    load_subscribers()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("stats", stats))

    app.add_handler(ChatMemberHandler(chat_member_update, ChatMemberHandler.MY_CHAT_MEMBER))

    # ---- التعديل المهم ----
    job_queue = app.job_queue
    job_queue.run_repeating(live_checker_job, interval=CHECK_INTERVAL, first=5)

    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
