import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

BOT_TOKEN = os.getenv("BOT_TOKEN")
TIKTOK_URL = "https://www.tiktok.com/@d.shakertawfiqalaroury"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

subscribers = set()
is_live_now = False  # حالة البث الحالية


# ----------------------------
# دالة فحص البث من تيك توك
# ----------------------------
async def check_live_status():
    async with aiohttp.ClientSession() as session:
        async with session.get(TIKTOK_URL, headers={"User-Agent": "Mozilla/5.0"}) as resp:
            html = await resp.text()
            return '"isLive":true' in html


# ----------------------------
# مهمة خلفية تفحص البث
# ----------------------------
async def live_monitor():
    global is_live_now

    while True:
        try:
            live = await check_live_status()

            # بداية البث
            if live and not is_live_now:
                is_live_now = True
                for user_id in subscribers:
                    await bot.send_message(
                        user_id,
                        "🔴 **الدكتور شاكر العاروري بدأ البث الآن!**\nادخل بسرعة ❤️"
                    )

            # نهاية البث
            elif not live and is_live_now:
                is_live_now = False
                for user_id in subscribers:
                    await bot.send_message(
                        user_id,
                        "⚫ **تم إنهاء البث المباشر**.\nنشوفكم في بث جديد بإذن الله."
                    )

        except Exception as e:
            print("Error:", e)

        await asyncio.sleep(20)  # فحص كل 20 ثانية


# ----------------------------
# أمر /start
# ----------------------------
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    subscribers.add(message.from_user.id)
    await message.answer("أهلاً بك ❤️\nسجّلت اشتراكك وستصلك إشعارات البث 🔔")


# ----------------------------
# تشغيل البوت
# ----------------------------
async def main():
    asyncio.create_task(live_monitor())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
