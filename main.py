import os
import math
import time
import logging
import subprocess

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.apihelper import ApiTelegramException

from yt_dlp import YoutubeDL

# ================= إعداد اللوج =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ================= إعداد التوكن =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN غير موجود في Environment variables")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ================= إعداد الكوكيز =================
# نقرأ الكوكيز من متغير البيئة:
# YT_COOKIES_HEADER أو YT_COOKIES (احتياطاً لو استخدمت الاسم القديم)
YT_COOKIES_HEADER = os.getenv("YT_COOKIES_HEADER", os.getenv("YT_COOKIES", "")).strip()

# إلغاء استخدام ملف cookies.txt نهائياً
COOKIES_PATH = None

# ================= إعدادات الحجم =================
# الحد الأقصى لكل جزء (حتى لا نضرب حد تليجرام 50MB)
MAX_MB_PER_PART = 48

# ================= جلسات المستخدمين =================
# نخزن حالة كل مستخدم:
# state: awaiting_url / awaiting_start / awaiting_end / awaiting_quality / processing
user_sessions = {}  # {chat_id: {...}}


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


def reset_session(chat_id: int):
    user_sessions[chat_id] = {
        "state": "awaiting_url"
    }


# ========= دالة: جلب الجودات المتاحة =========
def get_available_qualities(video_url: str):
    """
    يرجع dict مثل: {144: "91", 360: "18", 480: "94", ...}
    حسب الجودات الموجودة فعلاً في الفيديو
    """
    # إعدادات yt-dlp لفحص الجودات فقط دون تحميل
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "geo_bypass": True,

        # **التعديل هنا: استخدام الكوكيز كـ HTTP Header**
        "http_headers": {
            "Cookie": YT_COOKIES_HEADER
        } if YT_COOKIES_HEADER else None,

        # استخدام عميل أندرويد لتقليل مشاكل YouTube
        "extractor_args": {
            "youtube": {
                "player_client": ["android"]
            }
        }
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        formats = info.get("formats", [])

    target_heights = [144, 240, 360, 480, 720, 1080]
    result = {}

    for f in formats:
        height = f.get("height")
        fmt_id = f.get("format_id")
        vcodec = f.get("vcodec")
        if not height or not fmt_id:
            continue
        # نتأكد أنه ليس صوت فقط
        if vcodec == "none":
            continue
        if height in target_heights:
            # آخر واحد غالباً أحدث/أفضل لنفس الارتفاع
            result[height] = fmt_id

    return result


