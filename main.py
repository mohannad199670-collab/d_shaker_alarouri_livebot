import os
import math
import time
import logging
import subprocess

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.apihelper import ApiTelegramException

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# ================= إعداد اللوج =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ================= إعداد التوكن =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN غير موجود في Environment variables")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ================= إعداد الكوكيز =================
# الآن الاعتماد على ملف cookies.txt الموجود في نفس مجلد main.py
COOKIES_PATH = "cookies.txt"
if not os.path.exists(COOKIES_PATH):
    logger.warning("⚠️ ملف cookies.txt غير موجود، قد تفشل بعض الفيديوهات المحمية أو الطويلة.")
    COOKIES_PATH = None  # حتى لا نعطي yt-dlp مسار غير موجود

# ================= إعدادات الحجم =================
MAX_TELEGRAM_MB = 48  # الحد المستهدف لكل جزء (تقريباً 48 ميغا)


# ================= إدارة جلسات المستخدم =================
# لكل مستخدم نخزن الحالة هنا
# مثال:
# {
#   chat_id: {
#       "step": "await_url" / "await_start" / "await_end" / "choose_quality" / "processing",
#       "url": "...",
#       "start": 10,
#       "end": 120,
#       "duration": 110,
#       "quality_height": 360,
#       "available_heights": [144, 360, 720]
#   }
# }
user_sessions = {}


def reset_session(chat_id: int):
    """إعادة تهيئة جلسة المستخدم."""
    user_sessions[chat_id] = {
        "step": "await_url"
    }


# ================= دوال مساعدة =================
def extract_url(text: str) -> str:
    """
    يلتقط أول شيء يشبه الرابط من الرسالة (في حال أرسل نص + رابط).
    """
    parts = text.split()
    for p in parts:
        if "http" in p or "youtu" in p:
            return p
    return text.strip()


def parse_time_to_seconds(time_str: str) -> int:
    """
    يقبل: SS أو MM:SS أو HH:MM:SS
    ويرجع عدد الثواني كـ int
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


def get_available_qualities(video_url: str):
    """
    إرجاع قائمة الجودات المتاحة مع صوت (فيديو+أوديو) مثل:
    [144, 240, 360, 480, 720, 1080]
    إذا حصل خطأ نرمي استثناء ونتعامل معه خارج الدالة.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "geo_bypass": True,
    }

    # استخدام ملف الكوكيز إذا متوفر
    if COOKIES_PATH:
        ydl_opts["cookies"] = COOKIES_PATH

    target_heights = {144, 240, 360, 480, 720, 1080}
    available = set()

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        formats = info.get("formats", [])

    for f in formats:
        height = f.get("height")
        if not height:
            continue

        if height not in target_heights:
            continue

        # نتأكد أن فيه صوت
        acodec = f.get("acodec")
        audio_ext = f.get("audio_ext")
        has_audio = (acodec and acodec != "none") or (audio_ext and audio_ext != "none")

        if has_audio:
            available.add(height)

    return sorted(list(available))


def build_format_string_for_height(height: int | None) -> str:
    """
    صيغة الفورمات لـ yt-dlp بحيث يختار فيديو+صوت حسب الارتفاع المطلوب،
    مع fallback في حال عدم توفر نفس الارتفاع بالضبط.
    """
    if height is None:
        # أفضل شيء متاح
        return "bv*+ba/best"

    # نحاول mp4 + m4a أولاً ثم أي شيء أقل من أو يساوي هذه الجودة
    return (
        f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={height}]+bestaudio/"
        f"best[height<={height}]/best"
    )


def download_video(video_url: str, quality_height: int | None, output_name: str = "source") -> str:
    """
    تحميل الفيديو من يوتيوب بالجودة المطلوبة (مع صوت) وإرجاع اسم الملف.
    دائماً يخرج بصيغة mp4 (بفضل merge_output_format).
    """
    fmt = build_format_string_for_height(quality_height)

    ydl_opts = {
        "format": fmt,
        "outtmpl": f"{output_name}.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "geo_bypass": True,
        "merge_output_format": "mp4",
    }

    # استخدام cookies.txt لو متوفر
    if COOKIES_PATH:
        ydl_opts["cookies"] = COOKIES_PATH

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        filename = ydl.prepare_filename(info)

    return filename  # مثل "source.mp4"


