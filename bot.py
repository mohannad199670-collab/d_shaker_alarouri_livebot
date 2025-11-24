import os
import json
import datetime
from statistics import mean

from aiogram import Bot, Dispatcher, executor, types

# ============== الإعدادات الأساسية ==============

TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not TOKEN:
    raise Exception("❌ ضع TOKEN في متغيرات Koyeb")

bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

STREAMS_FILE = "streams.json"

# ============== دوال تخزين / تحميل البثوث ==============

def load_streams():
    if not os.path.exists(STREAMS_FILE):
        return []
    try:
        with open(STREAMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def save_streams(streams):
    with open(STREAMS_FILE, "w", encoding="utf-8") as f:
        json.dump(streams, f, ensure_ascii=False, indent=2)


def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID


# ============== أوامر المدير لإدخال بيانات البث ==============

"""
طريقة استخدام البوت (للإدخال اليدوي):

1) بعد انتهاء البث، ترسل الأمر:

/اضافة_بث
العنوان: شرح سورة الكهف
التاريخ: 2025-11-24
الوقت_البدء: 21:00
المدة_بالدقائق: 60
اعلى_مشاهدين: 1200
متوسط_مشاهدين: 750
اعلى_تعليقات: 340
اعلى_لايكات: 5500

البوت يحول هذه البيانات إلى سجل ويحفظه.
"""

@dp.message_handler(commands=["اضافة_بث"])
async def add_stream(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.answer("❗ هذا الأمر خاص بالمدير فقط.")

    # إزالة سطر الأمر نفسه
    text = message.text.replace("/اضافة_بث", "", 1).strip()
    if not text:
        example = (
            "/اضافة_بث\n"
            "العنوان: شرح سورة الكهف\n"
            "التاريخ: 2025-11-24\n"
            "الوقت_البدء: 21:00\n"
            "المدة_بالدقائق: 60\n"
            "اعلى_مشاهدين: 1200\n"
            "متوسط_مشاهدين: 750\n"
            "اعلى_تعليقات: 340\n"
            "اعلى_لايكات: 5500\n"
        )
        return await message.answer(
            "📥 أرسل بيانات البث بهذا الشكل:\n\n" + example
        )

    # تحويل النص إلى خطوط
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    data = {}

    for line in lines:
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            data[key] = value

    required_keys = [
        "العنوان",
        "التاريخ",
        "الوقت_البدء",
        "المدة_بالدقائق",
        "اعلى_مشاهدين",
        "متوسط_مشاهدين",
        "اعلى_تعليقات",
        "اعلى_لايكات",
    ]

    for k in required_keys:
        if k not in data:
            return await message.answer(f"❌ ينقص الحقل: <b>{k}</b>")

    # تحويل القيم
    try:
        date_str = data["التاريخ"]
        time_str = data["الوقت_البدء"]
        duration = int(data["المدة_بالدقائق"])
        peak_viewers = int(data["اعلى_مشاهدين"])
        avg_viewers = int(data["متوسط_مشاهدين"])
        top_comments = int(data["اعلى_تعليقات"])
        top_likes = int(data["اعلى_لايكات"])

        start_dt = datetime.datetime.fromisoformat(f"{date_str} {time_str}")
    except Exception as e:
        return await message.answer("❌ خطأ في تنسيق التاريخ/الأرقام، تأكد من الكتابة جيداً.")

    streams = load_streams()

    new_stream = {
        "id": len(streams) + 1,
        "title": data["العنوان"],
        "date": date_str,
        "start_time": time_str,
        "duration_min": duration,
        "peak_viewers": peak_viewers,
        "avg_viewers": avg_viewers,
        "top_comments": top_comments,
        "top_likes": top_likes,
    }

    streams.append(new_stream)
    save_streams(streams)

    await message.answer(
        "✅ تم حفظ البث بنجاح!\n"
        f"العنوان: <b>{new_stream['title']}</b>\n"
        f"التاريخ: {new_stream['date']} – الساعة: {new_stream['start_time']}\n"
        f"أعلى مشاهدين: {new_stream['peak_viewers']}\n"
        f"متوسط مشاهدين: {new_stream['avg_viewers']}"
    )


# ============== أوامر التقارير ==============

@dp.message_handler(commands=["تقرير_البثوث"])
async def report_streams(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.answer("❗ هذا الأمر للمدير فقط.")

    streams = load_streams()
    if not streams:
        return await message.answer("📭 لا يوجد أي بث محفوظ حتى الآن.")

    lines = ["📊 <b>قائمة مختصرة بالبثوث المحفوظة:</b>\n"]
    for s in streams[-10:]:
        lines.append(
            f"#{s['id']} – {s['date']} {s['start_time']}\n"
            f"العنوان: {s['title']}\n"
            f"ذروة: {s['peak_viewers']} – متوسط: {s['avg_viewers']}\n"
        )

    await message.answer("\n".join(lines))


@dp.message_handler(commands=["افضل_وقت"])
async def best_time(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.answer("❗ هذا الأمر للمدير فقط.")

    streams = load_streams()
    if not streams:
        return await message.answer("📭 لا يوجد بيانات بعد.")

    # نحسب المتوسط لكل ساعة/يوم
    by_hour = {}
    for s in streams:
        hour = s["start_time"].split(":")[0]
        key = hour
        if key not in by_hour:
            by_hour[key] = []
        by_hour[key].append(s["peak_viewers"])

    best_hour = None
    best_value = -1
    for h, vals in by_hour.items():
        avg_peak = mean(vals)
        if avg_peak > best_value:
            best_value = avg_peak
            best_hour = h

    await message.answer(
        "🕒 <b>أفضل ساعة للبث حسب أعلى معدل مشاهدين:</b>\n"
        f"الساعة: <b>{best_hour}:00</b>\n"
        f"بمتوسط ذروة: <b>{int(best_value)}</b> مشاهد."
    )


@dp.message_handler(commands=["احصائيات_عامة"])
async def global_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.answer("❗ هذا الأمر للمدير فقط.")

    streams = load_streams()
    if not streams:
        return await message.answer("📭 لا يوجد بيانات بعد.")

    peaks = [s["peak_viewers"] for s in streams]
    avgs = [s["avg_viewers"] for s in streams]
    durations = [s["duration_min"] for s in streams]

    txt = (
        "📈 <b>إحصائيات عامة للبثوث:</b>\n\n"
        f"🔹 عدد البثوث: <b>{len(streams)}</b>\n"
        f"🔹 متوسط أعلى المشاهدين: <b>{int(mean(peaks))}</b>\n"
        f"🔹 متوسط عدد المشاهدين: <b>{int(mean(avgs))}</b>\n"
        f"🔹 متوسط مدة البث: <b>{int(mean(durations))} دقيقة</b>\n"
    )

    await message.answer(txt)


# ============== /start العادي للمستخدمين ==============

@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    await message.answer(
        "👋 أهلاً بك.\n"
        "هذا بوت داخلي لتحليل بثوث الشيخ.\n"
        "هذه الأوامر الإدارية خاصة بالمدير فقط."
    )


# ============== تشغيل البوت ==============

def main():
    executor.start_polling(dp, skip_updates=True)


if __name__ == "__main__":
    main()
