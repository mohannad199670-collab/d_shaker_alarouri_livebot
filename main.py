import os
import math
import subprocess

import yt_dlp
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.apihelper import ApiTelegramException

# ============ إعداد التوكن ============
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN غير موجود في Environment variables")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ملف الكوكيز (من الإضافة التي صدّرناها)
COOKIE_FILE = "cookies.txt"

# حد حجم كل جزء (بالبايت) ≈ 48MB
MAX_PART_MB = 48
MAX_PART_BYTES = MAX_PART_MB * 1024 * 1024

# جلسات المستخدمين
# {chat_id: {"url":..., "start":..., "end":..., "duration":..., "formats":{height:format_id}, "format_id":...}}
user_sessions = {}


# ========= دالة مساعدة: خيارات yt_dlp مع الكوكيز =========
def make_ydl_opts(base_opts=None):
    """
    يُرجع قاموس خيارات جاهز لـ yt_dlp مع استخدام cookies.txt إن وجد.
    """
    if base_opts is None:
        base_opts = {}

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # محاولة تقليل مشاكل الـ JS
        "extractor_args": {
            "youtube": {
                "player_client": ["web"]
            }
        },
    }
    opts.update(base_opts)

    if os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE

    return opts


# ========= دالة: تحويل الوقت إلى ثواني =========
def parse_time_to_seconds(time_str: str) -> int:
    """
    يقبل: SS أو MM:SS أو HH:MM:SS
    ويرجع عدد الثواني.
    """
    time_str = time_str.strip()
    parts = time_str.split(":")

    if not all(p.isdigit() for p in parts):
        raise ValueError("صيغة وقت غير صحيحة")

    if len(parts) == 1:
        # ثواني فقط
        return int(parts[0])
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
    يرجع dict مثل: {144: "18", 360: "18", 480: "135", ...}
    يأخذ فقط الصيغ التي تحتوي Audio + Video حتى لا يختفي الصوت.
    """
    ydl_opts = make_ydl_opts({
        "skip_download": True,
    })

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        formats = info.get("formats", [])

    target_heights = [144, 240, 360, 480, 720, 1080]
    result = {}

    for f in formats:
        height = f.get("height")
        fmt_id = f.get("format_id")
        acodec = f.get("acodec")
        vcodec = f.get("vcodec")
        ext = f.get("ext")

        # نأخذ فقط الصيغ التي تحتوي صوت + صورة، وغالبًا mp4
        if (
            not height
            or not fmt_id
            or acodec in (None, "none")
            or vcodec in (None, "none")
        ):
            continue

        if height in target_heights:
            # آخر واحد غالبًا أفضل
            result[height] = fmt_id

    return result


# ========= دالة: تحميل الفيديو بالجودة المطلوبة =========
def download_video(video_url: str, format_id: str, output_name: str = "source.mp4") -> str:
    """
    يقوم بتحميل الفيديو (مع الصوت) بالجودة المحددة، ويعيد اسم الملف الناتج.
    """
    # نجبر yt_dlp أن يأخذ نفس format_id (progressive) إن وجد،
    # وإن لم يوجد يحاول دمج أفضل صوت مع نفس الفيديو.
    fmt_selector = f"bv*[format_id={format_id}]+ba/b[format_id={format_id}]/b[height<=?2160]"

    ydl_opts = make_ydl_opts({
        "format": fmt_selector,
        "outtmpl": "source.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    })

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        filename = ydl.prepare_filename(info)

    return filename  # اسم الملف الحقيقي (مثلاً source.mp4 أو غيره)


# ========= دالة: قص الفيديو باستخدام ffmpeg =========
def cut_video(input_file: str, start_seconds: int, duration_seconds: int, output_file: str = "cut.mp4"):
    """
    يقص جزء من الفيديو بدون إعادة ترميز (copy) لسرعة أعلى.
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