def cut_video_range(input_file: str, start_seconds: int, duration_seconds: int, output_file: str = "cut_full.mp4") -> str:
    """
    قص المقطع من الفيديو الأصلي حسب البداية والمدة.
    نستخدم -c copy للحفاظ على الجودة والسرعة.
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


def get_video_duration(input_file: str) -> float:
    """
    إرجاع مدة الفيديو بالثواني باستخدام ffprobe.
    """
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        input_file,
    ]
    result = subprocess.check_output(command, stderr=subprocess.DEVNULL).decode().strip()
    return float(result)


def split_video_to_parts(input_file: str, max_mb: int = MAX_TELEGRAM_MB):
    """
    تقسيم الفيديو إلى أجزاء حسب الحجم المستهدف (تقريبياً).
    نعتمد على تقسيم المدة إلى N أجزاء (ceiling) حتى لا يضيع الجزء الأخير الصغير.
    """
    limit_bytes = max_mb * 1024 * 1024
    size_bytes = os.path.getsize(input_file)

    if size_bytes <= limit_bytes:
        return [input_file]

    duration = get_video_duration(input_file)

    # عدد الأجزاء (ceiling) لضمان عدم ضياع أي جزء صغير
    num_parts = math.ceil(size_bytes / limit_bytes)
    if num_parts < 1:
        num_parts = 1

    part_duration = duration / num_parts

    base, ext = os.path.splitext(input_file)
    output_files = []

    for i in range(num_parts):
        start = part_duration * i
        out_file = f"{base}_part{i+1}{ext}"

        command = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-i",
            input_file,
            "-t",
            str(part_duration),
            "-c",
            "copy",
            out_file,
        ]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if os.path.exists(out_file) and os.path.getsize(out_file) > 0:
            output_files.append(out_file)

    return output_files


def clean_files(*paths):
    """حذف الملفات المؤقتة بأمان."""
    for p in paths:
        if not p:
            continue
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


# ================= منطق البوت =================

@bot.message_handler(commands=["start"])
def handle_start_cmd(message):
    chat_id = message.chat.id
    reset_session(chat_id)
    bot.reply_to(
        message,
        "👋 أهلاً بك في بوت <b>قص مقاطع يوتيوب</b>.\n\n"
        "أرسل الآن رابط فيديو يوتيوب (عادي أو بث محفوظ)، "
        "أو يمكنك في أي وقت إرسال رابط جديد وسيبدأ البوت من جديد 😉."
    )


@bot.message_handler(func=lambda m: m.text is not None)
def handle_text(message):
    chat_id = message.chat.id
    text = message.text.strip()

    # لو كتب أمر /start سيُعالَج في الهاندلر الخاص به
    if text.startswith("/"):
        return

    # لو أرسل رابط يوتيوب في أي لحظة -> نبدأ جلسة جديدة مباشرة
    if "youtu.be" in text or "youtube.com" in text:
        url = extract_url(text)
        user_sessions[chat_id] = {
            "step": "await_start",
            "url": url,
        }
        bot.reply_to(
            message,
            "✅ تم استلام رابط يوتيوب.\n\n"
            "⏱️ أرسل وقت <b>البداية</b> بصيغة مثل:\n"
            "<code>80</code> (ثواني)\n"
            "<code>1:20</code>\n"
            "<code>00:01:20</code>"
        )
        return

    # إن لم تكن جلسة موجودة، نطلب منه رابط أو /start
    session = user_sessions.get(chat_id)
    if not session:
        bot.reply_to(
            message,
            "⚠️ أرسل أولاً رابط فيديو يوتيوب أو استخدم الأمر /start."
        )
        return

    step = session.get("step", "await_url")

    if step == "await_url":
        # هذا السيناريو قليل لأننا نعتمد على رابط يوتيوب لتشغيل الجلسة
        if "youtu" not in text:
            bot.reply_to(message, "⚠️ أرسل رابط يوتيوب صحيح لبدء القص.")
            return
        url = extract_url(text)
        session["url"] = url
        session["step"] = "await_start"
        bot.reply_to(
            message,
            "⏱️ أرسل وقت <b>البداية</b> بصيغة مثل:\n"
            "<code>80</code>\n<code>1:20</code>\n<code>00:01:20</code>"
        )

    elif step == "await_start":
        try:
            start_seconds = parse_time_to_seconds(text)
        except ValueError:
            bot.reply_to(message, "⚠️ صيغة وقت البداية غير صحيحة، أعد الإرسال.")
            return

        session["start"] = start_seconds
        session["step"] = "await_end"
        bot.reply_to(
            message,
            "⏱️ الآن أرسل وقت <b>النهاية</b> لنقطة القص بنفس الصيغ السابقة.\n"
            "مثال: <code>00:05:00</code> يعني بعد 5 دقائق من بداية الفيديو."
        )

    elif step == "await_end":
        try:
            end_seconds = parse_time_to_seconds(text)
        except ValueError:
            bot.reply_to(message, "⚠️ صيغة وقت النهاية غير صحيحة، أعد الإرسال.")
            return

        start_seconds = session.get("start", 0)
        if end_seconds <= start_seconds:
            bot.reply_to(
                message,
                "⚠️ يجب أن يكون وقت النهاية أكبر من وقت البداية.\nأعد إرسال وقت النهاية."
            )
            return

        duration = end_seconds - start_seconds
        session["end"] = end_seconds
        session["duration"] = duration

        # الآن فحص الجودات
        bot.reply_to(message, "⏳ يتم فحص الجودات المتاحة للفيديو…")

        video_url = session["url"]
        try:
            heights = get_available_qualities(video_url)
        except Exception as e:
            logger.error("Error getting qualities from YouTube", exc_info=e)
            # لو فشل الفحص، نستخدم 360p افتراضياً
            session["quality_height"] = 360
            session["step"] = "processing"
            bot.send_message(
                chat_id,
                "❌ حدث خطأ أثناء فحص الجودات من يوتيوب.\n"
                "سيتم استخدام جودة <b>360p</b> افتراضياً."
            )
            start_cutting(chat_id)
            return

        if not heights:
            # نفس الشيء: لو ما وجد أي جودة "مع صوت"
            session["quality_height"] = 360
            session["step"] = "processing"
            bot.send_message(
                chat_id,
                "⚠️ لم أجد جودات قياسية (144p–1080p) مع صوت.\n"
                "سيتم استخدام جودة <b>360p</b> افتراضياً."
            )
            start_cutting(chat_id)
            return

        # حفظ أن لدينا جودات متاحة
        session["available_heights"] = heights
        session["step"] = "choose_quality"

        # إنشاء أزرار حسب الجودات الموجودة فعلاً
        markup = InlineKeyboardMarkup()
        row = []
        for h in [144, 240, 360, 480, 720, 1080]:
            if h in heights:
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

    elif step in ("choose_quality", "processing"):
        bot.reply_to(
            message,
            "⌛ يتم حالياً تجهيز المقطع.\n"
            "انتظر حتى ينتهي أو أرسل رابط يوتيوب جديد لبدء عملية جديدة."
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("q_"))
def handle_quality_callback(call):
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id)

    if not session:
        bot.answer_callback_query(call.id, "انتهت الجلسة، أرسل رابطاً جديداً.", show_alert=True)
        return

    try:
        height = int(call.data.split("_")[1])
    except Exception:
        bot.answer_callback_query(call.id, "⚠️ خطأ في اختيار الجودة.", show_alert=True)
        return

    available_heights = session.get("available_heights") or []
    if height not in available_heights:
        bot.answer_callback_query(call.id, "⚠️ هذه الجودة غير متاحة لهذا الفيديو.", show_alert=True)
        return

    session["quality_height"] = height
    session["step"] = "processing"

    bot.answer_callback_query(call.id, f"تم اختيار الجودة: {height}p ✅", show_alert=False)

    try:
        bot.edit_message_text(
            f"✅ تم اختيار الجودة: <b>{height}p</b>\n"
            "سيتم الآن تحميل الفيديو وقص المقطع وإرساله…",
            chat_id=chat_id,
            message_id=call.message.message_id
        )
    except Exception:
        # لو فشل التعديل لا مشكلة
        pass

    start_cutting(chat_id)


def start_cutting(chat_id: int):
    """تحميل الفيديو، قص المقطع، تقسيمه لأجزاء مناسبة، وإرساله كفيديو."""
    session = user_sessions.get(chat_id)
    if not session:
        bot.send_message(chat_id, "⚠️ حصل خطأ في الجلسة. أرسل رابط يوتيوب من جديد.")
        return

    url = session.get("url")
    start_seconds = session.get("start")
    duration = session.get("duration")
    quality_height = session.get("quality_height")

    if url is None or start_seconds is None or duration is None:
        bot.send_message(chat_id, "⚠️ بيانات الجلسة غير مكتملة. أرسل رابطاً جديداً.")
        return

    bot.send_message(
        chat_id,
        "🔧 جاري تحميل الفيديو وقص المقطع…\n"
        "قد يستغرق ذلك بعض الوقت حسب طول المقطع والجودة."
    )

    input_file = None
    cut_file = None
    parts = []

    try:
        # تحميل الفيديو مع صوت
        input_file = download_video(url, quality_height, output_name="source")
        logger.info("Downloaded video file: %s", input_file)

        # قص المقطع المطلوب
        cut_file = cut_video_range(input_file, start_seconds, duration, output_file="cut_full.mp4")
        logger.info("Cut file created: %s", cut_file)

        # تقسيم المقطع إلى أجزاء حسب الحجم
        parts = split_video_to_parts(cut_file, max_mb=MAX_TELEGRAM_MB)
        logger.info("Parts to send: %s", parts)

        total_parts = len(parts)
        if total_parts == 0:
            bot.send_message(chat_id, "❌ لم أستطع استخراج المقطع بعد القص.")
            return

        # إرسال الأجزاء كفيديو (مع صوت) واحداً تلو الآخر
        for idx, part in enumerate(parts, start=1):
            bot.send_message(
                chat_id,
                f"📤 جاري إرسال الجزء {idx}/{total_parts}…"
            )
            with open(part, "rb") as f:
                try:
                    bot.send_video(
                        chat_id,
                        f,
                        caption=f"🎬 الجزء {idx}/{total_parts}",
                    )
                except ApiTelegramException as e:
                    # لو ظهر خطأ حجم كبير جداً من تيليجرام
                    if "413" in str(e) or "Request Entity Too Large" in str(e):
                        bot.send_message(
                            chat_id,
                            "❌ تيليجرام رفض هذا الجزء لأن حجمه ما زال أكبر من المسموح.\n"
                            "حاول قص مدة أقصر أو اختيار جودة أقل."
                        )
                    else:
                        bot.send_message(
                            chat_id,
                            f"❌ خطأ من تيليجرام أثناء إرسال الجزء {idx}:\n<code>{e}</code>"
                        )
                    # نستمر في حذف الملفات على أي حال
                    break

        bot.send_message(
            chat_id,
            "✅ انتهى إرسال المقطع.\n"
            "يمكنك الآن إرسال رابط يوتيوب جديد لقص مقطع آخر 🎯."
        )
        # بعد الانتهاء نضع الحالة إلى await_url
        reset_session(chat_id)

    except DownloadError as e:
        logger.error("DownloadError from YouTube", exc_info=e)
        bot.send_message(
            chat_id,
            "❌ حدث خطأ أثناء تحميل الفيديو من يوتيوب.\n"
            "تأكد أن رابط الفيديو يعمل، وأن ملف <b>cookies.txt</b> صحيح ومحدّث."
        )
    except Exception as e:
        logger.error("Unexpected error in start_cutting", exc_info=e)
        bot.send_message(
            chat_id,
            "❌ حدث خطأ غير متوقع أثناء القص أو التحميل."
        )
    finally:
        # تنظيف الملفات المؤقتة
        try:
            clean_files(input_file, cut_file, *parts)
        except Exception:
            pass


# ================= تشغيل البوت مع معالجة أخطاء polling =================
if __name__ == "__main__":
    logger.info("🔥 Bot is running…")

    while True:
        try:
            # skip_pending=True حتى لا يأخذ رسائل قديمة عند كل إعادة تشغيل
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            logger.error("Polling error from Telegram: %s", e)
            # ملاحظة: لو ظهر خطأ 409 فهذا يعني أن هناك نسخة أخرى من البوت تعمل بنفس التوكن
            # يجب إيقاف أي Instance أخرى للبوت (في Koyeb أو أي مكان آخر).
            time.sleep(5)
