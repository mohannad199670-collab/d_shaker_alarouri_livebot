import os
import math
import subprocess

import yt_dlp
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.apihelper import ApiTelegramException

# ================= إعداد التوكن =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN غير موجود في Environment variables")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# جلسات المستخدمين لحفظ البيانات بين الخطوات
# {chat_id: {"url":..., "start":..., "end":..., "duration":..., "formats":{height:format_id}, "format_id":...}}
user_sessions = {}


# ========= دالة مساعدة: تحويل الوقت إلى ثواني =========
def parse_time_to_seconds(time_str: str) -> int:
    """
    يقبل: SS أو MM:SS أو HH:MM:SS
    ويرجع عدد الثواني
    """
    time_str = time_str.strip()
    parts = time_str.split(":")

    if not all(p.isdigit() for p in parts):
        raise ValueError("صيغة وقت غير صحيحة")

    if len(parts) == 1:
        s = int(parts[0])
        return s
    elif len(parts) == 2:
        m, s = map(int, parts)
        return m * 60 + s
    elif len(parts) == 3:
        h, m, s = map(int, parts)
        return h * 3600 + m * 60 + s
    else:
        raise ValueError("صيغة وقت غير مدعومة")


# ========= دالة: جلب الجودات المتاحة =========
def get_available_qualities(video_url: str):
    """
    يرجع dict مثل: {144: "91", 360: "18", 480: "94", ...}
    حسب الجودات الموجودة فعلاً في الفيديو
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        # لتخفيف مشاكل SABR و JS
        "extractor_args": {"youtube": {"player_client": ["default"]}},
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        formats = info.get("formats", [])

    target_heights = [144, 240, 360, 480, 720, 1080]
    result = {}

    for f in formats:
        height = f.get("height")
        fmt_id = f.get("format_id")
        if not height or not fmt_id:
            continue
        if height in target_heights:
            # آخر واحد غالباً أفضل / أحدث
            result[height] = fmt_id

    return result


# ========= دالة: تحميل الفيديو بالجودة المطلوبة =========
def download_video(video_url: str, format_id: str, output_name: str = "source.mp4") -> str:
    """
    يقوم بتحميل الفيديو من يوتيوب بالجودة المحددة
    ويعيد اسم الملف الناتج
    """
    ydl_opts = {
        "format": format_id,
        "outtmpl": "source.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {"youtube": {"player_client": ["default"]}},
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        filename = ydl.prepare_filename(info)

    # نعيد الاسم (قد يكون .mp4 أو غيره، لكن ffmpeg يتعامل معه)
    return filename


# ========= دالة: جلب مدة الفيديو =========
def get_video_duration(filename: str) -> float:
    """
    يرجع مدة الفيديو بالثواني (float)
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        filename,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip())


# ========= دالة: قص الفيديو مع إعادة ترميز =========
def cut_video_reencode(
    input_file: str,
    start_seconds: int,
    duration_seconds: int,
    output_file: str = "cut.mp4",
):
    """
    يقص جزء من الفيديو مع إعادة ترميز (لضمان الصوت والفيديو يعملان دائماً)
    """
    command = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_seconds),
        "-i",
        input_file,
        "-t",
        str(duration_seconds),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        output_file,
    ]
    subprocess.run(command, check=True)
    return output_file


# ========= دالة: تقسيم الفيديو إلى عدة أجزاء حسب الحجم =========
def split_video_by_size(input_file: str, max_size_mb: int = 48):
    """
    يقسم ملف الفيديو إلى عدة أجزاء بحيث لا يتجاوز حجم كل جزء max_size_mb تقريباً
    يرجع قائمة بأسماء الملفات للأجزاء الناتجة
    """
    max_bytes = max_size_mb * 1024 * 1024
    total_size = os.path.getsize(input_file)

    # إذا كان الحجم أصلاً أقل من الحد، لا حاجة للتقسيم
    if total_size <= max_bytes:
        return [input_file]

    # مدة الفيديو الكاملة
    total_duration = get_video_duration(input_file)

    # عدد الأجزاء المطلوب (يأخذ في الحسبان الباقي أيضاً)
    num_parts = math.ceil(total_size / max_bytes)

    # مدة كل جزء (الأخير سيأخذ المتبقي تلقائياً)
    part_duration = total_duration / num_parts

    parts_files = []
    for i in range(num_parts):
        start = i * part_duration
        out_name = f"part_{i + 1}.mp4"

        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-i",
            input_file,
        ]

        # الأجزاء ما عدا الأخير نحدد لها -t
        if i < num_parts - 1:
            cmd += ["-t", str(part_duration)]

        # تقسيم بدون إعادة ترميز ثانية
        cmd += [
            "-c",
            "copy",
            out_name,
        ]

        subprocess.run(cmd, check=True)
        parts_files.append(out_name)

    return parts_files