# ========= دالة: تقسيم الفيديو إلى أجزاء بحجم ≈48MB =========
def split_video_by_size(input_file: str, duration_seconds: int, max_bytes: int = MAX_PART_BYTES):
    """
    تقسم ملف الفيديو إلى عدة أجزاء تقريبية بناءً على الحجم.
    ترجع قائمة بأسماء الملفات للأجزاء.
    لا يتم تجاهل أي جزء، حتى لو كان صغيرًا جدًا.
    """
    size_bytes = os.path.getsize(input_file)
    if size_bytes <= max_bytes:
        return [input_file]

    if duration_seconds <= 0:
        return [input_file]

    bytes_per_second = size_bytes / duration_seconds
    if bytes_per_second == 0:
        return [input_file]

    max_seconds_per_part = int(max_bytes / bytes_per_second)
    if max_seconds_per_part <= 0:
        max_seconds_per_part = 1

    parts_files = []
    current_start = 0
    part_index = 1

    while current_start < duration_seconds:
        remaining = duration_seconds - current_start
        part_duration = min(max_seconds_per_part, remaining)

        part_name = f"part_{part_index}.mp4"
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            str(current_start),
            "-i",
            input_file,
            "-t",
            str(part_duration),
            "-c",
            "copy",
            part_name,
        ]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        parts_files.append(part_name)
        current_start += part_duration
        part_index += 1

    return parts_files


# ========= /start =========
@bot.message_handler(commands=["start"])
def start_cmd(message):
    chat_id = message.chat.id
    user_sessions.pop(chat_id, None)

    bot.reply_to(
        message,
        "👋 أهلاً بك في بوت <b>قص فيديوهات من يوتيوب</b>.\n\n"
        "أرسل أي رابط <b>YouTube</b> (فيديو عادي أو بث محفوظ) وسنبدأ معك خطوات القص."
    )


# ========= تشغيل تلقائي عند إرسال أي رابط يوتيوب =========
@bot.message_handler(func=lambda m: m.text and ("youtu.be" in m.text or "youtube.com" in m.text))
def auto_handle_youtube_link(message):
    """
    لو المستخدم أرسل رابط يوتيوب مباشرة بدون /start
    نبدأ دورة جديدة تلقائياً.
    """
    chat_id = message.chat.id
    user_sessions.pop(chat_id, None)
    handle_url(message)


# ========= خطوة 1: استلام الرابط =========
def handle_url(message):
    chat_id = message.chat.id
    url = message.text.strip()

    user_sessions[chat_id] = {"url": url}

    bot.reply_to(
        message,
        "⏱️ أرسل وقت البداية بصيغة مثل:\n"
        "<code>00:01:20</code> أو <code>1:20</code> أو <code>80</code> ثانية."
    )
    bot.register_next_step_handler(message, handle_start_time)


# ========= خطوة 2: استلام وقت البداية =========
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


# ========= خطوة 3: استلام وقت النهاية =========
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

    # جلب الجودات
    try:
        qualities = get_available_qualities(session["url"])
    except Exception as e:
        print("Error getting qualities:", e)
        qualities = {}

    if not qualities:
        bot.send_message(
            chat_id,
            "⚠️ لم نتمكن من فحص الجودات بدقة.\n"
            "سيتم استخدام أفضل جودة متاحة تلقائياً."
        )
        session["format_id"] = "best"
        start_cutting(chat_id)
        return

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
        "سيتم الآن قصّ المقطع وتقسيمه وإرساله…",
        chat_id=chat_id,
        message_id=call.message.message_id
    )

    start_cutting(chat_id)


