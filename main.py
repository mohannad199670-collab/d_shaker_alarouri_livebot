import os
import math
import time
import subprocess

import yt_dlp
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.apihelper import ApiTelegramException

# ================= إعداد التوكن =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN غير موجود في Environment variables")

# متغير الكوكيز من Koyeb (يوضع كما هو من ملف cookies.txt)
YT_COOKIES_ENV = os.getenv("YT_COOKIES", "").strip()

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# جلسات المستخدمين لحفظ الحالة
# user_sessions = {
#   chat_id: {
#       "step": "wait_url" | "wait_start" | "wait_end" | "quality",
#       "url": str,
#       "start": int,
#       "end": int,
#       "duration": int,
#       "formats": {height: format_id},
#       "chosen_height": int,
#       "format_id": str,
#   }
# }
user_sessions = {}

# حد الحجم لكل جزء (48 ميغا)
MAX_CHUNK_MB = 48
MAX_CHUNK_BYTES = MAX_CHUNK_MB * 1024 * 1024


# ========= دالة مساعدة: كتابة الكوكيز من المتغيّر إلى ملف =========
def ensure_cookies_file() -> str | None:
    """
    إذا كان متغير YT_COOKIES غير فارغ، يكتب محتواه في cookies.txt
    ويعيد مسار الملف. وإلا يعيد None.
    """
    if not YT_COOKIES_ENV:
        return None

    cookies_path = os.path.join(os.getcwd(), "cookies.txt")
    try:
        with open(cookies_path, "w", encoding="utf-8") as f:
            f.write(YT_COOKIES_ENV)
        return cookies_path
    except Exception as e:
        print("Error writing cookies file:", e)
        return None


COOKIES_PATH = ensure_cookies_file()


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
    يرجع dict مثل: {144: "18", 360: "18", 480: "135", ...}
    يختار فقط الفورمات التي تحتوي فيديو + صوت (acodec != none, vcodec != none)
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "geo_bypass": True,
        # استخدام الكوكيز إن وجدت
        "cookiefile": COOKIES_PATH if COOKIES_PATH else None,
        # تقليل مشاكل SABR
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
    result: dict[int, str] = {}

    for f in formats:
        height = f.get("height")
        fmt_id = f.get("format_id")
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        ext = f.get("ext")

        # نتأكد أنه يحتوي صوت وفيديو (وليس صوت فقط أو فيديو فقط)
        if not height or not fmt_id:
            continue
        if vcodec == "none" or acodec == "none":
            continue
        if ext not in ("mp4", "webm"):
            continue
        if height in target_heights:
            # نخزن آخر فورمات لكل ارتفاع (عادة يكون أفضل)
            result[height] = fmt_id

    return result


