import os
import re
import json
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, executor, types

# =======================
# إعداد المتغيرات من Koyeb
# =======================

TOKEN = os.getenv("TOKEN")
TIKTOK_URL = os.getenv("TIKTOK_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHECK_INTERVAL = 20  # كل كم ثانية يتم الفحص

if not TOKEN:
    raise Exception("❌ المتغير TOKEN غير موجود!")

if not TIKTOK_URL:
    raise Exception("❌ المتغير TIKTOK_URL غير موجود!")


# =======================
# إعداد البوت
# =======================

bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

subscribers = set()
last_live_state = None
last_room_id = None


# =======================
# استخراج room_id من HTML
# =======================

def extract_room_id(html: str):
    patterns = [
        r'"roomId":"(\d+)"',
        r'"room_id":"(\d+)"',
        r'roomId":"(\d+)"',
        r'"liveRoomId":"(\d+)"'
    ]
    for p in patterns:
        m = re.search(p, html)
        if m:
            return m.group(1)
    return None


# =======================
# فحص Webcast API لمعرفة حالة البث
# =======================

async def check_live_status():
    global last_room_id

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120 Safari/537.36"
        )
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(TIKTOK_URL, headers=headers) as resp:
                html = await resp.text()

        # استخراج room_id من HTML
        room_id = extract_room_id(html)
        if room_id:
            last_room_id = room_id

        if not last_room_id:
            # fallback HTML detection
            if '"isLive":true' in html or '"is_live":true' in html:
                return True
            return False

        # Webcast API
        api_url = f"https://webcast.tiktok.com/webcast/room/info/?aid=1988&room_id={last_room_id}"

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers=headers) as resp:
                data = await resp.json()

        # قراءة status من JSON
        try:
            status = data["data"]["room_info"]["status"]
            # 1 = بث شغّال
            if status == 1 or status == "1":
                return True
            return False
        except:
            return False

    except Exception:
        return False


# =======================
# إرسال تنبيه للجميع
# =======================

async def notify_all(text):
    for uid in list(subscribers):
        try:
            await bot.send_message(uid, text)
            await asyncio.sleep(0.05)
        except:
            pass


# =======================
# الأوامر الأساسية
# =======================

@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    subscribers.add(message.chat.id)
    await message.answer(
        "🔥 تم تفعيل إشعارات البث المباشر!\n"
        "سيصلك تنبيه عند بدء أو انتهاء البث.\n\n"
        "استخدم:\n/status لمعرفة حالة البث الآن."
    )


@dp.message_handler(commands=["stop"])
async def stop_cmd(message: types.Message):
    subscribers.discard(message.chat.id)
    await message.answer("❌ تم إيقاف الإشعارات لك.")


@dp.message_handler(commands=["status"])
async def status_cmd(message: types.Message):
    live = await check_live_status()
    if live:
        await message.answer(f"🔴 <b>البث شغّال الآن!</b>\n{TIKTOK_URL}")
    else:
        await message.answer(f"⚪ <b>لا يوجد بث حالياً.</b>\n{TIKTOK_URL}")


# =======================
# مراقبة البث تلقائياً
# =======================

async def watcher():
    global last_live_state

    while True:
        live = await check_live_status()

        # أول تشغيل
        if last_live_state is None:
            last_live_state = live

        # بدء البث
        if live and last_live_state is False:
            await notify_all(
                f"🔴 <b>بدأ البث الآن!</b>\n"
                f"🎥 مشاهدة البث:\n{TIKTOK_URL}"
            )

        # انتهاء البث
        if not live and last_live_state is True:
            await notify_all(
                "⚪ <b>انتهى البث الآن.</b>\n"
                "📌 سيتم تنبيهك عند بدء بث جديد."
            )

        last_live_state = live
        await asyncio.sleep(CHECK_INTERVAL)


async def on_startup(dp):
    asyncio.create_task(watcher())


# =======================
# تشغيل البوت
# =======================

def main():
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)


if __name__ == "__main__":
    main()
