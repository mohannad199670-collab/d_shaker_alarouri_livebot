import os
import math
import time
import json
import logging
import subprocess
from datetime import datetime, date, timedelta

import telebot
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telebot.apihelper import ApiTelegramException

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# ================= إعداد اللوج =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ================ إعداد التوكن و ID الأدمن ================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN غير موجود في Environment variables")

# ADMIN_ID من متغير البيئة إن وُجد، وإلا يستخدم ID الافتراضي (ID الخاص بك)
ADMIN_ENV = os.getenv("ADMIN_ID", "").strip()
try:
    ADMIN_ID = int(ADMIN_ENV) if ADMIN_ENV else 604494923
except ValueError:
    ADMIN_ID = 604494923
    logger.warning("⚠️ قيمة ADMIN_ID في البيئة غير صالحة، سيتم استخدام 604494923 كأدمن افتراضي")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ================ إعداد الكوكيز الخاصة بيوتيوب ================
# متغير البيئة الذي تضع فيه هيدر الكوكيز الكامل:
# مثال: SID=...; HSID=...; SSID=...; APISID=...; SAPISID=...
YT_COOKIES_HEADER = os.getenv("YT_COOKIES_HEADER", os.getenv("YT_COOKIES", "")).strip()

# إلغاء استخدام ملف cookies.txt نهائياً
COOKIES_PATH = None

# ================ إعدادات الحجم =================
MAX_TELEGRAM_MB = 48  # الحد المستهدف لكل جزء (تقريباً 48 ميغا)

# ================ ملف قاعدة البيانات البسيطة =================
DB_FILE = "database.json"

DEFAULT_DB = {
    "users": {},            # user_id(str) -> user_data
    "visitors_today": 0,
    "last_reset_date": "",  # "YYYY-MM-DD"
}


def load_db():
    if not os.path.exists(DB_FILE):
        return DEFAULT_DB.copy()
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # تأمين المفاتيح الأساسية
        for k, v in DEFAULT_DB.items():
            if k not in data:
                data[k] = v
        return data
    except Exception as e:
        logger.error("Error loading DB, using default: %s", e)
        return DEFAULT_DB.copy()


def save_db(db):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Error saving DB: %s", e)


def today_str() -> str:
    return date.today().isoformat()


def ensure_daily_reset(db):
    t = today_str()
    if db.get("last_reset_date") != t:
        db["visitors_today"] = 0
        db["last_reset_date"] = t


def ensure_user(db, user_id: int, first_name: str, username: str):
    """إنشاء/تحديث سجل المستخدم في قاعدة البيانات"""
    uid = str(user_id)
    users = db.setdefault("users", {})
    user = users.get(uid) or {}
    user.setdefault("subscription", None)  # أو dict
    user.setdefault("total_visits", 0)
    user.setdefault("joined_at", today_str())

    user["first_name"] = first_name or ""
    user["username"] = username or ""
    user["last_seen"] = today_str()
    user["total_visits"] = int(user.get("total_visits", 0)) + 1

    users[uid] = user
    db["users"] = users


def register_visit(user_id: int, first_name: str, username: str):
    """تسجيل زيارة مستخدم (يُستدعى في /start)"""
    db = load_db()
    ensure_daily_reset(db)
    db["visitors_today"] = int(db.get("visitors_today", 0)) + 1
    ensure_user(db, user_id, first_name, username)
    save_db(db)


# ================ نظام الإشتراكات ================

PLANS = {
    "p1": {"name": "شهر واحد", "days": 30},
    "p3": {"name": "3 شهور", "days": 90},
    "p6": {"name": "6 شهور", "days": 180},
    "p12": {"name": "سنة كاملة", "days": 365},
}


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def get_user_record(user_id: int):
    db = load_db()
    uid = str(user_id)
    return db["users"].get(uid)


def set_user_record(user_id: int, record: dict):
    db = load_db()
    db["users"][str(user_id)] = record
    save_db(db)


