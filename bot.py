import os
import asyncio
import json
import logging
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher, executor, types

# ===============================
#          الإعدادات
# ===============================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TIKTOK_URL = os.getenv("TIKTOK_URL", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ يجب ضبط TELEGRAM_TOKEN في لوحة Koyeb")

if not TIKTOK_URL:
    raise RuntimeError("❌ يجب ضبط TIKTOK_URL في لوحة Koyeb")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

SUBS_FILE = Path("subscribers.json")
CHECK_INTERVAL = 30  # فحص كل 30 ثانية
last_live_state = None


# ===============================
#     إدارة المشتركين
# ===============================

def is_admin(user_id):
    return user_id == ADMIN_ID

def load_subs():
    if not SUBS_FILE.exists():
        return set()
    try:
        return set(json.loads(SUBS_FILE.read_text()))
    except:
        return set()

def save_subs(subs):
    SUBS_FILE.write_text(json.dumps(list(subs)), encoding="utf-8")

async def add_subscriber(chat_id):
    subs = load_subs()
    subs.add(chat_id)
    save_subs(subs)

async def remove_subscriber(chat_id):
    subs = load_subs()
    if chat_id in subs:
        subs.remove(chat_id)
        save_subs(subs)


# ===============================
#     فحص البث في تيك توك
# ===============================

async def check_live():
    """
    يعتمد على رابط /live الحقيقي:
    يبحث عن roomId / liveRoom / webcast / isLive
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(TIKTOK_URL, headers=headers, timeout=15) as resp:
                html = await resp.text()

        keywords = [
            "roomId",
            "liveRoom",
            "webcast",
            '"isLive":true',
            '"is_live":true',
            'liveRoomId'
        ]

        for k in keywords:
            if k in html:
                return True

        return False

    except Exception as e:
        logger.error(f"خطأ أثناء فحص البث: {e}")
        return False


# ===============================
#        إرسال الإشعارات
# ===============================

async def notify_all(message):
    subs = load_subs()
    for chat_id in subs:
        try:
            await bot.send_message(chat_id, message, disable_web_page_preview=True)
            await asyncio.sleep(0.05)
        except:
            pass


# ===============================
#        أوامر البوت
# ===============================

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    await add_subscriber(uid)

    txt = (
        "🎉 <b>تم تفعيل إشعارات بث الدكتور شاكر توفيق العاروري.</b>\n\n"
        "🔥 سيتم تنبيهك عند بداية البث ونهايته.\n\n"
        "الأوامر:\n"
        "/start — تفعيل الإشعارات\n"
        "/stop — إيقاف الإشعارات\n"
        "/status — معرفة حالة البث الآن\n"
    )

    if is_admin(uid):
        txt += "\n👑 <i>أنت مدير البوت.</i>"

    await message.answer(txt)


@dp.message_handler(commands=["stop"])
async def cmd_stop(message: types.Message):
    await remove_subscriber(message.chat.id)
    await message.answer("❌ تم إيقاف الإشعارات.")


# ===============================
#          أمر الحالة (معدل)
# ===============================

@dp.message_handler(commands=["status"])
async def cmd_status(message: types.Message):
    live = await check_live()
    if live:
        await message.answer(
            f"🔴 <b>البث شغّال الآن!</b>\n\n"
            f"🎥 رابط البث:\n{TIKTOK_URL}"
        )
    else:
        await message.answer(
            f"⚪ <b>لا يوجد بث مباشر حاليًا.</b>\n\n"
            f"📌 رابط الحساب:\n{TIKTOK_URL}"
        )


# ===============================
#     أوامر المدير
# ===============================

@dp.message_handler(commands=["مدير"])
async def admin_menu(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    text = (
        "🛡 <b>أوامر المدير:</b>\n\n"
        "/مشتركين — عدد المشتركين\n"
        "/ارسال — إرسال رسالة للجميع\n"
        "/احصائيات — معلومات البوت\n"
        "/تنبيه — اختبار تنبيه\n"
        "/اعادة — إعادة تشغيل البوت\n"
    )
    await message.answer(text)


@dp.message_handler(commands=["مشتركين"])
async def cmd_users(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    subs = load_subs()
    await message.answer(f"👥 عدد المشتركين: <b>{len(subs)}</b>")


@dp.message_handler(commands=["ارسال"])
async def cmd_sendall(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    content = message.text.replace("/ارسال", "").strip()
    if not content:
        return await message.answer("❗ اكتب هكذا:\n/ارسال نص الرسالة")

    await notify_all(content)
    await message.answer("📢 تم إرسال الرسالة للجميع.")


@dp.message_handler(commands=["احصائيات"])
async def cmd_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    subs = load_subs()
    await message.answer(
        f"📊 <b>إحصائيات البوت</b>\n\n"
        f"👥 المشتركين: {len(subs)}\n"
        f"🔗 رابط تيك توك:\n{TIKTOK_URL}\n"
        f"⏱ الفحص كل {CHECK_INTERVAL} ثانية\n"
    )


@dp.message_handler(commands=["تنبيه"])
async def cmd_test(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🔔 تنبيه تجريبي!")


@dp.message_handler(commands=["اعادة"])
async def cmd_reboot(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("♻️ إعادة تشغيل…")
    os._exit(0)


# ===============================
#     راصد البث (خلف الكواليس)
# ===============================

async def tiktok_watcher():
    global last_live_state

    last_live_state = await check_live()

    while True:
        live = await check_live()

        if last_live_state is False and live is True:
            await notify_all("🔴 <b>بدأ البث الآن!</b>\n" + TIKTOK_URL)

        elif last_live_state is True and live is False:
            await notify_all("⚪ <b>انتهى البث الآن.</b>\n" + TIKTOK_URL)

        last_live_state = live
        await asyncio.sleep(CHECK_INTERVAL)


async def on_start(dp):
    asyncio.create_task(tiktok_watcher())
    logger.info("🚀 البوت يعمل الآن")


# ===============================
#         تشغيل البوت
# ===============================

def main():
    executor.start_polling(dp, skip_updates=True, on_startup=on_start)


if __name__ == "__main__":
    main()
