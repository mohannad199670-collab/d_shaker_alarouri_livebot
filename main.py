import os
import math
import subprocess
import yt_dlp
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.apihelper import ApiTelegramException

# ================== إعداد التوكن ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN غير موجود في Environment variables")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ============= حدود الأحجام لتليجرام =============
# الحد الحقيقي تقريباً 50MB، نستخدم 49MB للتقسيم كهامش أمان
MAX_TELEGRAM_MB = 50
SPLIT_TARGET_MB = 49

MAX_TELEGRAM_BYTES = MAX_TELEGRAM_MB * 1024 * 1024
SPLIT_TARGET_BYTES = SPLIT_TARGET_MB * 1024 * 1024

# جلسات المستخدمين:
# {chat_id: {"url":..., "start":..., "end":..., "duration":..., "formats":{height:format_id}, "format_id":...}}
user_sessions = {}


# ========= دالة مساعدة: تحويل الوقت إلى ثواني =========
def parse_time_to_seconds(time_str: str) -> int:
    """
    يقبل: SS أو MM:SS أو HH:MM:SS
    ويرجع عدد الثواني
    """
    time_str = (time_str or "").strip()
    parts = time_str.split(":")
    if not parts or not all(p.isdigit() for p in parts):
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


# ========= دالة: جلب الجودات المتاحة (آمنة) =========
def safe_get_available_qualities(video_url: str):
    """
    يرجع dict مثل: {144: "91", 360: "18", 480: "94", ...}
    لو حصل خطأ من yt_dlp يرجّع {} بدون أن يرمي استثناء.
    """
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            # لتقليل مشاكل JS runtime
            "extractor_args": {
                "youtube": {
                    "player_client": ["default"]
                }
            },
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
                result[height] = fmt_id  # آخر واحد غالباً أفضل خيار

        return result
    except Exception as e:
        print("yt-dlp qualities error:", e)
        return {}  # نرجع فارغ ونكمل على best


# ========= دالة: تحميل الفيديو بالجودة المطلوبة =========
def download_video(video_url: str, format_id: str, output_template: str = "source.%(ext)s") -> str:
    """
    تحميل الفيديو من يوتيوب بالجودة المحددة ويعيد اسم الملف الناتج
    """
    ydl_opts = {
        "format": format_id,
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["default"]
            }
        },
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        filename = ydl.prepare_filename(info)

    return filename