def set_subscription(user_id: int, plan_key: str):
    """تفعيل اشتراك للمستخدم حسب الخطة"""
    if plan_key not in PLANS:
        return

    db = load_db()
    uid = str(user_id)
    users = db.setdefault("users", {})
    user = users.get(uid) or {}
    ensure_user(db, user_id, user.get("first_name", ""), user.get("username", ""))

    plan = PLANS[plan_key]
    today = date.today()
    end_date = today + timedelta(days=plan["days"])

    subscription = user.get("subscription") or {}
    subscription.update(
        {
            "active": True,
            "plan_key": plan_key,
            "plan_name": plan["name"],
            "days": plan["days"],
            "start_date": today.isoformat(),
            "end_date": end_date.isoformat(),
        }
    )
    user["subscription"] = subscription
    users[uid] = user
    db["users"] = users
    save_db(db)


def clear_subscription(user_id: int):
    db = load_db()
    uid = str(user_id)
    users = db.setdefault("users", {})
    user = users.get(uid)
    if not user:
        return
    sub = user.get("subscription") or {}
    sub.update(
        {
            "active": False,
        }
    )
    user["subscription"] = sub
    users[uid] = user
    db["users"] = users
    save_db(db)


def _normalize_subscription(user_id: int):
    """يتأكد من انتهاء الاشتراكات المنتهية تلقائياً"""
    db = load_db()
    uid = str(user_id)
    user = db["users"].get(uid)
    if not user:
        return None

    sub = user.get("subscription")
    if not sub:
        return None

    end_str = sub.get("end_date")
    if not end_str:
        sub["active"] = False
    else:
        try:
            end_d = date.fromisoformat(end_str)
            if end_d < date.today():
                sub["active"] = False
        except Exception:
            sub["active"] = False

    user["subscription"] = sub
    db["users"][uid] = user
    save_db(db)
    return sub


def has_active_subscription(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    sub = _normalize_subscription(user_id)
    return bool(sub and sub.get("active"))


def subscription_status_text(user_id: int) -> str:
    sub = _normalize_subscription(user_id)
    if not sub or not sub.get("active"):
        return "غير مشترك حالياً."

    plan_name = sub.get("plan_name", "باقة غير معروفة")
    end_str = sub.get("end_date", "")
    days_total = sub.get("days", 0)

    try:
        end_d = date.fromisoformat(end_str)
        remaining = (end_d - date.today()).days
        if remaining < 0:
            remaining = 0
    except Exception:
        remaining = 0

    return (
        f"📦 الباقة الحالية: <b>{plan_name}</b>\n"
        f"📅 تاريخ الانتهاء: <code>{end_str}</code>\n"
        f"⏳ الأيام المتبقية: <b>{remaining}</b> يوم"
    )


def get_stats_text() -> str:
    db = load_db()
    users = db.get("users", {})
    total_visitors = len(users)

    today_active = 0
    total_active = 0
    today_iso = today_str()

    for uid, user in users.items():
        sub = user.get("subscription") or {}
        active = bool(sub.get("active"))
        if active:
            try:
                end_d = date.fromisoformat(sub.get("end_date", today_iso))
                if end_d < date.today():
                    active = False
            except Exception:
                active = False

        if active:
            total_active += 1

        # زائر اليوم (حسب last_seen)
        if user.get("last_seen") == today_iso:
            today_active += 1

    visitors_today = db.get("visitors_today", 0)

    return (
        "📊 <b>إحصائيات البوت</b>\n\n"
        f"👥 إجمالي الزوار: <b>{total_visitors}</b>\n"
        f"🧑‍💻 إجمالي المشتركين: <b>{total_active}</b>\n"
        f"📅 زوار اليوم (من قاعدة البيانات): <b>{visitors_today}</b>"
    )


# ================ إدارة جلسات المستخدم =================
# لكل مستخدم نخزن الحالة هنا
# {
#   chat_id: {
#       "step": "...",
#       "url": "...",
#       "start": 10,
#       "end": 120,
#       "duration": 110,
#       "quality_height": 360,
#       "mode": "video" / "audio",
#       "pending_plan": "p1" / "p3" / ...,
#   }
# }
user_sessions = {}


def reset_session(chat_id: int):
    """إعادة تهيئة جلسة المستخدم."""
    user_sessions[chat_id] = {
        "step": "await_url"
    }


# ================ دوال مساعدة للواجهة ================

def build_main_keyboard(chat_id: int):
    kb = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)

    # الصف الأول: قص + الاشتراكات للجميع
    kb.row(
        KeyboardButton("✂️ قص مقطع يوتيوب"),
        KeyboardButton("📦 الاشتراكات"),
    )

    # الصف الثاني:
    if is_admin(chat_id):
        # للأدمن: الإعدادات + لوحة التحكم معاً
        kb.row(
            KeyboardButton("⚙️ الإعدادات"),
            KeyboardButton("🛠 لوحة التحكم"),
        )
    else:
        # للمستخدم العادي: الإعدادات فقط
        kb.row(KeyboardButton("⚙️ الإعدادات"))

    return kb


