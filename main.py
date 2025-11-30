import os
import re
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

# ================= إعدادات الحجم =================
# الحد الأقصى لحجم الجزء الواحد (ميغابايت)
MAX_PART_SIZE_MB = 49
MAX_PART_SIZE_BYTES = MAX_PART_SIZE_MB * 1024 * 1024

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
            # آخر واحد غالباً أفضل / أحدث لهذه الجودة
            result[height] = fmt_id

    return result


# ========= دالة: تحميل الفيديو بالجودة المطلوبة =========
def download_video(video_url: str, format_id: str) -> str:
    """
    يقوم بتحميل الفيديو من يوتيوب بالجودة المحددة
    ويعيد اسم الملف الناتج
    """
    ydl_opts = {
        "format": format_id,
        "outtmpl": "source.%(ext)s",
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        filename = ydl.prepare_filename(info)

    return filename


# ========= دالة: قص الفيديو باستخدام ffmpeg =========
def cut_video(input_file: str, start_seconds: int, duration_seconds: int, output_file: str = "cut.mp4"):
    """
    يقص جزء من الفيديو بدون إعادة ترميز (copy) لسرعة أعلى
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
        "-c",
        "copy",
        output_file,
    ]

    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output_file


# ========= دالة: الحصول على مدة الفيديو بالثواني عبر ffprobe =========
def get_video_duration_seconds(file_path: str) -> float:
    """
    تستخدم ffprobe للحصول على مدة الفيديو بالثواني
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nw=1:nk=1",
        file_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("فشل الحصول على مدة الفيديو عبر ffprobe")
    duration_str = result.stdout.strip()
    return float(duration_str)


# ========= دالة: تقسيم الفيديو إلى أجزاء حجمها تقريباً 49MB =========
def split_video_by_size(input_file: str, target_bytes: int = MAX_PART_SIZE_BYTES):
    """
    تقسيم الفيديو إلى أجزاء متتالية بحيث يكون كل جزء تقريباً بالحجم المطلوب
    نعتمد على حساب الـ bitrate والمدة لتقدير مدة كل جزء
    """
    # حجم الملف الإجمالي
    total_size_bytes = os.path.getsize(input_file)
    if total_size_bytes <= target_bytes:
        # لا يحتاج تقسيم
        return [input_file]

    # الحصول على مدة الفيديو بالكامل
    duration = get_video_duration_seconds(input_file)  # بالثواني

    # متوسط bitrate بالـ bit/s
    avg_bitrate_bps = (total_size_bytes * 8) / duration

    # تقدير مدة الجزء الواحد
    # target_bytes -> target_bits -> مدة تقريبية = target_bits / bitrate
    approx_part_duration = int((target_bytes * 8) / avg_bitrate_bps)
    # ضمان ألا تقل مدة الجزء عن 30 ثانية (احتياط)
    if approx_part_duration < 30:
        approx_part_duration = 30

    # عدد الأجزاء التقريبي
    num_parts = math.ceil(duration / approx_part_duration)

    parts_files = []
    for i in range(num_parts):
        start = i * approx_part_duration
        # عدم تجاوز النهاية
        remaining = duration - start
        if remaining <= 0:
            break
        this_part_duration = min(approx_part_duration, remaining)

        part_file = f"part_{i+1}.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(int(start)),
            "-i",
            input_file,
            "-t",
            str(int(this_part_duration)),
            "-c",
            "copy",
            part_file,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # إذا كان الجزء بعد القص أكبر من target_bytes بكثير، نبقيه (تلغرام يسمح حتى 2GB)
        # لكن غالباً سيكون قريب من المطلوب
        parts_files.append(part_file)

    return parts_files


# ========= /start =========
@bot.message_handler(commands=["start"])
def start(message):
    chat_id = message.chat.id
    user_sessions.pop(chat_id, None)  # إعادة ضبط الجلسة

    bot.reply_to(
        message,
        "👋 أهلاً بك في بوت <b>قص فيديوهات من يوتيوب</b>\n\n"
        "أرسل الآن رابط فيديو يوتيوب (عادي أو بث محفوظ)."
    )


# ========= اكتشاف رابط يوتيوب في أي رسالة =========
YOUTUBE_REGEX = re.compile(
    r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/[^\s]+",
    re.IGNORECASE,
)


@bot.message_handler(func=lambda m: m.text is not None and YOUTUBE_REGEX.search(m.text.strip()))
def handle_youtube_link(message):
    """
    أي رسالة تحتوي رابط يوتيوب تبدأ من هنا
    """
    chat_id = message.chat.id
    url_match = YOUTUBE_REGEX.search(message.text.strip())
    url = url_match.group(0)

    # بدء جلسة جديدة لهذا المستخدم
    user_sessions[chat_id] = {"url": url}

    bot.reply_to(
        message,
        "✅ تم استقبال رابط يوتيوب.\n"
        "⏱️ الآن أرسل وقت البداية بصيغة مثل:\n"
        "<code>00:01:20</code> أو <code>1:20</code> أو <code>80</code> ثانية."
    )
    bot.register_next_step_handler(message, handle_start_time)


# ========= خطوة: استلام وقت البداية =========
def handle_start_time(message):
    chat_id = message.chat.id
    session = user_sessions.get(chat_id)
    if not session:
        bot.reply_to(message, "⚠️ حصل خطأ في الجلسة. أرسل الرابط من جديد.")
        return

    try:
        start_seconds = parse_time_to_seconds(message.text)
    except ValueError:
        bot.reply_to(message, "⚠️ صيغة وقت غير صحيحة. أعد إرسال وقت البداية بشكل صحيح.")
        bot.register_next_step_handler(message, handle_start_time)
        return

    session["start"] = start_seconds

    bot.reply_to(
        message,
        "⏱️ الآن أرسل وقت النهاية.\n"
        "مثال: <code>00:05:00</code> يعني بعد 5 دقائق من بداية الفيديو."
    )
    bot.register_next_step_handler(message, handle_end_time)


# ========= خطوة: استلام وقت النهاية =========
def handle_end_time(message):
    chat_id = message.chat.id
    session = user_sessions.get(chat_id)
    if not session:
        bot.reply_to(message, "⚠️ حصل خطأ في الجلسة. أرسل الرابط من جديد.")
        return

    try:
        end_seconds = parse_time_to_seconds(message.text)
    except ValueError:
        bot.reply_to(message, "⚠️ صيغة وقت غير صحيحة. أعد إرسال وقت النهاية بشكل صحيح.")
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
        bot.reply_to(message, "❌ حدث خطأ أثناء فحص الجودات من يوتيوب.")
        return

    if not qualities:
        bot.reply_to(
            message,
            "⚠️ لم يتم العثور على جودات قياسية (144p–1080p).\n"
            "سيتم استخدام أفضل جودة متاحة تلقائياً."
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
        reply_markup=markup
    )


# ========= التعامل مع ضغط زر الجودة =========
@bot.callback_query_handler(func=lambda call: call.data.startswith("q_"))
def handle_quality_callback(call):
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id)
    if not session:
        bot.answer_callback_query(call.id, "انتهت الجلسة. أرسل الرابط من جديد.", show_alert=True)
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
        "سيتم الآن قصّ المقطع وتجهيزه للإرسال…",
        chat_id=chat_id,
        message_id=call.message.message_id
    )

    # بدء عملية القص والإرسال
    start_cutting(chat_id)


