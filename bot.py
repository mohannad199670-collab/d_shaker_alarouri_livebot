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
BOT_TOKEN = "PASTE_YOUR_TOKEN_HERE"  # ← حط هنا التوكن تبعك بين علامات التنصيص
TIKTOK_USERNAME = "d.shakertawfiqalaroury"  # اسم حساب الدكتور على تيك توك
TIKTOK_URL = f"https://www.tiktok.com/@{TIKTOK_USERNAME}"

DATA_FILE = "subscribers.json"  # ملف تخزين المشتركين
CHECK_INTERVAL = 30  # كل كم ثانية نتحقق من البث

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

subscribers: Set[int] = set()
last_is_live = False  # لتفادي إرسال نفس التنبيه أكثر من مرة


# ============ دوال تخزين المشتركين ============

def load_subscribers():
    global subscribers
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                subscribers = set(data.get("chats", []))
        except Exception as e:
            logger.error(f"Error loading subscribers: {e}")
            subscribers = set()
    else:
        subscribers = set()


def save_subscribers():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"chats": list(subscribers)}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving subscribers: {e}")


# ============ أوامر البوت ============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers.add(chat_id)
    save_subscribers()

    text = (
        "🎥 أهلاً بك في بوت تنبيهات البث المباشر للدكتور شاكر توفيق العاروري.\n\n"
        "✅ تم تفعيل التنبيهات لهذا الحساب / القناة.\n"
        "📩 سيتم إعلامك تلقائياً عند بدء البث المباشر على تيك توك.\n\n"
        "حساب تيك توك:\n"
        f"https://www.tiktok.com/@{TIKTOK_USERNAME}"
    )
    await update.effective_chat.send_message(text)


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in subscribers:
        subscribers.remove(chat_id)
        save_subscribers()
        await update.effective_chat.send_message(
            "❌ تم إلغاء تفعيل التنبيهات لهذا الحساب."
        )
    else:
        await update.effective_chat.send_message(
            "⚠️ هذا الحساب غير مشترك أساساً في التنبيهات."
        )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        f"📊 عدد الحسابات / القنوات المشتركة في التنبيهات: {len(subscribers)}"
    )


# ============ كشف إذا كان الحساب لايف على تيك توك ============

def check_tiktok_live() -> bool:
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            )
        }
        resp = requests.get(TIKTOK_URL, headers=headers, timeout=10)

        if resp.status_code != 200:
            logger.warning(f"TikTok returned status code {resp.status_code}")
            return False

        html = resp.text
        if '"isLive":true' in html or '"liveRoomId"' in html:
            return True

        return False
    except Exception as e:
        logger.error(f"Error checking TikTok live status: {e}")
        return False


# ============ مهمة دورية لفحص البث وإرسال التنبيهات ============

async def live_checker_job(context: ContextTypes.DEFAULT_TYPE):
    global last_is_live
    is_live = await asyncio.get_event_loop().run_in_executor(None, check_tiktok_live)

    if is_live and not last_is_live:
        last_is_live = True

        message = (
            "🔴 *تم بدء البث المباشر الآن!*\n\n"
            f"🎥 الدكتور شاكر توفيق العاروري الآن على البث المباشر في تيك توك.\n\n"
            f"📲 أدخل الآن للمشاهدة:\nhttps://www.tiktok.com/@{TIKTOK_USERNAME}/live"
        )

        for chat_id in list(subscribers):
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"Error sending to {chat_id}: {e}")

    elif not is_live and last_is_live:
        last_is_live = False


# ============ التعامل مع إضافة البوت إلى قنوات / مجموعات ============

async def chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat is None:
        return

    chat_id = chat.id
    if chat_id not in subscribers:
        subscribers.add(chat_id)
        save_subscribers()
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "✅ تم تفعيل بوت تنبيهات بث الدكتور شاكر في هذه القناة / المجموعة.\n"
                    "لإلغاء التنبيهات: /stop"
                ),
            )
        except Exception as e:
            logger.error(f"Error welcoming chat {chat_id}: {e}")


# ============ نقطة تشغيل البوت ============

async def main():
    load_subscribers()

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("stats", stats))

    application.add_handler(ChatMemberHandler(chat_member_update, ChatMemberHandler.MY_CHAT_MEMBER))

    application.job_queue.run_repeating(
        live_checker_job, interval=CHECK_INTERVAL, first=5
    )

    await application.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