def build_plans_keyboard(for_admin_manual: bool = False):
    """لوحة اختيار الباقات (تُستخدم للعميل وللأدمن)"""
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("شهر واحد", callback_data="plan_p1_admin" if for_admin_manual else "plan_p1_user"),
        InlineKeyboardButton("3 شهور", callback_data="plan_p3_admin" if for_admin_manual else "plan_p3_user"),
    )
    markup.row(
        InlineKeyboardButton("6 شهور", callback_data="plan_p6_admin" if for_admin_manual else "plan_p6_user"),
        InlineKeyboardButton("سنة كاملة", callback_data="plan_p12_admin" if for_admin_manual else "plan_p12_user"),
    )
    return markup


# ================ دوال قص الفيديو وتحميله ================

def extract_url(text: str) -> str:
    """يأخذ أول جزء يبدو كرابط من النص."""
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

    # استخدام الكوكيز من الهيدر إذا موجودة
    if YT_COOKIES_HEADER:
        ydl_opts["http_headers"] = {
            "Cookie": YT_COOKIES_HEADER
        }

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

    if YT_COOKIES_HEADER:
        ydl_opts["http_headers"] = {
            "Cookie": YT_COOKIES_HEADER
        }

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


# ================ منطق البوت ================

@bot.message_handler(commands=["start"])
def handle_start_cmd(message):
    chat_id = message.chat.id
    user = message.from_user
    user_id = user.id
    first_name = user.first_name or ""
    username = user.username or ""

    # تسجيل الزيارة في قاعدة البيانات
    register_visit(user_id, first_name, username)

    # إشعار للأدمن عند دخول مستخدم جديد /start
    if is_admin(ADMIN_ID):
        try:
            username_display = f"@{username}" if username else "بدون يوزر"
            profile_link = f"https://t.me/{username}" if username else "لا يوجد رابط"

            bot.send_message(
                ADMIN_ID,
                f"📥 <b>دخول جديد للبوت</b>\n\n"
                f"🆔 ID: <code>{user_id}</code>\n"
                f"👤 الاسم: {first_name}\n"
                f"🪪 اليوزر: {username_display}\n"
                f"🔗 الرابط: {profile_link}"
            )
        except Exception:
            pass

    reset_session(chat_id)

    # لوحة المفاتيح الرئيسية
    reply_kb = build_main_keyboard(chat_id)

    # رسالة ترحيب
    welcome_text = (
        "👋 أهلاً بك في بوت <b>قص مقاطع يوتيوب</b>.\n\n"
        "✂️ يتيح لك البوت قص جزء محدد من أي فيديو (أو بث محفوظ) من يوتيوب "
        "وإرساله لك بجودة تختارها.\n\n"
        "🔒 لاستخدام البوت بشكل كامل، يرجى الاشتراك في إحدى الباقات من زر <b>📦 الاشتراكات</b>.\n\n"
        "📌 ملاحظة: إذا تجاوز حجم الفيديو الناتج <b>48 ميغابايت</b> سيتم تقسيمه تلقائياً "
        "إلى عدة أجزاء حسب طول المقطع والجودة."
    )

    bot.send_message(chat_id, welcome_text, reply_markup=reply_kb)

    # رسالة معلومات المستخدم والاشتراك
    username_display = f"@{username}" if username else "لا يوجد"
    info_text = (
        "ℹ️ <b>معلومات حسابك في البوت</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"👤 الاسم: {first_name}\n"
        f"🪪 اليوزر: {username_display}\n\n"
        f"{subscription_status_text(user_id)}"
    )
    bot.send_message(chat_id, info_text)


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    """استقبال لقطة شاشة الدفع من العميل"""
    chat_id = message.chat.id
    session = user_sessions.get(chat_id, {})
    step = session.get("step")

    # إذا كان طالب اشتراك ويرسل لقطة
    if step == "await_payment_proof" and session.get("pending_plan"):
        plan_key = session.get("pending_plan")
        plan = PLANS.get(plan_key)
        if not plan:
            bot.reply_to(message, "⚠️ حدث خطأ في تحديد الباقة، أعد الطلب من جديد من زر الاشتراكات.")
            reset_session(chat_id)
            return

        user = message.from_user
        user_id = user.id
        first_name = user.first_name or ""
        username = user.username or ""
        username_display = f"@{username}" if username else "بدون يوزر"
        profile_link = f"https://t.me/{username}" if username else "لا يوجد رابط"

        # إرسال الصورة للأدمن مع أزرار قبول/رفض
        try:
            caption = (
                "🧾 <b>طلب اشتراك جديد</b>\n\n"
                f"🆔 ID: <code>{user_id}</code>\n"
                f"👤 الاسم: {first_name}\n"
                f"🪪 اليوزر: {username_display}\n"
                f"🔗 الرابط: {profile_link}\n\n"
                f"📦 الباقة المطلوبة: <b>{plan['name']}</b>\n"
                f"⏳ مدة الباقة: <b>{plan['days']}</b> يوم"
            )

            markup = InlineKeyboardMarkup()
            markup.row(
                InlineKeyboardButton("✅ تفعيل الاشتراك", callback_data=f"payok|{user_id}|{plan_key}"),
                InlineKeyboardButton("❌ رفض الطلب", callback_data=f"payno|{user_id}|{plan_key}"),
            )

            file_id = message.photo[-1].file_id
            bot.send_photo(
                ADMIN_ID,
                file_id,
                caption=caption,
                reply_markup=markup,
            )
        except Exception as e:
            logger.error("Error sending payment proof to admin: %s", e)

        bot.reply_to(
            message,
            "✅ تم استلام لقطة شاشة الدفع.\n"
            "📡 سيتم مراجعة طلبك من قبل الإدارة، وستصلك رسالة عند تفعيل الباقة أو رفض الطلب."
        )

        # إعادة الجلسة للوضع الافتراضي
        reset_session(chat_id)
        return

    # إن لم نكن بمرحلة الدفع، نتجاهل الصورة أو نرسل رسالة بسيطة
    bot.reply_to(message, "📷 تم استلام الصورة، ولكن لا يوجد طلب اشتراك قيد المراجعة حالياً.")