# ========= /start =========
@bot.message_handler(commands=["start"])
def start(message):
    chat_id = message.chat.id
    user_sessions.pop(chat_id, None)  # إعادة ضبط الجلسة

    bot.reply_to(
        message,
        "👋 أهلاً بك في بوت <b>قص فيديوهات من يوتيوب</b>\n\n"
        "أرسل الآن رابط فيديو يوتيوب (عادي أو بث محفوظ).",
    )


# ========= استقبال أي رابط يوتيوب مباشرة =========
@bot.message_handler(func=lambda m: m.text and ("youtu.be" in m.text or "youtube.com" in m.text))
def auto_handle_youtube_link(message):
    """
    لو المستخدم أرسل رابط يوتيوب مباشرة بدون /start
    نبدأ دورة جديدة تلقائياً
    """
    chat_id = message.chat.id
    # نلغي أي جلسة قديمة
    user_sessions.pop(chat_id, None)
    handle_url(message)


# ========= خطوة: استلام الرابط =========
def handle_url(message):
    chat_id = message.chat.id
    url = message.text.strip()

    # تخزين الرابط في الجلسة
    user_sessions[chat_id] = {"url": url}

    bot.reply_to(
        message,
        "⏱️ أرسل وقت البداية بصيغة مثل:\n"
        "<code>00:01:20</code> أو <code>1:20</code> أو <code>80</code> ثانية.",
    )
    bot.register_next_step_handler(message, handle_start_time)


# ========= خطوة: استلام وقت البداية =========
def handle_start_time(message):
    chat_id = message.chat.id
    session = user_sessions.get(chat_id)
    if not session:
        bot.reply_to(message, "⚠️ حصل خطأ في الجلسة. أرسل /start من جديد.")
        return

    try:
        start_seconds = parse_time_to_seconds(message.text)
    except ValueError:
        bot.reply_to(message, "⚠️ صيغة وقت البداية غير صحيحة. أعد إرسال وقت البداية بشكل صحيح.")
        bot.register_next_step_handler(message, handle_start_time)
        return

    session["start"] = start_seconds

    bot.reply_to(
        message,
        "⏱️ الآن أرسل وقت النهاية.\n"
        "مثال: <code>00:05:00</code> يعني بعد 5 دقائق من بداية الفيديو.",
    )
    bot.register_next_step_handler(message, handle_end_time)


# ========= خطوة: استلام وقت النهاية =========
def handle_end_time(message):
    chat_id = message.chat.id
    session = user_sessions.get(chat_id)
    if not session:
        bot.reply_to(message, "⚠️ حصل خطأ في الجلسة. أرسل /start من جديد.")
        return

    try:
        end_seconds = parse_time_to_seconds(message.text)
    except ValueError:
        bot.reply_to(message, "⚠️ صيغة وقت النهاية غير صحيحة. أعد إرسال وقت النهاية بشكل صحيح.")
        bot.register_next_step_handler(message, handle_end_time)
        return

    start_seconds = session["start"]
    if end_seconds <= start_seconds:
        bot.reply_to(message, "⚠️ يجب أن يكون وقت النهاية أكبر من وقت البداية. أعد الإرسال.")
        bot.register_next_step_handler(message, handle_end_time)
        return

    duration = end_seconds - start_seconds
    session["end"] = end_seconds
    session["duration"] = duration

    bot.reply_to(message, "⏳ يتم فحص الجودات المتاحة للفيديو… الرجاء الانتظار.")

    # الآن نأخذ الجودات
    try:
        qualities = get_available_qualities(session["url"])
    except Exception as e:
        print("Error getting qualities:", e)
        bot.reply_to(
            message,
            "⚠️ لم نتمكن من فحص الجودات بدقة.\n"
            "سيتم استخدام أفضل جودة متاحة تلقائياً.",
        )
        session["format_id"] = "best"
        start_cutting(chat_id)
        return

    if not qualities:
        bot.reply_to(
            message,
            "⚠️ لم يتم العثور على جودات قياسية (144p–1080p).\n"
            "سيتم استخدام أفضل جودة متاحة تلقائياً.",
        )
        session["format_id"] = "best"
        start_cutting(chat_id)
        return

    # حفظ الجودات في الجلسة
    session["formats"] = qualities

    # إنشاء أزرار الجودات المتاحة فقط
    markup = InlineKeyboardMarkup()
    row = []
    for h in [144, 240, 360, 480, 720, 1080]:
        if h in qualities:
            btn = InlineKeyboardButton(text=f"{h}p", callback_data=f"q_{h}")
            row.append(btn)
            if len(row) == 3:
                markup.row(*row)
                row = []
    if row:
        markup.row(*row)

    bot.send_message(
        chat_id,
        "🎚️ <b>اختر الجودة</b> من الأزرار بالأسفل:",
        reply_markup=markup,
    )


