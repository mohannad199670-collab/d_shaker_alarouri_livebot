import os
import json
import asyncio
import logging
import requests
from typing import Set
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# -----------------------------
# المتغيرات من Environment Variables
# -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
TIKTOK_USERNAME = os.getenv("TIKTOK_USERNAME")

TIKTOK_URL = f"https://www.tiktok.com/@{TIKTOK_USERNAME}/live"
DATA_FILE = "subscribers.json"
CHECK_INTERVAL = 30  # كل 30 ثانية فحص

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------
# تحميل المشتركين
# -----------------------------
def load_subscribers() -> Set[int]:
    if not os.path.exists(DATA_FILE):
        return set()
    with open(DATA_FILE, "r") as f:
        return set(json.load(f))

# -----------------------------
# حفظ المشتركين
# -----------------------------
def save_subscribers(subscribers: Set[int]):
    with open(DATA_FILE, "w") as f:
        json.dump(list(subscribers), f)

subscribers = load_subscribers()

# -----------------------------
# أمر Start
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in subscribers:
        subscribers.add(user_id)
        save_subscribers(subscribers)
    
    await update.message.reply_text(
        "أهلاً بك!👌\n"
        "سيصلك إشعار بمجرد أن يبدأ الدكتور شاكر العاروري بث مباشر على التيك توك."
    )

# -----------------------------
# فحص البث
# -----------------------------
async def is_live():
    try:
        response = requests.get(TIKTOK_URL, timeout=10)
        return "is_live_broadcast" in response.text
    except:
        return False

# -----------------------------
# وظيفة الفحص المتكرر
# -----------------------------
async def live_checker(app):
    was_live = False

    while True:
        now_live = await asyncio.to_thread(is_live)

        if now_live and not was_live:
            for user_id in subscribers:
                try:
                    await app.bot.send_message(
                        chat_id=user_id,
                        text="🔴 الدكتور شاكر بدأ البث الآن على التيك توك!"
                    )
                except:
                    pass

        was_live = now_live
        await asyncio.sleep(CHECK_INTERVAL)

# -----------------------------
# Main
# -----------------------------
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    asyncio.create_task(live_checker(app))

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