@bot.message_handler(func=lambda m: m.text is not None)
def handle_text(message):
    chat_id = message.chat.id
    text = message.text.strip()

    # الأوامر النصية الخاصة
    if text.startswith("/"):
        return

    # لوحة المفاتيح الرئيسية
    if text == "✂️ قص مقطع يوتيوب":
        if not has_active_subscription(chat_id):
            bot.reply_to(
                message,
                "🔒 لا يمكنك استخدام خدمة القص حالياً.\n"
                "يرجى الاشتراك من زر <b>📦 الاشتراكات</b> أولاً."
            )
            return
        reset_session(chat_id)
        bot.send_message(
            chat_id,
            "🔗 أرسل الآن رابط فيديو يوتيوب (عادي أو بث محفوظ) لبدء عملية القص.",
        )
        return

    if text == "📦 الاشتراكات":
        user_id = message.from_user.id
        status = subscription_status_text(user_id)
        bot.send_message(
            chat_id,
            f"{status}\n\n"
            "🧾 <b>اختر الباقة التي ترغب بها:</b>",
            reply_markup=build_plans_keyboard(for_admin_manual=False),
        )
        return

    if text == "⚙️ الإعدادات":
        user = message.from_user
        user_id = user.id
        first_name = user.first_name or ""
        username = user.username or ""
        username_display = f"@{username}" if username else "لا يوجد"
        info_text = (
            "⚙️ <b>إعدادات حسابك</b>\n\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"👤 الاسم: {first_name}\n"
            f"🪪 اليوزر: {username_display}\n\n"
            f"{subscription_status_text(user_id)}"
        )
        bot.send_message(chat_id, info_text)
        return

    if text == "🛠 لوحة التحكم":
        if not is_admin(chat_id):
            bot.reply_to(message, "❌ هذه اللوحة مخصصة للإدارة فقط.")
            return
        show_admin_panel(chat_id)
        return

    # لو أرسل رابط يوتيوب مباشرة في أي وقت -> نبدأ القص (إن كان مشتركاً)
    if "youtu.be" in text or "youtube.com" in text:
        if not has_active_subscription(chat_id):
            bot.reply_to(
                message,
                "🔒 لا يمكنك استخدام خدمة القص حالياً.\n"
                "يرجى الاشتراك من زر <b>📦 الاشتراكات</b> أولاً."
            )
            return

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

    # من هنا وما بعده نتعامل مع خطوات القص
    session = user_sessions.get(chat_id)
    if not session:
        bot.reply_to(
            message,
            "⚠️ أرسل أولاً رابط فيديو يوتيوب أو استخدم زر <b>✂️ قص مقطع يوتيوب</b>."
        )
        return

    step = session.get("step", "await_url")

    if step == "await_url":
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
            session["step"] = "choose_mode"
            bot.send_message(
                chat_id,
                "❌ حدث خطأ أثناء فحص الجودات من يوتيوب.\n"
                "سيتم استخدام جودة <b>360p</b> افتراضياً."
            )
            ask_video_or_audio(chat_id)
            return

        if not heights:
            session["quality_height"] = 360
            session["step"] = "choose_mode"
            bot.send_message(
                chat_id,
                "⚠️ لم أجد جودات قياسية (144p–1080p) مع صوت.\n"
                "سيتم استخدام جودة <b>360p</b> افتراضياً."
            )
            ask_video_or_audio(chat_id)
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
            "🎛️ <b>اختر الجودة</b> من الأزرار بالأسفل:",
            reply_markup=markup
        )

    elif step in ("choose_quality", "choose_mode", "processing"):
        bot.reply_to(
            message,
            "⌛ يتم حالياً تجهيز المقطع أو انتظار اختيار الجودة/نوع الملف.\n"
            "انتظر حتى ينتهي أو أرسل رابط يوتيوب جديد لبدء عملية جديدة."
        )


