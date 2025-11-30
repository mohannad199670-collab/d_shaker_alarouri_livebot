import os
import re
import math
import time
import glob
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

# ================= إعداد الكوكيز =================
COOKIES_FILE = "cookies.txt"

# إذا المستخدم وضع الكوكيز في متغير البيئة YT_COOKIES نكتبه في ملف
ENV_COOKIES = os.getenv("YT_COOKIES")
if ENV_COOKIES:
    try:
        with open(COOKIES_FILE, "w", encoding="utf-8") as cf:
            cf.write(ENV_COOKIES)
    except Exception as e:
        print("⚠️ لم أستطع كتابة cookies من Environment:", e)

USE_COOKIES = os.path.exists(COOKIES_FILE)

# ================= إعداد الحجم =================
# حجم الهدف لكل جزء (بالميغابايت) – يمكن تغييره من Environment
TARGET_SEGMENT_MB = int(os.getenv("TARGET_SEGMENT_MB", "49"))
TARGET_SEGMENT_BYTES = TARGET_SEGMENT_MB * 1024 * 1024

# حد تلجرام الحقيقي (تقريباً 2 غيغا؛ نخليه 1900 ميغا هامش أمان)
TELEGRAM_HARD_LIMIT_BYTES = 1900 * 1024 * 1024

# ================= جلسات المستخدم =================
# state: idle / waiting_url / waiting_start / waiting_end / waiting_quality / processing
user_sessions = {}  # {chat_id: {...}}


# ========= دوال مساعدة عامة =========

def is_youtube_url(text: str) -> bool:
    if not text:
        return False
    text = text.strip()
    return ("youtube.com/" in text) or ("youtu.be/" in text) or ("youtube.com/live/" in text)


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


def build_yt_dlp_opts(base_opts=None, skip_download=False):
    """
    يبني خيارات yt-dlp مع الكوكيز إن وجدت
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    if skip_download:
        opts["skip_download"] = True

    if USE_COOKIES:
        opts["cookiefile"] = COOKIES_FILE

    if base_opts:
        opts.update(base_opts)
    return opts


# ========= جلب الجودات =========

def get_available_qualities(video_url: str):
    """
    يرجع dict مثل: {144: "17", 360: "18", 480: "135+140", ...}
    هنا نركز على الفورمات التي فيها صوت وصورة (progressive) قدر الإمكان.
    """
    ydl_opts = build_yt_dlp_opts(skip_download=True)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        formats = info.get("formats", [])

    # هدفنا جودات قياسية:
    target_heights = [144, 240, 360, 480, 720, 1080]
    best_for_height = {}

    for f in formats:
        height = f.get("height")
        fmt_id = f.get("format_id")
        acodec = f.get("acodec")
        vcodec = f.get("vcodec")
        if not height or not fmt_id:
            continue

        # نفضّل progressive (فيه صوت وصورة معاً)
        if acodec != "none" and vcodec != "none":
            if height in target_heights:
                # نخزن أعلى bitrate تقريباً
                current = best_for_height.get(height)
                if not current:
                    best_for_height[height] = f
                else:
                    # نختار الأعلى حجم أو bitrate
                    if (f.get("tbr") or 0) > (current.get("tbr") or 0):
                        best_for_height[height] = f

    # نحول لدكت: {height: format_id}
    result = {}
    for h in target_heights:
        if h in best_for_height:
            result[h] = best_for_height[h]["format_id"]

    return result


# ========= تحميل الفيديو =========

def download_video(video_url: str, format_id: str, output_name: str = "source.mp4") -> str:
    """
    يقوم بتحميل الفيديو من يوتيوب بالجودة المحددة.
    لو فشل الفورمات المحدد، يحاول fallback إلى 'best'.
    يرجع اسم الملف الناتج.
    """
    # اسم مؤقت – نخلي yt-dlp يحدده
    base_opts = {
        "format": format_id,
        "outtmpl": "source.%(ext)s",
    }
    ydl_opts = build_yt_dlp_opts(base_opts, skip_download=False)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            filename = ydl.prepare_filename(info)
            return filename
    except Exception as e:
        print("⚠️ فشل تحميل الفورمات المحدد، سيتم المحاولة مع 'best':", e)

    # Fallback to best
    base_opts = {
        "format": "best",
        "outtmpl": "source.%(ext)s",
    }
    ydl_opts = build_yt_dlp_opts(base_opts, skip_download=False)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        filename = ydl.prepare_filename(info)
        return filename


# ========= قص الفيديو =========

def cut_video(input_file: str, start_seconds: int, duration_seconds: int, output_file: str = "cut.mp4") -> str:
    """
    يقص جزء من الفيديو مع إعادة ترميز (لضمان الصوت وعدم المشاكل)
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

    print("⚙️ Running ffmpeg cut:", " ".join(command))
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output_file