# ========= دوال ffmpeg/ffprobe =========
def get_video_duration(input_file: str) -> float:
    """
    إرجاع مدة الفيديو بالثواني باستخدام ffprobe
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_file,
    ]
    out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
    return float(out)


def cut_video(input_file: str, start_seconds: int, duration_seconds: int, output_file: str = "cut.mp4") -> str:
    """
    قص جزء من الفيديو مع الاحتفاظ بالصوت والصورة (copy)
    """
    command = [
        "ffmpeg",
        "-y",
        "-ss", str(start_seconds),
        "-i", input_file,
        "-t", str(duration_seconds),
        "-c", "copy",
        output_file,
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output_file


def split_video_equal_parts_by_size(input_file: str, target_bytes: int):
    """
    تقسيم الفيديو إلى عدد أجزاء متساوية زمنياً
    بحيث الحجم التقريبي لكل جزء <= target_bytes.

    مثال:
      الحجم 100MB والهدف 49MB -> ceil(100/49)=3 أجزاء
      فيخرج تقريباً 49 + 49 + 2 ميغا
    """
    total_size = os.path.getsize(input_file)
    if total_size <= target_bytes:
        return [input_file]

    duration = get_video_duration(input_file)

    # عدد الأجزاء المطلوب
    parts_count = int(math.ceil(total_size / float(target_bytes)))
    if parts_count < 1:
        parts_count = 1

    part_duration = duration / parts_count  # مدة كل جزء بالثواني تقريباً

    parts = []
    for idx in range(parts_count):
        start = part_duration * idx
        # آخر جزء يأخذ ما تبقّى بالكامل
        this_dur = duration - start if idx == parts_count - 1 else part_duration

        out_name = f"part_{idx + 1}.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(start),
            "-i", input_file,
            "-t", str(this_dur),
            "-c", "copy",
            out_name,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        parts.append(out_name)

    return parts


# ================= /start =================
@bot.message_handler(commands=["start"])
def start(message):
    chat_id = message.chat.id
    user_sessions.pop(chat_id, None)

    bot.reply_to(
        message,
        "👋 أهلاً بك في بوت <b>قص فيديوهات من يوتيوب</b>\n\n"
        "أرسل الآن رابط فيديو يوتيوب (عادي أو بث محفوظ)."
    )
    bot.register_next_step_handler(message, handle_url)


# ========= خطوة: استلام الرابط =========
def handle_url(message):
    chat_id = message.chat.id
    url = (message.text or "").strip()

    user_sessions[chat_id] = {"url": url}

    bot.reply_to(
        message,
        "⏱️ أرسل وقت البداية بصيغة مثل:\n"
        "<code>00:01:20</code> أو <code>1:20</code> أو <code>80</code> ثانية."
    )
    bot.register_next_step_handler(message, handle_start_time)


# ========= خطوة: وقت البداية =========
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
        "مثال: <code>00:05:00</code> يعني بعد 5 دقائق من بداية الفيديو."
    )
    bot.register_next_step_handler(message, handle_end_time)


# ========= خطوة: وقت النهاية =========
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

    # فحص الجودات بطريقة آمنة
    qualities = safe_get_available_qualities(session["url"])

    if not qualities:
        # لو فشل الفحص، نكمل تلقائيًا بأفضل جودة
        session["format_id"] = "best"
        bot.send_message(
            chat_id,
            "⚠️ تعذّر تحديد الجودات المتاحة بشكل دقيق.\n"
            "سيتم استخدام أفضل جودة متاحة تلقائياً."
        )
        start_cutting(chat_id)
        return

    session["formats"] = qualities

    # أزرار الجودات المتاحة
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
        "سيتم الآن تحميل الفيديو وقصّ المقطع…",
        chat_id=chat_id,
        message_id=call.message.message_id
    )

    start_cutting(chat_id)


# ========= تنفيذ القص + التقسيم + الإرسال =========
def start_cutting(chat_id: int):
    session = user_sessions.get(chat_id)
    if not session:
        bot.send_message(chat_id, "⚠️ حصل خطأ في الجلسة. أرسل /start من جديد.")
        return

    url = session["url"]
    start_seconds = session["start"]
    duration = session["duration"]
    format_id = session.get("format_id", "best")

    bot.send_message(
        chat_id,
        "🔧 جاري تحميل الفيديو وقص المقطع… الرجاء الانتظار.\n"
        "قد يستغرق ذلك بعض الوقت حسب طول المقطع والجودة."
    )

    input_file = None
    cut_file = None
    parts_files = []
    oversized_error = False

    try:
        # تحميل الفيديو بالجودة المحددة
        input_file = download_video(url, format_id, output_template="source.%(ext)s")

        # قص الجزء المطلوب
        cut_file = cut_video(input_file, start_seconds, duration, "cut.mp4")

        # تقسيم إلى أجزاء متساوية زمنياً حتى لا يتجاوز أي جزء تقريباً 49MB
        parts_files = split_video_equal_parts_by_size(cut_file, SPLIT_TARGET_BYTES)
        total_parts = len(parts_files)

        if total_parts > 1:
            bot.send_message(
                chat_id,
                f"📦 حجم المقطع بعد القص كبير، سيتم تقسيمه تلقائياً إلى {total_parts} جزء(أجزاء) "
                f"بحيث لا يتجاوز كل جزء تقريباً {SPLIT_TARGET_MB}MB."
            )
        else:
            bot.send_message(chat_id, "📤 سيتم الآن إرسال المقطع كملف واحد…")

        # إرسال الأجزاء
        for idx, part_path in enumerate(parts_files, start=1):
            size_bytes = os.path.getsize(part_path)
            size_mb = size_bytes / (1024 * 1024)

            if size_bytes > MAX_TELEGRAM_BYTES:
                bot.send_message(
                    chat_id,
                    "❌ حجم المقطع أو أحد الأجزاء ما زال أكبر من الحد المسموح للبوت (≈50MB).\n"
                    f"حجم هذا الجزء ≈ {size_mb:.1f}MB.\n"
                    "حاول اختيار جودة أقل أو قص مدة أقصر."
                )
                oversized_error = True
                break

            caption = f"✅ المقطع جاهز 🎬\nجزء {idx}/{total_parts} • ≈ {size_mb:.1f}MB"
            bot.send_message(chat_id, f"📤 جاري إرسال الجزء {idx}/{total_parts}…")
            with open(part_path, "rb") as f:
                bot.send_document(chat_id, f, caption=caption)

        if not oversized_error:
            bot.send_message(
                chat_id,
                "✅ انتهى إرسال المقطع بالكامل!\n"
                "يمكنك الآن إرسال رابط يوتيوب جديد لقص مقطع آخر."
            )

    except ApiTelegramException as e:
        if "413" in str(e) or "Request Entity Too Large" in str(e):
            bot.send_message(
                chat_id,
                "❌ حجم المقطع أو أحد الأجزاء أكبر من المسموح في تليجرام للبوت.\n"
                "حاول اختيار جودة أقل أو قص مدة أقصر."
            )
        else:
            bot.send_message(chat_id, f"❌ خطأ من تليجرام أثناء الإرسال:\n<code>{e}</code>")
    except Exception as e:
        print("Error in start_cutting:", e)
        bot.send_message(chat_id, "❌ حدث خطأ أثناء التحميل أو القص.")
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


# ========= جعل البوت يشتغل تلقائيًا عند أي رابط يوتيوب =========
@bot.message_handler(func=lambda m: m.text and ("youtu.be/" in m.text or "youtube.com/" in m.text))
def auto_handle_youtube_link(message):
    """
    لو المستخدم أرسل رابط يوتيوب مباشرة (بدون /start)،
    نبدأ دورة جديدة تلقائياً.
    """
    if message.text.strip().startswith("/start"):
        return

    chat_id = message.chat.id
    user_sessions.pop(chat_id, None)
    handle_url(message)


# ========= تشغيل البوت =========
if __name__ == "__main__":
    print("🔥 Bot is running…")
    # skip_pending=True حتى لا يأخذ رسائل قديمة عند كل إعادة تشغيل
    bot.infinity_polling(skip_pending=True)