def ask_video_or_audio(chat_id: int):
    """سؤال المستخدم: هل يريد فيديو أم صوت فقط؟"""
    session = user_sessions.get(chat_id)
    if not session:
        return

    session["step"] = "choose_mode"
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🎬 فيديو", callback_data="mode_video"),
        InlineKeyboardButton("🎧 صوت", callback_data="mode_audio"),
    )
    bot.send_message(
        chat_id,
        "🎚️ <b>اختر نوع الملف الذي تريده:</b>",
        reply_markup=markup
    )


# ========== كولباكات الاشتراكات والجودات وأنواع الملفات وطلبات الدفع ولوحة التحكم ==========

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    chat_id = call.message.chat.id
    data = call.data or ""

    # أولاً: كولباكات الدفع (تفعيل/رفض من الأدمن)
    if data.startswith("payok|") or data.startswith("payno|"):
        if not is_admin(chat_id):
            bot.answer_callback_query(call.id, "هذه الأزرار خاصة بالإدارة.", show_alert=True)
            return

        try:
            action, user_id_str, plan_key = data.split("|", 2)
            target_id = int(user_id_str)
        except Exception:
            bot.answer_callback_query(call.id, "بيانات الطلب غير صالحة.", show_alert=True)
            return

        plan = PLANS.get(plan_key)
        if not plan:
            bot.answer_callback_query(call.id, "الباقة غير معروفة.", show_alert=True)
            return

        if action == "payok":
            set_subscription(target_id, plan_key)
            status = subscription_status_text(target_id)
            # رسالة للعميل
            try:
                bot.send_message(
                    target_id,
                    "✅ تم تفعيل اشتراكك بنجاح.\n\n" + status
                )
            except Exception:
                pass

            # تعديل رسالة الأدمن
            try:
                bot.edit_message_caption(
                    caption=call.message.caption + "\n\n✅ <b>تم تفعيل الاشتراك لهذا المستخدم.</b>",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                )
            except Exception:
                pass

            bot.answer_callback_query(call.id, "تم تفعيل الاشتراك 👍")
            return

        elif action == "payno":
            # رسالة للعميل
            try:
                bot.send_message(
                    target_id,
                    "❌ تم رفض طلب الاشتراك.\n"
                    "السبب: لم يتم تأكيد عملية الدفع من قبل الإدارة."
                )
            except Exception:
                pass

            # تعديل رسالة الأدمن
            try:
                bot.edit_message_caption(
                    caption=call.message.caption + "\n\n❌ <b>تم رفض هذا الطلب.</b>",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                )
            except Exception:
                pass

            bot.answer_callback_query(call.id, "تم رفض الطلب.")
            return

    # ثانياً: كولباكات اختيار الباقة للعميل
    if data.startswith("plan_") and data.endswith("_user"):
        plan_key = data[5:-5]  # بين "plan_" و "_user"
        plan = PLANS.get(plan_key)
        if not plan:
            bot.answer_callback_query(call.id, "الباقة غير معروفة.", show_alert=True)
            return

        chat_id_user = call.from_user.id
        session = user_sessions.setdefault(chat_id_user, {})
        session["pending_plan"] = plan_key
        session["step"] = "await_payment_proof"

        bot.answer_callback_query(call.id, f"تم اختيار الباقة: {plan['name']}")
        bot.send_message(
            chat_id_user,
            "📸 الآن أرسل لقطة شاشة لإشعار الدفع ليتم مراجعة طلبك وتفعيل الاشتراك."
        )
        return

    # ثالثاً: كولباكات اختيار الباقة في لوحة التحكم (تفعيل يدوي)
    if data.startswith("plan_") and data.endswith("_admin"):
        if not is_admin(chat_id):
            bot.answer_callback_query(call.id, "هذه الأزرار خاصة بالإدارة.", show_alert=True)
            return

        plan_key = data[5:-6]  # بين "plan_" و "_admin"
        plan = PLANS.get(plan_key)
        if not plan:
            bot.answer_callback_query(call.id, "الباقة غير معروفة.", show_alert=True)
            return

        admin_session = user_sessions.setdefault(chat_id, {})
        admin_session["admin_chosen_plan"] = plan_key
        admin_session["step"] = "admin_wait_user_id"

        bot.answer_callback_query(call.id, f"تم اختيار الباقة: {plan['name']}")
        bot.send_message(
            chat_id,
            "✏️ أرسل الآن <b>ID</b> المستخدم الذي تريد تفعيل هذه الباقة له."
        )
        return

    # رابعاً: كولباكات اختيار الجودة
    if data.startswith("q_"):
        session = user_sessions.get(chat_id)
        if not session:
            bot.answer_callback_query(call.id, "انتهت الجلسة، أرسل رابطاً جديداً.", show_alert=True)
            return

        try:
            height = int(data.split("_")[1])
        except Exception:
            bot.answer_callback_query(call.id, "⚠️ خطأ في اختيار الجودة.", show_alert=True)
            return

        available_heights = session.get("available_heights") or []
        if height not in available_heights:
            bot.answer_callback_query(call.id, "⚠️ هذه الجودة غير متاحة لهذا الفيديو.", show_alert=True)
            return

        session["quality_height"] = height
        session["step"] = "choose_mode"

        bot.answer_callback_query(call.id, f"تم اختيار الجودة: {height}p ✅", show_alert=False)

        try:
            bot.edit_message_text(
                f"✅ تم اختيار الجودة: <b>{height}p</b>\n"
                "الآن اختر نوع الملف الذي تريده:",
                chat_id=chat_id,
                message_id=call.message.message_id
            )
        except Exception:
            pass

        ask_video_or_audio(chat_id)
        return

    # خامساً: كولباكات اختيار نوع الملف (فيديو / صوت)
    if data == "mode_video" or data == "mode_audio":
        session = user_sessions.get(chat_id)
        if not session:
            bot.answer_callback_query(call.id, "انتهت الجلسة، أرسل رابطاً جديداً.", show_alert=True)
            return

        mode = "video" if data == "mode_video" else "audio"
        session["mode"] = mode
        session["step"] = "processing"

        bot.answer_callback_query(
            call.id,
            "سيتم تجهيز المقطع كفيديو 🎬" if mode == "video" else "سيتم تجهيز المقطع كصوت فقط 🎧",
            show_alert=False,
        )

        try:
            bot.edit_message_text(
                ("🎬 سيتم الآن تحميل الفيديو وقص المقطع وإرساله كفيديو…" if mode == "video"
                 else "🎧 سيتم الآن تحميل الفيديو وقص المقطع وإرساله كصوت فقط…"),
                chat_id=chat_id,
                message_id=call.message.message_id
            )
        except Exception:
            pass

        start_cutting(chat_id)
        return

    # سادساً: كولباكات لوحة التحكم الإدارية (تفعيل، إلغاء، إحصائيات)
    if data in ["admin_activate", "admin_cancel", "admin_stats"]:
        if not is_admin(chat_id):
            bot.answer_callback_query(call.id, "هذه الأزرار خاصة بالإدارة.", show_alert=True)
            return

        admin_session = user_sessions.setdefault(chat_id, {})

        if data == "admin_activate":
            bot.answer_callback_query(call.id)
            bot.send_message(
                chat_id,
                "✅ اختر أولاً الباقة التي تريد تفعيلها للمستخدم:",
                reply_markup=build_plans_keyboard(for_admin_manual=True),
            )
            return

        if data == "admin_cancel":
            bot.answer_callback_query(call.id)
            admin_session["step"] = "admin_cancel_wait_id"
            bot.send_message(
                chat_id,
                "⛔ أرسل الآن <b>ID</b> المستخدم الذي تريد إلغاء اشتراكه."
            )
            return

        if data == "admin_stats":
            bot.answer_callback_query(call.id)
            bot.send_message(chat_id, get_stats_text())
            return

    # افتراضي
    bot.answer_callback_query(call.id)