# ========= التعامل مع ضغط زر الجودة =========
@bot.callback_query_handler(func=lambda call: call.data.startswith("q_"))
def handle_quality_callback(call):
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id)
    if not session:
        bot.answer_callback_query(call.id, "انتهت الجلسة. أرسل /start من جديد.", show_alert=True)
        return

    try:
        height = int(call.data.split("_")[1])
    except Exception:
        bot.answer_callback_query(call.id, "خطأ في اختيار الجودة.", show_alert=True)
        return

    fmt_id = session.get("formats", {}).get(height)
    if not fmt_id:
        bot.answer_callback_query(call.id, "هذه الجودة غير متاحة.", show_alert=True)
        return

    session["format_id"] = fmt_id

    bot.answer_callback_query(call.id, f"تم اختيار الجودة: {height}p ✅", show_alert=False)
    bot.edit_message_text(
        f"✅ تم اختيار الجودة: <b>{height}p</b>\n"
        "سيتم الآن تحميل الفيديو وقصّ المقطع وإرساله…",
        chat_id=chat_id,
        message_id=call.message.message_id,
    )

    # بدء عملية القص والإرسال
    start_cutting(chat_id)


# ========= تنفيذ القص والإرسال =========
def start_cutting(chat_id: int):
    session = user_sessions.get(chat_id)
    if not session:
        bot.send_message(chat_id, "⚠️ حصل خطأ في الجلسة. أرسل /start من جديد.")
        return

    url = session["url"]
    start_seconds = session["start"]
    duration = session["duration"]
    format_id = session.get("format_id", "best")

    # رسالة بدء التحميل والقص
    bot.send_message(
        chat_id,
        "🔧 جاري تحميل الفيديو وقص المقطع… الرجاء الانتظار.\n"
        "قد يستغرق ذلك بعض الوقت حسب طول المقطع والجودة.",
    )

    input_file = None
    cut_file = None
    parts_files = []

    try:
        # تحميل الفيديو بالجودة المحددة
        input_file = download_video(url, format_id)

        # قص الفيديو مع إعادة ترميز (لضمان الصوت)
        cut_file = "cut.mp4"
        cut_video_reencode(input_file, start_seconds, duration, cut_file)

        # تقسيم حسب الحجم (48MB تقريباً لكل جزء)
        parts_files = split_video_by_size(cut_file, max_size_mb=48)

        if len(parts_files) > 1:
            bot.send_message(
                chat_id,
                "📦 حجم المقطع بعد القص كبير، سيتم تقسيمه تلقائياً إلى أجزاء "
                "لا يتجاوز كل منها تقريباً 48MB…",
            )

        total_parts = len(parts_files)

        # إرسال الأجزاء كفيديو عادي
        for idx, part_path in enumerate(parts_files, start=1):
            size_mb = os.path.getsize(part_path) / (1024 * 1024)
            caption = f"جزء {idx}/{total_parts} 🎬 (≈ {size_mb:.1f}MB)"

            with open(part_path, "rb") as f:
                try:
                    bot.send_video(
                        chat_id,
                        f,
                        caption=caption,
                        supports_streaming=True,
                    )
                except ApiTelegramException as e:
                    # مشكلة حجم مثلاً 413
                    if "413" in str(e) or "Request Entity Too Large" in str(e):
                        bot.send_message(
                            chat_id,
                            "❌ حجم المقطع أو أحد الأجزاء أكبر من المسموح في تلجرام.\n"
                            "حاول اختيار جودة أقل أو مدة أقصر.",
                        )
                    else:
                        bot.send_message(
                            chat_id,
                            f"❌ خطأ من تلجرام أثناء إرسال الجزء {idx}:\n<code>{e}</code>",
                        )
                    # لا جدوى من متابعة إرسال باقي الأجزاء
                    break

        else:
            # فقط لو لم يحدث break في الحلقة
            bot.send_message(chat_id, "✅ انتهى! أرسل رابطاً جديداً لقص مقطع آخر.")

    except Exception as e:
        print("Error in start_cutting:", e)
        bot.send_message(chat_id, "❌ حدث خطأ أثناء التحميل أو القص. حاول مرة أخرى أو برابط مختلف.")
    finally:
        # تنظيف الملفات المؤقتة
        try:
            if input_file and os.path.exists(input_file):
                os.remove(input_file)
            if cut_file and os.path.exists(cut_file):
                os.remove(cut_file)
            for p in parts_files:
                if os.path.exists(p):
                    os.remove(p)
        except Exception:
            pass

        # إنهاء الجلسة بعد الانتهاء
        user_sessions.pop(chat_id, None)


# ========= تشغيل البوت =========
if __name__ == "__main__":
    print("🔥 Bot is running…")
    # skip_pending=True حتى لا يأخذ رسائل قديمة عند كل إعادة تشغيل
    bot.infinity_polling(skip_pending=True)