# ========= تقسيم الفيديو إذا لزم =========

def split_video_if_needed(cut_file: str, duration_seconds: int):
    """
    لو حجم المقطع أقل من الحد المستهدف – يرجع [cut_file]
    لو أكبر – يقسمه لأجزاء بعدد مناسب بحيث كل جزء تقريباً <= TARGET_SEGMENT_BYTES
    ويرجع قائمة أسماء الملفات بالترتيب.
    """
    size_bytes = os.path.getsize(cut_file)
    print(f"📏 حجم المقطع بعد القص: {size_bytes / (1024*1024):.2f} MB")

    # لو أصلاً أقل من الحد المستهدف أو أقل من حد تلغرام – لا نقسم
    if size_bytes <= TARGET_SEGMENT_BYTES or size_bytes <= TELEGRAM_HARD_LIMIT_BYTES:
        return [cut_file]

    # نحسب bitrate تقريبي (byte per second)
    if duration_seconds <= 0:
        # احتياط – لا نقسم لو ما عندنا مدة صحيحة
        return [cut_file]

    bytes_per_second = size_bytes / duration_seconds

    # عدد الثواني الأقصى لكل جزء ليكون تقريباً <= TARGET_SEGMENT_BYTES
    max_seg_duration = int(TARGET_SEGMENT_BYTES / bytes_per_second) - 1
    if max_seg_duration < 10:
        max_seg_duration = 10  # حد أدنى منطقي

    if max_seg_duration >= duration_seconds:
        # لا حاجة للتقسيم عملياً
        return [cut_file]

    print(f"🔪 سيتم التقسيم إلى أجزاء زمن كل جزء تقريباً: {max_seg_duration} ثانية")

    # نحذف أي ملفات segment قديمة
    for f in glob.glob("segment_*.mp4"):
        try:
            os.remove(f)
        except Exception:
            pass

    seg_pattern = "segment_%03d.mp4"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        cut_file,
        "-c",
        "copy",
        "-map",
        "0",
        "-f",
        "segment",
        "-segment_time",
        str(max_seg_duration),
        "-reset_timestamps",
        "1",
        seg_pattern,
    ]

    print("⚙️ Running ffmpeg segment:", " ".join(command))
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # جمع الأجزاء
    segments = sorted(glob.glob("segment_*.mp4"))
    print(f"📦 تم إنشاء {len(segments)} جزءاً بعد التقسيم.")
    if not segments:
        # لو فشل التقسيم لأي سبب – نرجع الملف الأصلي
        return [cut_file]

    return segments


# ========= إدارة الجلسة =========

def reset_session(chat_id):
    user_sessions[chat_id] = {
        "state": "idle",
        "url": None,
        "start": None,
        "end": None,
        "duration": None,
        "formats": {},
        "format_id": None,
    }


def set_state(chat_id, state):
    if chat_id not in user_sessions:
        reset_session(chat_id)
    user_sessions[chat_id]["state"] = state


# ========= أوامر البوت =========

@bot.message_handler(commands=["start"])
def cmd_start(message):
    chat_id = message.chat.id
    reset_session(chat_id)
    set_state(chat_id, "waiting_url")

    bot.reply_to(
        message,
        "👋 أهلاً بك في بوت <b>قص فيديوهات يوتيوب</b>.\n\n"
        "أرسل الآن رابط فيديو يوتيوب (عادي أو بث محفوظ)، أو أي وقت لاحقاً فقط أرسل الرابط مباشرة."
    )


# ========= استقبال أي رسالة تحتوي رابط يوتيوب =========

@bot.message_handler(func=lambda m: is_youtube_url(m.text or ""))
def handle_new_url(message):
    chat_id = message.chat.id
    url = (message.text or "").strip()

    reset_session(chat_id)
    session = user_sessions[chat_id]
    session["url"] = url
    set_state(chat_id, "waiting_start")

    bot.reply_to(
        message,
        "🔗 تم استلام رابط يوتيوب.\n"
        "⏱️ أرسل الآن <b>وقت البداية</b> بصيغة مثل:\n"
        "<code>80</code> أو <code>1:20</code> أو <code>00:01:20</code>"
    )