# ========= خطوة 4: القص، التقسيم، والإرسال =========
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
        "🛠️ جاري تحميل الفيديو وقص المقطع… الرجاء الانتظار.\n"
        "قد يستغرق ذلك بعض الوقت حسب طول المقطع والجودة."
    )

    input_file = None
    cut_file = "cut.mp4"
    parts_files = []

    try:
        # لو format_id == "best" نحمّل أفضل جودة مباشرة
        if format_id == "best":
            ydl_opts = make_ydl_opts({
                "format": "bestvideo+bestaudio/best",
                "outtmpl": "source.%(ext)s",
                "merge_output_format": "mp4",
            })
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                input_file = ydl.prepare_filename(info)
        else:
            input_file = download_video(url, format_id)

        # قص المقطع المطلوب من الفيديو
        cut_file = "cut.mp4"
        cut_video(input_file, start_seconds, duration, cut_file)

        # تقسيم المقطع إلى أجزاء بحجم مناسب
        parts_files = split_video_by_size(cut_file, duration, MAX_PART_BYTES)

        # إرسال كل جزء كفيديو
        total_parts = len(parts_files)

        if total_parts > 1:
            bot.send_message(
                chat_id,
                f"📦 حجم المقطع بعد القص كبير، سيتم تقسيمه تلقائياً إلى {total_parts} جزء(أجزاء) "
                f"لا يتجاوز كل منها تقريباً {MAX_PART_MB}MB…"
            )

        for idx, part_path in enumerate(parts_files, start=1):
            if not os.path.exists(part_path):
                continue

            part_size_mb = os.path.getsize(part_path) / (1024 * 1024)
            caption = f"جزء {idx}/{total_parts} 🎬 (≈{part_size_mb:.1f}MB)"

            bot.send_message(chat_id, f"📤 جاري إرسال الجزء {idx}/{total_parts}…")

            with open(part_path, "rb") as f:
                try:
                    bot.send_video(chat_id, f, caption=caption)
                except ApiTelegramException as e:
                    # في حال كان جزء أكبر من المسموح عن طريق الخطأ
                    if "413" in str(e) or "Request Entity Too Large" in str(e):
                        bot.send_message(
                            chat_id,
                            f"❌ حجم الجزء {idx}/{total_parts} أكبر من الحد المسموح في تلجرام.\n"
                            "حاول قص مدة أقصر أو اختيار جودة أقل."
                        )
                    else:
                        bot.send_message(
                            chat_id,
                            f"❌ خطأ من تلجرام أثناء إرسال الجزء {idx}/{total_parts}:\n<code>{e}</code>"
                        )

        bot.send_message(
            chat_id,
            "✅ انتهى إرسال جميع الأجزاء.\n"
            "أرسل رابط يوتيوب جديد لقص مقطع آخر (لا تحتاج إلى /start)."
        )

    except ApiTelegramException as e:
        bot.send_message(chat_id, f"❌ خطأ من تلجرام أثناء الإرسال:\n<code>{e}</code>")
    except yt_dlp.utils.DownloadError as e:
        # أخطاء تحميل يوتيوب (كوكيز – تأكيد أنك لست روبوت – إلخ)
        bot.send_message(
            chat_id,
            "❌ حدث خطأ أثناء تحميل الفيديو من يوتيوب.\n"
            "تأكد أن رابط الفيديو يعمل، وأن ملف <code>cookies.txt</code> محدث، ثم حاول مرة أخرى."
        )
        print("DownloadError:", e)
    except Exception as e:
        bot.send_message(
            chat_id,
            "❌ حدث خطأ أثناء التحميل أو القص.\n"
            "حاول مرة أخرى أو برابط مختلف."
        )
        print("Unknown error in start_cutting:", e)
    finally:
        # تنظيف الملفات المؤقتة
        try:
            if input_file and os.path.exists(input_file):
                os.remove(input_file)
            if cut_file and os.path.exists(cut_file) and cut_file != input_file:
                os.remove(cut_file)
            for p in parts_files:
                if os.path.exists(p):
                    os.remove(p)
        except Exception:
            pass


# ========= تشغيل البوت =========
if __name__ == "__main__":
    print("🔥 Bot is running…")
    # skip_pending=True حتى لا يأخذ رسائل قديمة عند كل إعادة تشغيل
    bot.infinity_polling(skip_pending=True)