# ========= تنفيذ القص والإرسال + التقسيم =========
def start_cutting(chat_id):
    session = user_sessions.get(chat_id)
    if not session:
        bot.send_message(chat_id, "⚠️ حصل خطأ في الجلسة. أرسل الرابط من جديد.")
        return

    url = session["url"]
    start_seconds = session["start"]
    duration = session["duration"]
    format_id = session.get("format_id", "best")

    # رسالة بدء التحميل والقص
    bot.send_message(
        chat_id,
        "🔧 جاري تحميل الفيديو وقص المقطع… الرجاء الانتظار.\n"
        "قد يستغرق ذلك بعض الوقت حسب طول المقطع والجودة."
    )

    input_file = None
    cut_file = "cut.mp4"

    try:
        # تحميل الفيديو بالجودة المحددة
        input_file = download_video(url, format_id)

        # قص الفيديو للمدة المطلوبة
        cut_video(input_file, start_seconds, duration, cut_file)

        # فحص الحجم بعد القص
        cut_size = os.path.getsize(cut_file)

        if cut_size <= MAX_PART_SIZE_BYTES:
            # مقطع واحد فقط
            bot.send_message(chat_id, "📤 جاري إرسال الفيديو كملف واحد… الرجاء الانتظار.")
            with open(cut_file, "rb") as f:
                bot.send_document(chat_id, f, caption="✅ المقطع جاهز 🎬")
        else:
            # تقسيم إلى عدة أجزاء
            bot.send_message(
                chat_id,
                f"📦 حجم المقطع بعد القص كبير، سيتم تقسيمه تلقائياً إلى أجزاء "
                f"لا يتجاوز كل منها تقريباً {MAX_PART_SIZE_MB}MB…"
            )

            parts = split_video_by_size(cut_file, MAX_PART_SIZE_BYTES)
            total_parts = len(parts)

            for idx, part_path in enumerate(parts, start=1):
                part_size_mb = os.path.getsize(part_path) / (1024 * 1024)
                bot.send_message(
                    chat_id,
                    f"📤 جاري إرسال الجزء {idx}/{total_parts} "
                    f"(≈ {part_size_mb:.1f}MB)…"
                )
                with open(part_path, "rb") as f:
                    bot.send_document(
                        chat_id,
                        f,
                        caption=f"🎬 جزء {idx}/{total_parts}"
                    )

            bot.send_message(
                chat_id,
                "✅ تم إرسال جميع الأجزاء بنجاح.\n"
                "يمكنك الآن إرسال رابط يوتيوب جديد لقص مقطع آخر."
            )

    except ApiTelegramException as e:
        # في حال ظهور خطأ من تلغرام 413 أو غيره
        if "413" in str(e) or "Request Entity Too Large" in str(e):
            bot.send_message(
                chat_id,
                "❌ حجم المقطع أو أحد الأجزاء أكبر من المسموح في تلجرام.\n"
                "حاول اختيار جودة أقل أو مدة أقصر."
            )
        else:
            bot.send_message(chat_id, f"❌ خطأ من تلجرام أثناء الإرسال:\n<code>{e}</code>")
    except Exception as e:
        print("Error in start_cutting:", e)
        bot.send_message(chat_id, "❌ حدث خطأ أثناء التحميل أو القص أو التقسيم.")
    finally:
        # تنظيف الملفات المؤقتة
        try:
            if input_file and os.path.exists(input_file):
                os.remove(input_file)
            if os.path.exists(cut_file):
                os.remove(cut_file)
            # إزالة الأجزاء إن وجدت
            for fname in os.listdir("."):
                if fname.startswith("part_") and fname.endswith(".mp4"):
                    try:
                        os.remove(fname)
                    except Exception:
                        pass
        except Exception:
            pass

        # بعد كل شيء، تبقى الجلسة القديمة، لكن عند إرسال رابط يوتيوب جديد
        # سيتم استبدالها تلقائياً في handle_youtube_link


# ========= تشغيل البوت =========
if __name__ == "__main__":
    print("🔥 Bot is running…")
    # skip_pending=True حتى لا يأخذ رسائل قديمة عند كل إعادة تشغيل
    bot.infinity_polling(skip_pending=True)