# ========= استقبال باقي الرسائل النصية بحسب الحالة =========

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text_states(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()

    if chat_id not in user_sessions:
        reset_session(chat_id)

    session = user_sessions[chat_id]
    state = session.get("state", "idle")

    # لو الحالة idle وليس نص مفيد – نطلب منه رابط أو /start
    if state == "idle":
        bot.reply_to(
            message,
            "👀 أرسل رابط يوتيوب لبدء القص، أو اكتب /start."
        )
        return

    # حالة انتظار وقت البداية
    if state == "waiting_start":
        try:
            start_seconds = parse_time_to_seconds(text)
        except ValueError:
            bot.reply_to(
                message,
                "⚠️ صيغة وقت غير صحيحة.\n"
                "أرسل وقت البداية مثلاً: <code>80</code> أو <code>1:20</code> أو <code>00:01:20</code>"
            )
            return

        session["start"] = start_seconds
        set_state(chat_id, "waiting_end")

        bot.reply_to(
            message,
            "⏱️ الآن أرسل <b>وقت النهاية</b>.\n"
            "مثال: <code>00:05:00</code> (أي بعد 5 دقائق من بداية الفيديو الأصلية)."
        )
        return

    # حالة انتظار وقت النهاية
    if state == "waiting_end":
        try:
            end_seconds = parse_time_to_seconds(text)
        except ValueError:
            bot.reply_to(
                message,
                "⚠️ صيغة وقت غير صحيحة.\n"
                "أرسل وقت النهاية مثلاً: <code>300</code> أو <code>5:00</code> أو <code>00:05:00</code>"
            )
            return

        start_seconds = session.get("start")
        if start_seconds is None:
            bot.reply_to(message, "⚠️ حصل خطأ في الجلسة. أرسل رابط يوتيوب من جديد.")
            reset_session(chat_id)
            return

        if end_seconds <= start_seconds:
            bot.reply_to(
                message,
                "⚠️ يجب أن يكون وقت النهاية أكبر من وقت البداية.\n"
                "أعد إرسال وقت النهاية بشكل صحيح."
            )
            return

        duration = end_seconds - start_seconds
        session["end"] = end_seconds
        session["duration"] = duration

        bot.reply_to(message, "⏳ يتم الآن فحص الجودات المتاحة للفيديو…")

        # استدعاء الجودات
        try:
            qualities = get_available_qualities(session["url"])
        except Exception as e:
            print("❌ Error getting qualities:", e)
            bot.reply_to(
                message,
                "❌ حدث خطأ أثناء فحص الجودات من يوتيوب.\n"
                "قد يكون هناك مشكلة في الاتصال أو في الكوكيز."
            )
            reset_session(chat_id)
            return

        if not qualities:
            bot.reply_to(
                message,
                "⚠️ لم أجد جودات قياسية (144p–1080p) بصيغة progressive.\n"
                "سيتم استخدام أفضل جودة متاحة تلقائياً."
            )
            session["format_id"] = "best"
            set_state(chat_id, "processing")
            start_cutting(chat_id)
            return

        session["formats"] = qualities
        set_state(chat_id, "waiting_quality")

        # إنشاء أزرار الجودات
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
        return

    # لو حالة أخرى لم نعرّفها
    bot.reply_to(
        message,
        "⚠️ حصل ارتباك في الجلسة.\n"
        "أرسل رابط يوتيوب من جديد أو اكتب /start."
    )
    reset_session(chat_id)


# ========= التعامل مع ضغط زر الجودة =========

@bot.callback_query_handler(func=lambda call: call.data.startswith("q_"))
def handle_quality_callback(call):
    chat_id = call.message.chat.id
    if chat_id not in user_sessions:
        bot.answer_callback_query(call.id, "انتهت الجلسة. أرسل رابط جديد.", show_alert=True)
        return

    session = user_sessions[chat_id]
    state = session.get("state")
    if state != "waiting_quality":
        bot.answer_callback_query(call.id, "هذه الجلسة لم تعد صالحة. أرسل رابط جديد.", show_alert=True)
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
    set_state(chat_id, "processing")

    bot.answer_callback_query(call.id, f"تم اختيار الجودة: {height}p ✅", show_alert=False)
    bot.edit_message_text(
        f"✅ تم اختيار الجودة: <b>{height}p</b>\n"
        "سيتم الآن تحميل الفيديو وقصّه وإرساله…",
        chat_id=chat_id,
        message_id=call.message.message_id
    )

    start_cutting(chat_id)


# ========= تنفيذ القص والإرسال =========

def start_cutting(chat_id):
    if chat_id not in user_sessions:
        bot.send_message(chat_id, "⚠️ الجلسة غير موجودة. أرسل رابط من جديد.")
        return

    session = user_sessions[chat_id]
    url = session.get("url")
    start_seconds = session.get("start")
    duration = session.get("duration")
    format_id = session.get("format_id", "best")

    if not url or start_seconds is None or duration is None:
        bot.send_message(chat_id, "⚠️ بيانات الجلسة غير كاملة. أرسل رابط من جديد.")
        reset_session(chat_id)
        return

    bot.send_message(
        chat_id,
        "⬇️ جاري تحميل الفيديو من يوتيوب بالجودة المختارة…\n"
        "ثم سيتم القص والتقسيم والإرسال كفيديو."
    )

    input_file = None
    cut_file = "cut.mp4"

    try:
        # تحميل الفيديو
        input_file = download_video(url, format_id)
        print("✅ تم تحميل الفيديو:", input_file)

        # قص الفيديو
        cut_video(input_file, start_seconds, duration, cut_file)
        print("✅ تم قص الفيديو:", cut_file)

        # تقسيم لو لزم
        segments = split_video_if_needed(cut_file, duration_seconds=duration)

        # إرسال
        total_parts = len(segments)
        for idx, seg_path in enumerate(segments, start=1):
            size_mb = os.path.getsize(seg_path) / (1024 * 1024)
            caption = f"🎬 المقطع {idx}/{total_parts} — {size_mb:.1f} MB"
            if total_parts == 1:
                caption = "🎬 المقطع الجاهز بعد القص"

            bot.send_message(chat_id, f"📤 جاري إرسال الجزء {idx}/{total_parts} كفيديو…")
            with open(seg_path, "rb") as vid:
                bot.send_video(chat_id, vid, caption=caption)

        bot.send_message(
            chat_id,
            "✅ انتهى الإرسال.\n"
            "يمكنك الآن إرسال رابط يوتيوب جديد مباشرة لبدء قص مقطع آخر."
        )
        reset_session(chat_id)

    except ApiTelegramException as e:
        print("❌ ApiTelegramException:", e)
        if "413" in str(e) or "Request Entity Too Large" in str(e):
            bot.send_message(
                chat_id,
                "❌ الملف الذي نحاول إرساله أكبر من الحد المسموح في تلغرام (حوالي 2 غيغا).\n"
                "حاول قص مدة أقصر."
            )
        else:
            bot.send_message(
                chat_id,
                f"❌ خطأ من تلغرام أثناء الإرسال:\n<code>{e}</code>"
            )
        reset_session(chat_id)

    except yt_dlp.utils.DownloadError as e:
        print("❌ yt-dlp DownloadError:", e)
        bot.send_message(
            chat_id,
            "❌ حدث خطأ أثناء تحميل الفيديو من يوتيوب.\n"
            "تأكد أن الرابط يعمل، وأن ملف cookies.txt / متغير YT_COOKIES صحيحان، ثم حاول مرة أخرى."
        )
        reset_session(chat_id)

    except Exception as e:
        print("❌ Error in start_cutting:", e)
        bot.send_message(
            chat_id,
            "❌ حدث خطأ غير متوقع أثناء القص أو التحميل.\n"
            "حاول مرة أخرى أو أرسل رابطاً آخر."
        )
        reset_session(chat_id)

    finally:
        # تنظيف الملفات المؤقتة
        try:
            if input_file and os.path.exists(input_file):
                os.remove(input_file)
            if os.path.exists(cut_file):
                os.remove(cut_file)
            for f in glob.glob("segment_*.mp4"):
                try:
                    os.remove(f)
                except Exception:
                    pass
        except Exception:
            pass


# ========= تشغيل البوت مع إعادة المحاولة تلقائياً =========

if __name__ == "__main__":
    print("🔥 Bot is running…")
    while True:
        try:
            bot.infinity_polling(
                skip_pending=True,
                timeout=60,
                long_polling_timeout=60,
            )
        except Exception as e:
            print("⚠️ Polling error, will retry in 5s:", e)
            time.sleep(5)