def show_admin_panel(chat_id: int):
    """عرض لوحة التحكم للأدمن"""
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.row(
        KeyboardButton("✂️ قص مقطع يوتيوب"),
        KeyboardButton("📦 الاشتراكات"),
    )
    markup.row(
        KeyboardButton("⚙️ الإعدادات"),
        KeyboardButton("🛠 لوحة التحكم"),
    )

    bot.send_message(
        chat_id,
        "🛠 <b>لوحة التحكم الإدارية</b>\n"
        "اختر الإجراء المطلوب من الأزرار التالية:",
        reply_markup=markup
    )

    # لوحة داخلية بأزرار إنلاين
    inline = InlineKeyboardMarkup()
    inline.row(
        InlineKeyboardButton("✅ تفعيل اشتراك", callback_data="admin_activate"),
        InlineKeyboardButton("⛔ إلغاء اشتراك", callback_data="admin_cancel"),
    )
    inline.row(
        InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats"),
    )
    bot.send_message(chat_id, "اختر من لوحة التحكم:", reply_markup=inline)


def start_cutting(chat_id: int):
    """تحميل الفيديو، قص المقطع، تقسيمه لأجزاء مناسبة، وإرساله كفيديو أو صوت."""
    session = user_sessions.get(chat_id)
    if not session:
        bot.send_message(chat_id, "⚠️ حصل خطأ في الجلسة. أرسل رابط يوتيوب من جديد.")
        return

    url = session.get("url")
    start_seconds = session.get("start")
    duration = session.get("duration")
    quality_height = session.get("quality_height")
    mode = session.get("mode", "video")

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
    audio_file = None

    try:
        # تحميل الفيديو مع صوت
        input_file = download_video(url, quality_height, output_name="source")
        logger.info("Downloaded video file: %s", input_file)

        # قص المقطع المطلوب
        cut_file = cut_video_range(input_file, start_seconds, duration, output_file="cut_full.mp4")
        logger.info("Cut file created: %s", cut_file)

        if mode == "audio":
            # تحويل إلى صوت فقط (m4a)
            audio_file = "cut_audio.m4a"
            command = [
                "ffmpeg",
                "-y",
                "-i",
                cut_file,
                "-vn",
                "-acodec",
                "aac",
                audio_file,
            ]
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            if not os.path.exists(audio_file):
                bot.send_message(chat_id, "❌ لم أستطع استخراج الصوت من المقطع.")
                return

            with open(audio_file, "rb") as f:
                bot.send_audio(
                    chat_id,
                    f,
                    caption="🎧 المقطع الصوتي الذي طلبته.",
                )

            bot.send_message(
                chat_id,
                "✅ انتهى إرسال المقطع.\n"
                "يمكنك الآن إرسال رابط يوتيوب جديد لقص مقطع آخر 🎯."
            )
            reset_session(chat_id)
            return

        # تقسيم المقطع إلى أجزاء (فيديو)
        parts = split_video_to_parts(cut_file, max_mb=MAX_TELEGRAM_MB)
        logger.info("Parts to send: %s", parts)

        total_parts = len(parts)
        if total_parts == 0:
            bot.send_message(chat_id, "❌ لم أستطع استخراج المقطع بعد القص.")
            return

        # إرسال الأجزاء كفيديو
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
                    break

        bot.send_message(
            chat_id,
            "✅ انتهى إرسال المقطع.\n"
            "يمكنك الآن إرسال رابط يوتيوب جديد لقص مقطع آخر 🎯."
        )
        reset_session(chat_id)

    except DownloadError as e:
        logger.error("DownloadError from YouTube", exc_info=e)
        bot.send_message(
            chat_id,
            "❌ حدث خطأ أثناء تحميل الفيديو من يوتيوب.\n"
            "تأكد أن رابط الفيديو يعمل، وأن متغير الكوكيز <b>YT_COOKIES_HEADER</b> (أو YT_COOKIES) صحيح ومحدّث."
        )
    except Exception as e:
        logger.error("Unexpected error in start_cutting", exc_info=e)
        bot.send_message(
            chat_id,
            "❌ حدث خطأ غير متوقع أثناء القص أو التحميل."
        )
    finally:
        try:
            clean_files(input_file, cut_file, audio_file, *parts)
        except Exception:
            pass