# ========= دالة: تحميل الفيديو بالجودة المطلوبة =========
def download_video(video_url: str, format_id: str | None, output_name: str = "source") -> str:
    """
    يقوم بتحميل الفيديو من يوتيوب بالجودة المحددة
    ويعيد اسم الملف الناتج
    """
    if format_id:
        fmt = format_id
    else:
        # في حال لم نختر جودة معينة نستخدم أفضل جودة متاحة
        fmt = "bestvideo*+bestaudio/best"

    ydl_opts = {
        "format": fmt,
        "outtmpl": f"{output_name}.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "geo_bypass": True,

        # **التعديل هنا: استخدام الكوكيز كـ HTTP Header**
        "http_headers": {
            "Cookie": YT_COOKIES_HEADER
        } if YT_COOKIES_HEADER else None,

        "extractor_args": {
            "youtube": {
                "player_client": ["android"]
            }
        }
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        filename = ydl.prepare_filename(info)

    return filename


# ========= دالة: قص الفيديو باستخدام ffmpeg =========
def cut_video(input_file: str, start_seconds: int, duration_seconds: int, output_file: str = "cut_full.mp4"):
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


# ========= دالة: تقسيم الفيديو حسب الحجم =========
def split_video_by_size(input_file: str, duration_seconds: int, max_mb: int = MAX_MB_PER_PART):
    """
    تقسيم الفيديو إلى عدة ملفات بحيث يكون حجم كل ملف تقريباً <= max_mb
    نقسم حسب الزمن بالتساوي تقريباً
    """
    size_bytes = os.path.getsize(input_file)
    size_mb = size_bytes / (1024 * 1024)

    if size_mb <= max_mb:
        # لا حاجة للتقسيم
        return [input_file]

    parts = math.ceil(size_mb / max_mb)
    # نتأكد أن مدة كل جزء على الأقل 1 ثانية
    base_chunk = max(1, duration_seconds // parts)

    chunk_files = []
    for i in range(parts):
        start = i * base_chunk
        if i == parts - 1:
            # آخر جزء يأخذ كل المتبقي
            this_duration = duration_seconds - start
        else:
            this_duration = base_chunk

        if this_duration <= 0:
            continue

        out_name = f"part_{i + 1}.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-i",
            input_file,
            "-t",
            str(this_duration),
            "-c",
            "copy",
            out_name,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        chunk_files.append(out_name)

    return chunk_files


# ========= تنفيذ القص والتقسيم والإرسال =========
def process_video(chat_id: int):
    session = user_sessions.get(chat_id)
    if not session:
        bot.send_message(chat_id, "⚠️ حصل خطأ في الجلسة. أرسل /start من جديد.")
        return

    url = session.get("url")
    start_seconds = session.get("start")
    duration = session.get("duration")
    format_id = session.get("format_id")

    if not url or start_seconds is None or duration is None:
        bot.send_message(chat_id, "⚠️ بيانات غير مكتملة. أرسل /start من جديد.")
        reset_session(chat_id)
        return

    bot.send_message(
        chat_id,
        "🔧 جاري قصّ المقطع وتحضيره…\n"
        "قد يستغرق ذلك بعض الوقت حسب طول المقطع والجودة."
    )

    input_file = None
    cut_file = "cut_full.mp4"
    chunk_files = []

    try:
        # تحميل الفيديو
        input_file = download_video(url, format_id, output_name="source")
        # قص الجزء المطلوب
        cut_video(input_file, start_seconds, duration, cut_file)
        # تقسيم حسب الحجم
        chunk_files = split_video_by_size(cut_file, duration, MAX_MB_PER_PART)

        total_parts = len(chunk_files)

        for idx, path in enumerate(chunk_files, start=1):
            bot.send_message(chat_id, f"📤 جاري إرسال الجزء {idx} من {total_parts}…")
            with open(path, "rb") as f:
                bot.send_video(
                    chat_id,
                    f,
                    supports_streaming=True,
                    caption=f"🎬 الجزء {idx}/{total_parts}"
                )

        bot.send_message(chat_id, "✅ انتهى! يمكنك الآن إرسال رابط يوتيوب جديد مباشرة.")
        reset_session(chat_id)

    except ApiTelegramException as e:
        logging.exception("Telegram API error while sending video")
        if "413" in str(e) or "Request Entity Too Large" in str(e):
            bot.send_message(
                chat_id,
                "❌ ما زال أحد المقاطع أكبر من الحد المسموح في تلجرام حتى بعد التقسيم.\n"
                "حاول اختيار جودة أقل أو تقليل مدة القص."
            )
        else:
            bot.send_message(chat_id, f"❌ خطأ من تلجرام أثناء الإرسال:\n<code>{e}</code>")
    except Exception as e:
        logging.exception("Error in process_video")
        bot.send_message(chat_id, "❌ حدث خطأ غير متوقع أثناء تحميل أو قص الفيديو.")
    finally:
        # تنظيف الملفات المؤقتة
        try:
            if input_file and os.path.exists(input_file):
                os.remove(input_file)
            if os.path.exists(cut_file):
                os.remove(cut_file)
            for p in chunk_files:
                if p and os.path.exists(p):
                    os.remove(p)
        except Exception:
            pass


# ========= أوامر البوت =========
@bot.message_handler(commands=["start"])
def cmd_start(message):
    chat_id = message.chat.id
    reset_session(chat_id)
    bot.reply_to(
        message,
        "👋 أهلاً بك في بوت <b>قص فيديوهات يوتيوب</b>\n\n"
        "📎 أرسل الآن رابط فيديو يوتيوب (عادي أو بث محفوظ)،\n"
        "وسأطلب منك وقت البداية والنهاية ثم الجودة."
    )


# ========= التعامل مع أي رسالة نصية =========
@bot.message_handler(content_types=["text"])
def handle_text(message):
    chat_id = message.chat.id
    text = message.text.strip()

    # تجاهل الأوامر الأخرى (مثل /start تمت معالجتها)
    if text.startswith("/"):
        return

    # إن لم يوجد جلسة، نجهز واحدة
    if chat_id not in user_sessions:
        reset_session(chat_id)

    session = user_sessions[chat_id]
    state = session.get("state", "awaiting_url")

    # إذا أرسل رابط يوتيوب جديد في أي وقت نبدأ من الصفر
    if ("youtube.com" in text) or ("youtu.be" in text):
        session["url"] = text
        session["state"] = "awaiting_start"
        bot.reply_to(
            message,
            "⏱️ أرسل وقت البداية بصيغة مثل:\n"
            "<code>00:01:20</code> أو <code>1:20</code> أو <code>80</code> ثانية."
        )
        return

    # لو ليس رابط يوتيوب، نحدد حسب الحالة الحالية
    if state == "awaiting_start":
        try:
            start_seconds = parse_time_to_seconds(text)
        except ValueError:
            bot.reply_to(message, "⚠️ صيغة وقت البداية غير صحيحة. أعد الإرسال.")
            return

        session["start"] = start_seconds
        session["state"] = "awaiting_end"
        bot.reply_to(
            message,
            "⏱️ الآن أرسل وقت النهاية.\n"
            "مثال: <code>00:05:00</code> يعني بعد 5 دقائق من بداية الفيديو."
        )

    elif state == "awaiting_end":
        try:
            end_seconds = parse_time_to_seconds(text)
        except ValueError:
            bot.reply_to(message, "⚠️ صيغة وقت النهاية غير صحيحة. أعد الإرسال.")
            return

        start_seconds = session.get("start")
        if start_seconds is None or end_seconds <= start_seconds:
            bot.reply_to(message, "⚠️ يجب أن يكون وقت النهاية أكبر من وقت البداية. أعد الإرسال.")
            return

        duration = end_seconds - start_seconds
        session["end"] = end_seconds
        session["duration"] = duration

        bot.reply_to(message, "⏳ يتم فحص الجودات المتاحة للفيديو… الرجاء الانتظار قليلاً.")
        video_url = session.get("url")

        try:
            qualities = get_available_qualities(video_url)
        except Exception as e:
            logging.exception("Error getting qualities from YouTube")
            bot.reply_to(
                message,
                "❌ حدث خطأ أثناء فحص الجودات من يوتيوب.\n"
                "قد يكون هناك مشكلة في الاتصال أو في الكوكيز."
            )
            return

        if not qualities:
            bot.send_message(
                chat_id,
                "⚠️ لم يتم العثور على جودات قياسية (144p–1080p).\n"
                "سيتم استخدام أفضل جودة متاحة تلقائياً."
            )
            session["format_id"] = None
            session["state"] = "processing"
            process_video(chat_id)
            return

        # حفظ الجودات في الجلسة
        session["formats"] = qualities
        session["state"] = "awaiting_quality"

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

    else:
        # أي نص آخر في حالة مختلفة
        bot.reply_to(
            message,
            "📎 أرسل رابط يوتيوب لبدء قص مقطع جديد،\n"
            "أو استخدم الأمر /start لإعادة التشغيل."
        )


# ========= التعامل مع ضغط زر الجودة =========
@bot.callback_query_handler(func=lambda call: call.data.startswith("q_"))
def handle_quality_callback(call):
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id)

    if not session:
        bot.answer_callback_query(call.id, "انتهت الجلسة. أرسل /start من جديد.", show_alert=True)
        return

    if session.get("state") != "awaiting_quality":
        bot.answer_callback_query(call.id, "لا يوجد اختيار جودة مطلوب حالياً.", show_alert=True)
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
    session["state"] = "processing"

    bot.answer_callback_query(call.id, f"تم اختيار الجودة: {height}p ✅", show_alert=False)
    bot.edit_message_text(
        f"✅ تم اختيار الجودة: <b>{height}p</b>\n"
        "سيتم الآن قصّ المقطع وتقسيمه وإرساله…",
        chat_id=chat_id,
        message_id=call.message.message_id
    )

    process_video(chat_id)


# ========= تشغيل البوت مع إعادة المحاولة =========
if __name__ == "__main__":
    print("🔥 Bot is running…")
    while True:
        try:
            bot.infinity_polling(skip_pending=True)
        except ApiTelegramException as e:
            logging.error(f"Polling error from Telegram: {e}")
            # غالباً يعني أن نفس التوكن يعمل في مكان آخر أيضاً
            if getattr(e, "error_code", None) == 409:
                print("⚠️ يوجد تعارض 409: تأكد أن البوت لا يعمل على سيرفر آخر بنفس التوكن.")
            time.sleep(5)
        except Exception as e:
            logging.exception("Unknown polling error, retry after 5s")
            time.sleep(5)
