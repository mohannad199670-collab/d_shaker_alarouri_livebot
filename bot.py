import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ================ الإعدادات من Koyeb ================

TOKEN = os.getenv("TOKEN")  # توكن البوت من BotFather
TIKTOK_URL = os.getenv("TIKTOK_URL")  # رابط صفحة البث
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # اختياري (آيديك أنت)

CHECK_INTERVAL = 20  # كل كم ثانية نفحص حالة البث
last_state = None     # الحالة السابقة: True/False/None

bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

subscribers = set()   # نخزن فيها المشتركين في الإشعارات


# ================ قائمة أزرار الأوامر ================

def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📢 تفعيل الإشعارات", callback_data="start_alerts"),
        InlineKeyboardButton("❌ إيقاف الإشعارات", callback_data="stop_alerts"),
        InlineKeyboardButton("🔎 حالة البث الآن", callback_data="check_status"),
    )
    if is_admin:
        kb.add(
            InlineKeyboardButton("👥 عدد المشتركين", callback_data="admin_users"),
            InlineKeyboardButton("📊 إحصائيات البوت", callback_data="admin_stats"),
        )
    return kb


def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID


# ================ فحص حالة البث من تيك توك ================

async def is_live() -> bool:
    """
    نحاول معرفة إن كان هناك بث من خلال HTML الصفحة.
    هذه أفضل طريقة بسيطة بدون Puppeteer.
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
            "Referer": "https://www.google.com",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(TIKTOK_URL, headers=headers, timeout=15) as resp:
                html = await resp.text()

        # كلمات تدل غالباً على وجود بث
        keywords = [
            '"isLive":true',
            '"is_live":true',
            '"liveRoom"',
            '"webcast"',
            'LIVE_EVENT',
        ]

        return any(k in html for k in keywords)

    except Exception:
        # في حالة الخطأ نرجع False حتى لا نخربط
        return False


# ================ إرسال الإشعارات ================

async def notify_all(text: str):
    for chat_id in list(subscribers):
        try:
            await bot.send_message(chat_id, text)
            await asyncio.sleep(0.05)
        except Exception:
            pass


# ================ أوامر /start /help /stop /status ================

@dp.message_handler(commands=["start", "help"])
async def cmd_start(message: types.Message):
    subscribers.add(message.chat.id)
    txt = (
        "🔥 <b>مرحباً بك في بوت إشعارات بث الدكتور شاكر.</b>\n\n"
        "سيتم تنبيهك تلقائياً عند <b>بدء البث</b> و <b>انتهائه</b>.\n\n"
        "استخدم الأزرار في الأسفل للتحكم."
    )
    await message.answer(
        txt,
        reply_markup=main_menu(is_admin(message.from_user.id))
    )


@dp.message_handler(commands=["stop"])
async def cmd_stop(message: types.Message):
    subscribers.discard(message.chat.id)
    await message.answer("❌ تم إيقاف الإشعارات لك.")


@dp.message_handler(commands=["status"])
async def cmd_status(message: types.Message):
    live = await is_live()
    if live:
        await message.answer(f"🔴 <b>البث شغّال الآن!</b>\n\n🎥 {TIKTOK_URL}")
    else:
        await message.answer(f"⚪ <b>لا يوجد بث مباشر حالياً.</b>\n\n📌 {TIKTOK_URL}")


# ================ التعامل مع الأزرار (Callback) ================

@dp.callback_query_handler()
async def callbacks(call: types.CallbackQuery):
    user_id = call.from_user.id

    # تفعيل الإشعارات
    if call.data == "start_alerts":
        subscribers.add(call.message.chat.id)
        await call.message.edit_text(
            "📢 تم تفعيل الإشعارات لك.",
            reply_markup=main_menu(is_admin(user_id))
        )
        return

    # إيقاف الإشعارات
    if call.data == "stop_alerts":
        subscribers.discard(call.message.chat.id)
        await call.message.edit_text(
            "❌ تم إيقاف الإشعارات.",
            reply_markup=main_menu(is_admin(user_id))
        )
        return

    # حالة البث الآن
    if call.data == "check_status":
        live = await is_live()
        if live:
            txt = f"🔴 <b>البث شغّال الآن!</b>\n\n🎥 {TIKTOK_URL}"
        else:
            txt = f"⚪ <b>لا يوجد بث مباشر حالياً.</b>\n\n📌 {TIKTOK_URL}"
        await call.message.edit_text(
            txt,
            reply_markup=main_menu(is_admin(user_id))
        )
        return

    # الأوامر الإدارية
    if not is_admin(user_id):
        await call.answer("❗ هذه الخيارات خاصة بالمدير فقط.", show_alert=True)
        return

    if call.data == "admin_users":
        await call.message.edit_text(
            f"👥 عدد المشتركين حالياً: <b>{len(subscribers)}</b>",
            reply_markup=main_menu(True)
        )
        return

    if call.data == "admin_stats":
        txt = (
            "📊 <b>إحصائيات البوت</b>\n\n"
            f"👥 المشتركين: {len(subscribers)}\n"
            f"🔗 رابط تيك توك:\n{TIKTOK_URL}\n"
            f"⏱ فترة الفحص: كل {CHECK_INTERVAL} ثانية\n"
        )
        await call.message.edit_text(
            txt,
            reply_markup=main_menu(True)
        )
        return


# ================ مراقبة البث تلقائياً ================

async def watcher():
    global last_state
    await asyncio.sleep(5)  # انتظار بسيط بعد تشغيل البوت

    while True:
        try:
            live = await is_live()

            # أول مرة
            if last_state is None:
                last_state = live

            # انتقال من لايف = False إلى لايف = True
            if live and last_state is False:
                msg = (
                    "🔴 <b>تم بدء البث الآن!</b>\n\n"
                    f"🎥 ادخل الآن:\n{TIKTOK_URL}"
                )
                await notify_all(msg)

            # انتقال من لايف = True إلى لايف = False
            if not live and last_state is True:
                msg = (
                    "⚪ <b>انتهى البث الآن.</b>\n\n"
                    "📌 سيتم تنبيهك عند بدء بث جديد بإذن الله."
                )
                await notify_all(msg)

            last_state = live

        except Exception:
            # لا نوقف البوت لو صار خطأ، نكمل
            pass

        await asyncio.sleep(CHECK_INTERVAL)


async def on_startup(dp):
    asyncio.create_task(watcher())


def main():
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)


if __name__ == "__main__":
    main()