# ================ معالجة إدخال ID في لوحة التحكم ================

@bot.message_handler(func=lambda m: m.text is not None and m.chat.id == ADMIN_ID)
@bot.message_handler(func=lambda m: m.text and m.from_user.id == ADMIN_ID)
def handle_admin_text_extra(message):
    """معالجة نصوص إضافية للأدمن (ID للتفعيل/الإلغاء)"""
    chat_id = message.chat.id
    session = user_sessions.get(chat_id) or {}
    step = session.get("step")

    # ====== تفعيل اشتراك عبر ID ======
    if step == "admin_wait_user_id":
        plan_key = session.get("admin_chosen_plan")
        plan = PLANS.get(plan_key) if plan_key else None
        if not plan:
            bot.reply_to(message, "⚠️ لم يتم اختيار باقة بعد، اختر الباقة أولاً من لوحة التحكم.")
            return

        try:
            target_id = int(message.text.strip())
        except ValueError:
            bot.reply_to(message, "⚠️ أرسل ID رقمي صحيح.")
            return

        set_subscription(target_id, plan_key)
        status = subscription_status_text(target_id)

        bot.send_message(
            chat_id,
            f"✅ تم تفعيل باقة <b>{plan['name']}</b> للمستخدم ID: <code>{target_id}</code>."
        )
        try:
            bot.send_message(
                target_id,
                "✅ تم تفعيل اشتراكك من قبل الإدارة.\n\n" + status
            )
        except Exception:
            pass

        session["step"] = None
        session["admin_chosen_plan"] = None
        user_sessions[chat_id] = session
        return

    # ====== إلغاء اشتراك عبر ID ======
    if step == "admin_cancel_wait_id":
        try:
            target_id = int(message.text.strip())
        except ValueError:
            bot.reply_to(message, "⚠️ أرسل ID رقمي صحيح.")
            return

        clear_subscription(target_id)
        bot.send_message(
            chat_id,
            f"⛔ تم إلغاء اشتراك المستخدم ID: <code>{target_id}</code>."
        )
        try:
            bot.send_message(
                target_id,
                "⛔ تم إلغاء اشتراكك من قبل الإدارة."
            )
        except Exception:
            pass

        session["step"] = None
        user_sessions[chat_id] = session
        return

    # إن لم يكن في خطوة إدارية خاصة، نمرره للهاندلر الأساسي
    handle_text(message)

# ================ تشغيل البوت مع معالجة أخطاء polling =================
if __name__ == "__main__":
    logger.info("🔥 Bot is running…")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            logger.error("Polling error from Telegram: %s", e)
            time.sleep(5)