# ========= دالة: تحميل الفيديو بالجودة المطلوبة =========
def download_video(video_url: str, format_id: str | None, output_name: str = "source") -> str:
    """
    يقوم بتحميل الفيديو من يوتيوب بالجودة المحددة (format_id إذا موجود)
    وإلا يستخدم best.
    يعيد اسم الملف الناتج.
    """
    if format_id:
        fmt = format_id
    else:
        fmt = "best"

    ydl_opts = {
        "format": fmt,
        "outtmpl": f"{output_name}.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "geo_bypass": True,
        "cookiefile": COOKIES_PATH if COOKIES_PATH else None,
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


# ========= دالة: قص الفيديو =========
def cut_video(input_file: str, start_seconds: int, duration_seconds: int, output_file: str = "cut.mp4") -> str:
    """
    يقص جزء من الفيديو مع إعادة ترميز إلى H.264 + AAC
    حتى نضمن توافق الصوت والفيديو مع تيليجرام.
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

    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output_file


# ========= دالة: معرفة مدة الفيديو بالثواني =========
def get_video_duration(input_file: str) -> float | None:
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nk=1:nw=1",
            input_file,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            return None
        return float(res.stdout.strip())
    except Exception as e:
        print("Error getting duration:", e)
        return None


# ========= دالة: تقسيم الفيديو إلى أجزاء حسب الحجم =========
def split_video_by_size(input_file: str, max_size_bytes: int = MAX_CHUNK_BYTES) -> list[str]:
    """
    يقسم الفيديو إلى عدة ملفات بحيث لا يتجاوز كل واحد الحجم المحدد.
    القِيَم تقريبية حسب المدة، لكنها تضمن عدم تجاهل الجزء الأخير الصغير.
    """
    size = os.path.getsize(input_file)
    if size <= max_size_bytes:
        return [input_file]

    duration = get_video_duration(input_file)
    if not duration or duration <= 0:
        # لا نعرف المدة، نرجع الملف كما هو
        return [input_file]

    # عدد الأجزاء المطلوب (مثلاً إذا كان 100MB والحد 48 => parts = 3)
    parts = math.ceil(size / max_size_bytes)
    part_duration = duration / parts

    print(f"Splitting video: size={size} bytes, duration={duration}s, parts={parts}")

    part_files: list[str] = []
    for i in range(parts):
        start = i * part_duration
        part_name = f"part_{i+1}.mp4"

        if i == parts - 1:
            # الجزء الأخير: حتى نهاية الفيديو
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(start),
                "-i",
                input_file,
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                part_name,
            ]
        else:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(start),
                "-i",
                input_file,
                "-t",
                str(part_duration),
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                part_name,
            ]

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # نتأكد من أن الملف تم إنشاؤه
        if os.path.exists(part_name) and os.path.getsize(part_name) > 0:
            part_files.append(part_name)

    # لو لسبب ما لم ينتج أي جزء، نرجع الملف الأصلي
    if not part_files:
        part_files.append(input_file)

    return part_files


# ========= /start =========
@bot.message_handler(commands=["start"])
def start(message):
    chat_id = message.chat.id
    user_sessions[chat_id] = {"step": "wait_url"}

    bot.reply_to(
        message,
        "👋 أهلاً بك في بوت <b>قص فيديوهات من يوتيوب</b>\n\n"
        "أرسل الآن رابط فيديو يوتيوب (بث محفوظ أو فيديو عادي)."
    )


# ========= /cancel =========
@bot.message_handler(commands=["cancel"])
def cancel(message):
    chat_id = message.chat.id
    user_sessions.pop(chat_id, None)
    bot.reply_to(message, "✅ تم إلغاء العملية الحالية.\nأرسل رابط جديد أو الأمر /start للبدء من جديد.")


# ========= هاندلر واحد لكل الرسائل النصية =========
@bot.message_handler(content_types=["text"])
def handle_text(message):
    chat_id = message.chat.id
    text = message.text.strip()

    # لو كانت الرسالة رابط (يبدأ بـ http) نبدأ جلسة جديدة مباشرة
    if text.startswith("http://") or text.startswith("https://"):
        user_sessions[chat_id] = {
            "step": "wait_start",
            "url": text,
        }
        bot.reply_to(
            message,
            "⏱️ أرسل وقت البداية بصيغة مثل:\n"
            "<code>00:01:20</code> أو <code>1:20</code> أو <code>80</code> ثانية."
        )
        return

    # غير رابط -> نكمل حسب المرحلة
    session = user_sessions.get(chat_id)
    if not session:
        bot.reply_to(message, "⚠️ أرسل أولاً رابط يوتيوب أو الأمر /start للبدء.")
        return

    step = session.get("step")

    # ===== مرحلة انتظار وقت البداية =====
    if step == "wait_start":
        try:
            start_seconds = parse_time_to_seconds(text)
        except ValueError:
            bot.reply_to(message, "⚠️ صيغة وقت غير صحيحة. أعد إرسال وقت البداية بشكل صحيح.")
            return

        session["start"] = start_seconds
        session["step"] = "wait_end"

        bot.reply_to(
            message,
            "⏱️ الآن أرسل وقت النهاية.\n"
            "مثال: <code>00:05:00</code> يعني بعد 5 دقائق من بداية الفيديو."
        )
        return

    # ===== مرحلة انتظار وقت النهاية =====
    if step == "wait_end":
        try:
            end_seconds = parse_time_to_seconds(text)
        except ValueError:
            bot.reply_to(message, "⚠️ صيغة وقت غير صحيحة. أعد إرسال وقت النهاية بشكل صحيح.")
            return

        start_seconds = session["start"]
        if end_seconds <= start_seconds:
            bot.reply_to(message, "⚠️ يجب أن يكون وقت النهاية أكبر من وقت البداية. أعد الإرسال.")
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
                "❌ حدث خطأ أثناء فحص الجودات من يوتيوب.\n"
                "قد يكون هناك مشكلة في الاتصال أو في الكوكيز.\n"
                "تأكد أن متغير <code>YT_COOKIES</code> يحتوي على قيمة صحيحة من ملف cookies.txt."
            )
            session["step"] = None
            return

        if not qualities:
            bot.reply_to(
                message,
                "⚠️ لم يتم العثور على جودات قياسية (144p–1080p).\n"
                "سيتم استخدام أفضل جودة متاحة تلقائياً."
            )
            session["format_id"] = None  # best
            session["step"] = "ready"
            start_cutting(chat_id)
            return

        session["formats"] = qualities
        session["step"] = "quality"

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
        return

    # لو كان في مرحلة اختيار الجودة والرسالة ليست من الأزرار
    if step == "quality":
        bot.reply_to(message, "⬇️ الرجاء اختيار الجودة من الأزرار الموجودة في الرسالة السابقة.")
        return

    # أي شيء آخر
    bot.reply_to(message, "⚠️ لم أفهم الرسالة.\nأرسل رابط يوتيوب جديد أو استخدم /cancel لإلغاء العملية.")


# ========= التعامل مع ضغط زر الجودة =========
@bot.callback_query_handler(func=lambda call: call.data.startswith("q_"))
def handle_quality_callback(call):
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id)
    if not session:
        bot.answer_callback_query(call.id, "انتهت الجلسة. أرسل رابط جديد.", show_alert=True)
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

    session["chosen_height"] = height
    session["format_id"] = fmt_id
    session["step"] = "ready"

    bot.answer_callback_query(call.id, f"تم اختيار الجودة: {height}p ✅", show_alert=False)
    bot.edit_message_text(
        f"✅ تم اختيار الجودة: <b>{height}p</b>\n"
        "سيتم الآن قصّ المقطع وتقسيمه (إن لزم) وإرساله كفيديو…",
        chat_id=chat_id,
        message_id=call.message.message_id
    )

    # بدء عملية القص والإرسال
    start_cutting(chat_id)


# ========= تنفيذ القص + التقسيم + الإرسال =========
def start_cutting(chat_id: int):
    session = user_sessions.get(chat_id)
    if not session:
        bot.send_message(chat_id, "⚠️ حصل خطأ في الجلسة. أرسل رابط جديد.")
        return

    url = session["url"]
    start_seconds = session["start"]
    duration = session["duration"]
    format_id = session.get("format_id")  # قد تكون None => best

    bot.send_message(
        chat_id,
        "🔧 جاري القص… الرجاء الانتظار قليلاً.\n"
        "قد يستغرق ذلك وقتاً حسب طول المقطع والجودة."
    )

    input_file = None
    cut_file = "cut.mp4"
    part_files: list[str] = []

    try:
        # تحميل الفيديو بالجودة المحددة
        input_file = download_video(url, format_id, output_name="source")

        # قص الفيديو
        cut_video(input_file, start_seconds, duration, cut_file)

        # تقسيم حسب الحجم (48MB)
        part_files = split_video_by_size(cut_file, MAX_CHUNK_BYTES)

        # إشعار قبل الإرسال
        bot.send_message(chat_id, "📤 جاري إرسال الفيديو كفيديو (مع الصوت)… الرجاء الانتظار.")

        # إرسال كل جزء كـ Video
        for idx, part in enumerate(part_files, start=1):
            caption = f"✅ المقطع جاهز 🎬\nالجزء {idx} / {len(part_files)}" if len(part_files) > 1 else "✅ المقطع جاهز 🎬"
            with open(part, "rb") as f:
                bot.send_video(chat_id, f, caption=caption)

        bot.send_message(chat_id, "✅ انتهى! يمكنك الآن إرسال رابط جديد مباشرة لقص مقطع آخر.")
        # نضبط الخطوة لانتظار رابط جديد
        session["step"] = "wait_url"

    except ApiTelegramException as e:
        # حجم كبير جداً بالنسبة لحد تيليجرام (نادر لأننا قسمنا، لكن للاحتياط)
        if "413" in str(e) or "Request Entity Too Large" in str(e):
            bot.send_message(
                chat_id,
                "❌ ما زال حجم أحد الأجزاء أكبر من المسموح في تيليجرام.\n"
                "حاول اختيار جودة أقل أو قص مدة أقصر."
            )
        else:
            bot.send_message(chat_id, f"❌ خطأ من تيليجرام أثناء الإرسال:\n<code>{e}</code>")
    except yt_dlp.utils.DownloadError as e:
        print("DownloadError:", e)
        bot.send_message(
            chat_id,
            "❌ حدث خطأ أثناء تحميل الفيديو من يوتيوب.\n"
            "تأكد أن رابط الفيديو يعمل، وأن الكوكيز صحيحة (YT_COOKIES)، ثم حاول مرة أخرى."
        )
    except Exception as e:
        print("Error in start_cutting:", e)
        bot.send_message(chat_id, "❌ حدث خطأ أثناء القص أو التحميل.")
    finally:
        # تنظيف الملفات المؤقتة
        try:
            if input_file and os.path.exists(input_file):
                os.remove(input_file)
            if os.path.exists(cut_file):
                os.remove(cut_file)
            for p in part_files:
                if os.path.exists(p):
                    os.remove(p)
        except Exception:
            pass


# ========= تشغيل البوت مع إعادة المحاولة عند أخطاء الاتصال =========
if __name__ == "__main__":
    print("🔥 Bot is running…")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
        except Exception as e:
            print(f"⚠️ Polling error, will retry in 5s: {e}")
            time.sleep(5)
