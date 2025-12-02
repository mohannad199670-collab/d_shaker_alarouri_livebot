import os
import math
import time
import logging
import subprocess
import json
from datetime import datetime, timedelta

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

# ================= إعداد التوكن و ID الأدمن =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN غير موجود في Environment variables")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")
if not ADMIN_ID:
    logger.warning("⚠️ لم يتم ضبط ADMIN_ID، بعض ميزات لوحة التحكم لن تعمل بشكل صحيح")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ================= إعداد الكوكيز الخاصة بيوتيوب =================
# متغير البيئة الذي تضع فيه هيدر الكوكيز الكامل:
# مثال: SID=...; HSID=...; SSID=...; APISID=...; SAPISID=...
YT_COOKIES_HEADER = os.getenv("YT_COOKIES_HEADER", os.getenv("YT_COOKIES", "")).strip()

# إلغاء استخدام ملف cookies.txt نهائياً (نحن الآن نستخدم الهيدر فقط)
COOKIES_PATH = None

# ================= إعدادات الحجم =================
MAX_TELEGRAM_MB = 48  # الحد المستهدف لكل جزء (تقريباً 48 ميغا)

# ================= ملف الاشتراكات والإحصائيات =================
DB_PATH = "subscriptions.json"


def load_db():
    """قراءة قاعدة بيانات الاشتراكات من ملف JSON."""
    if not os.path.exists(DB_PATH):
        base = {
            "users": {},  # user_id -> info
            "stats": {
                "total_visitors": 0,
                "total_subscribers": 0,
                "visitors_by_date": {},  # "YYYY-MM-DD": count
                "last_subscribers": [],  # آخر 20 مشترك
            },
            "pending": {},  # طلبات اشتراك معلّقة: user_id -> {plan_name, days}
        }
        save_db(base)
        return base

    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        # لو الملف تالف نعيد إنشاؤه
        data = {
            "users": {},
            "stats": {
                "total_visitors": 0,
                "total_subscribers": 0,
                "visitors_by_date": {},
                "last_subscribers": [],
            },
            "pending": {},
        }
        save_db(data)
    # ضمان المفاتيح الأساسية
    data.setdefault("users", {})
    data.setdefault("stats", {})
    data["stats"].setdefault("total_visitors", 0)
    data["stats"].setdefault("total_subscribers", 0)
    data["stats"].setdefault("visitors_by_date", {})
    data["stats"].setdefault("last_subscribers", [])
    data.setdefault("pending", {})
    return data


def save_db(data):
    """حفظ قاعدة البيانات في ملف JSON."""
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


db = load_db()

# ================= إدارة جلسات المستخدم =================
# لكل مستخدم نخزن الحالة هنا
# مثال:
# {
#   chat_id: {
#       "step": "await_url" / "await_start" / "await_end" / "choose_quality" / "processing" / "await_payment_screenshot",
#       "url": "...",
#       "start": 10,
#       "end": 120,
#       "duration": 110,
#       "quality_height": 360,
#       "pending_plan": {"name": "شهر", "days": 30}
#   }
# }
user_sessions = {}

# جلسات خاصة بالأدمن (لتفعيل/إلغاء يدوي)
admin_sessions = {}


def reset_session(chat_id: int):
    """إعادة تهيئة جلسة القص للمستخدم."""
    user_sessions[chat_id] = {
        "step": "await_url"
    }


def get_today_str():
    return datetime.utcnow().strftime("%Y-%m-%d")


def ensure_user_record(user_id: int, first_name: str, username: str | None):
    """ضمان وجود سجل للمستخدم في قاعدة البيانات + تحديث الإحصائيات."""
    global db
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {
            "first_name": first_name or "",
            "username": username or "",
            "is_subscriber": False,
            "plan_name": None,
            "plan_days": 0,
            "start_ts": None,
            "end_ts": None,
            "created_at": datetime.utcnow().isoformat(),
        }
        # زيادة عدد الزوار الإجمالي مرة واحدة لكل مستخدم جديد
        db["stats"]["total_visitors"] += 1

    # تحديث الاسم واليوزر عند كل زيارة
    db["users"][uid]["first_name"] = first_name or ""
    db["users"][uid]["username"] = username or ""

    # تحديث زوار اليوم
    today = get_today_str()
    db["stats"]["visitors_by_date"].setdefault(today, 0)
    db["stats"]["visitors_by_date"][today] += 1

    save_db(db)


def is_user_subscriber(user_id: int) -> bool:
    """التحقق هل المستخدم مشترك حالياً أم لا (حسب تاريخ الانتهاء)."""
    uid = str(user_id)
    info = db["users"].get(uid)
    if not info:
        return False
    end_ts = info.get("end_ts")
    if not end_ts:
        return False
    now_ts = time.time()
    if now_ts > end_ts:
        # انتهى الاشتراك، نحدّث الحالة
        info["is_subscriber"] = False
        save_db(db)
        return False
    info["is_subscriber"] = True
    return True


def get_user_subscription_text(user_id: int) -> str:
    """إرجاع نص وصف حالة الاشتراك للمستخدم."""
    uid = str(user_id)
    info = db["users"].get(uid)
    if not info or not info.get("end_ts"):
        return "📌 حالة الاشتراك: <b>غير مفعّل</b>"

    now_ts = time.time()
    end_ts = info["end_ts"]
    plan_name = info.get("plan_name") or "غير محددة"
    plan_days = info.get("plan_days") or 0

    if now_ts > end_ts:
        return (
            "📌 حالة الاشتراك: <b>منتهي</b>\n"
            f"📦 الباقة السابقة: {plan_name} ({plan_days} يومًا)"
        )

    remaining_seconds = end_ts - now_ts
    remaining_days = math.ceil(remaining_seconds / 86400)
    end_date = datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d")

    return (
        "📌 حالة الاشتراك: <b>فعّال</b>\n"
        f"📦 الباقة الحالية: {plan_name} ({plan_days} يومًا)\n"
        f"⏳ الأيام المتبقية: <b>{remaining_days}</b>\n"
        f"📅 ينتهي بتاريخ: <b>{end_date}</b>"
    )


def activate_subscription(user_id: int, plan_name: str, days: int):
    """تفعيل اشتراك لمستخدم لمدة معينة."""
    global db
    uid = str(user_id)
    info = db["users"].setdefault(uid, {
        "first_name": "",
        "username": "",
        "is_subscriber": False,
        "plan_name": None,
        "plan_days": 0,
        "start_ts": None,
        "end_ts": None,
        "created_at": datetime.utcnow().isoformat(),
    })

    now = datetime.utcnow()
    start_ts = time.time()
    end_ts = start_ts + days * 86400

    info["is_subscriber"] = True
    info["plan_name"] = plan_name
    info["plan_days"] = days
    info["start_ts"] = start_ts
    info["end_ts"] = end_ts

    # إحصائيات المشتركين
    db["stats"]["total_subscribers"] += 1
    last_list = db["stats"]["last_subscribers"]
    if uid in last_list:
        last_list.remove(uid)
    last_list.append(uid)
    # نُبقي آخر 20 فقط
    db["stats"]["last_subscribers"] = last_list[-20:]

    save_db(db)
    return info


def cancel_subscription(user_id: int):
    """إلغاء اشتراك المستخدم (إن وجد)."""
    global db
    uid = str(user_id)
    info = db["users"].get(uid)
    if not info:
        return False
    info["is_subscriber"] = False
    info["plan_name"] = None
    info["plan_days"] = 0
    info["start_ts"] = None
    info["end_ts"] = None
    save_db(db)
    return True


def describe_user_brief(user_id: int) -> str:
    """وصف مختصر للمستخدم (للاستخدام في رسائل الأدمن)."""
    uid = str(user_id)
    info = db["users"].get(uid, {})
    first_name = info.get("first_name", "")
    username = info.get("username", "")
    uname_display = f"@{username}" if username else "بدون يوزر"
    return f"👤 الاسم: {first_name}\n🆔 ID: <code>{uid}</code>\n🪪 اليوزر: {uname_display}"


# ================= دوال مساعدة للقص والتحميل =================
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


# ================= لوحات الأزرار =================

def build_main_keyboard(is_admin: bool = False):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("✂️ قص مقطع"), KeyboardButton("📦 الاشتراكات"))
    kb.row(KeyboardButton("⚙️ الإعدادات"))
    if is_admin:
        kb.row(KeyboardButton("🛠 لوحة التحكم"))
    return kb


def build_subscriptions_keyboard():
    mk = InlineKeyboardMarkup()
    mk.row(
        InlineKeyboardButton("📅 شهر (30 يوم)", callback_data="plan_30_شهر"),
        InlineKeyboardButton("📅 3 أشهر (90 يوم)", callback_data="plan_90_3 أشهر"),
    )
    mk.row(
        InlineKeyboardButton("📅 6 أشهر (180 يوم)", callback_data="plan_180_6 أشهر"),
        InlineKeyboardButton("📅 سنة (365 يوم)", callback_data="plan_365_سنة"),
    )
    return mk


def build_admin_panel_keyboard():
    mk = InlineKeyboardMarkup()
    mk.row(
        InlineKeyboardButton("✅ تفعيل اشتراك يدوي", callback_data="adm_manual_activate"),
        InlineKeyboardButton("⛔️ إلغاء اشتراك يدوي", callback_data="adm_manual_cancel"),
    )
    mk.row(
        InlineKeyboardButton("📊 الإحصائيات", callback_data="adm_stats"),
    )
    return mk


def build_admin_stats_keyboard():
    mk = InlineKeyboardMarkup()
    mk.row(
        InlineKeyboardButton("👥 إجمالي الزوار", callback_data="adm_stats_visitors"),
        InlineKeyboardButton("⭐️ إجمالي المشتركين", callback_data="adm_stats_subscribers"),
    )
    mk.row(
        InlineKeyboardButton("🆕 آخر 20 مشترك", callback_data="adm_stats_last"),
        InlineKeyboardButton("📅 زوار اليوم", callback_data="adm_stats_today"),
    )
    return mk


# ================= منطق البوت =================

@bot.message_handler(commands=["start"])
def handle_start_cmd(message):
    chat_id = message.chat.id
    user = message.from_user
    user_id = user.id
    first_name = user.first_name or ""
    username = user.username or ""

    # حفظ المستخدم والإحصائيات
    ensure_user_record(user_id, first_name, username)

    # إرسال إشعار للأدمن عند دخول شخص جديد للبوت
    if ADMIN_ID:
        try:
            profile_link = f"https://t.me/{username}" if username else "لا يوجد رابط"
            bot.send_message(
                ADMIN_ID,
                f"📥 <b>شخص دخل البوت الآن</b>\n\n"
                f"{describe_user_brief(user_id)}\n"
                f"🔗 الرابط: {profile_link}"
            )
        except Exception:
            pass

    reset_session(chat_id)

    sub_text = get_user_subscription_text(user_id)
    is_admin = (user_id == ADMIN_ID)

    welcome_text = (
        "👋 أهلاً بك في بوت <b>قص مقاطع يوتيوب</b>.\n\n"
        "هذا البوت يسمح لك باختيار مقطع من أي فيديو يوتيوب (عادي أو بث محفوظ)، "
        "وتحميله بالجودة التي تختارها مباشرة من تيليجرام.\n\n"
        "💳 لاستخدام خدمة القص يجب الاشتراك بإحدى الباقات المتاحة من زر <b>📦 الاشتراكات</b>.\n\n"
        "ℹ️ ملاحظة: إذا تجاوز حجم الفيديو <b>48 ميغابايت</b> سيتم تقسيمه تلقائياً إلى عدة أجزاء وإرسالها لك بالتتابع.\n\n"
        "🧾 <b>معلوماتك:</b>\n"
        f"{describe_user_brief(user_id)}\n\n"
        f"{sub_text}\n\n"
        "اختر من الأزرار بالأسفل ما تريد القيام به 👇"
    )

    bot.send_message(
        chat_id,
        welcome_text,
        reply_markup=build_main_keyboard(is_admin=is_admin)
    )


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    """
    استقبال لقطة شاشة الدفع من العميل،
    وإرسالها للأدمن مع زرّي: تفعيل الاشتراك / رفض الطلب.
    """
    chat_id = message.chat.id
    user = message.from_user
    user_id = user.id

    session = user_sessions.get(chat_id, {})
    pending_plan = session.get("pending_plan")

    # لو لم يكن هناك طلب باقة معلّق لهذا المستخدم، نتجاهل كونها صورة عادية
    if not pending_plan:
        bot.reply_to(message, "📸 تم استلام الصورة.\n(إن كنت تريد الاشتراك بالبوت، اختر أولاً باقة من زر «📦 الاشتراكات» ثم أرسل لقطة الدفع.)")
        return

    plan_name = pending_plan["name"]
    plan_days = pending_plan["days"]

    # أخذ أعلى جودة من الصور المرسلة
    file_id = message.photo[-1].file_id

    # حفظ الطلب في قاعدة البيانات كطلب معلق
    uid = str(user_id)
    db["pending"][uid] = {
        "plan_name": plan_name,
        "plan_days": plan_days,
        "ts": datetime.utcnow().isoformat(),
    }
    save_db(db)

    # رسالة للعميل
    bot.reply_to(
        message,
        "✅ تم استلام لقطة شاشة الدفع.\n"
        "📡 سيتم مراجعة طلبك من قِبل الإدارة، وستصلك رسالة عند تفعيل الباقة أو رفض الطلب."
    )

    # إرسال الصورة للأدمن مع بيانات الطلب
    if ADMIN_ID:
        caption = (
            "💰 <b>طلب اشتراك جديد</b>\n\n"
            f"{describe_user_brief(user_id)}\n\n"
            f"📦 الباقة المطلوبة: <b>{plan_name}</b> ({plan_days} يومًا)\n"
        )
        mk = InlineKeyboardMarkup()
        mk.row(
            InlineKeyboardButton("✅ تفعيل الاشتراك", callback_data=f"payok:{user_id}"),
            InlineKeyboardButton("❌ رفض الطلب", callback_data=f"payno:{user_id}"),
        )
        try:
            bot.send_photo(
                ADMIN_ID,
                file_id,
                caption=caption,
                reply_markup=mk
            )
        except Exception as e:
            logger.error("Error sending payment photo to admin: %s", e)

    # إزالة حالة الانتظار من جلسة المستخدم، لكن نترك طلبه في db["pending"]
    session["pending_plan"] = None
    user_sessions[chat_id] = session


@bot.message_handler(func=lambda m: m.text is not None)
def handle_text(message):
    chat_id = message.chat.id
    user = message.from_user
    user_id = user.id
    text = message.text.strip()

    # معالجة أوضاع الأدمن اليدوية أولاً
    if user_id == ADMIN_ID and user_id in admin_sessions:
        adm_state = admin_sessions.get(user_id, {})
        mode = adm_state.get("mode")

        if mode == "await_manual_id_for_activate":
            # تفعيل اشتراك يدوي: استلام ID
            try:
                target_id = int(text)
            except ValueError:
                bot.reply_to(message, "⚠️ أرسل ID صحيح مكون من أرقام فقط.")
                return
            adm_state["target_user_id"] = target_id
            adm_state["mode"] = "await_manual_plan_for_activate"
            admin_sessions[user_id] = adm_state

            mk = build_subscriptions_keyboard()
            bot.reply_to(
                message,
                "📦 اختر الباقة التي تريد تفعيلها لهذا المستخدم:",
                reply_markup=mk
            )
            return

        if mode == "await_manual_id_for_cancel":
            # إلغاء اشتراك يدوي: استلام ID
            try:
                target_id = int(text)
            except ValueError:
                bot.reply_to(message, "⚠️ أرسل ID صحيح مكون من أرقام فقط.")
                return

            ok = cancel_subscription(target_id)
            if ok:
                bot.reply_to(
                    message,
                    f"✅ تم إلغاء اشتراك المستخدم:\n<code>{target_id}</code>"
                )
                try:
                    bot.send_message(
                        target_id,
                        "⛔️ تم إلغاء اشتراكك في البوت بواسطة الإدارة."
                    )
                except Exception:
                    pass
            else:
                bot.reply_to(message, "⚠️ هذا المستخدم غير موجود أو لا يملك اشتراكاً فعالاً.")
            admin_sessions.pop(user_id, None)
            return

    # أوامر سابقة مثل /start تعالج في هندلر آخر
    if text.startswith("/"):
        return

    # مفاتيح القائمة الرئيسية
    if text == "📦 الاشتراكات":
        sub_text = get_user_subscription_text(user_id)
        msg = (
            "📦 <b>باقات الاشتراك المتاحة</b>:\n\n"
            f"{sub_text}\n\n"
            "🪙 اختر الباقة المناسبة لك من الأزرار بالأسفل، "
            "ثم أرسل لقطة شاشة لإشعار الدفع عند طلب البوت لذلك."
        )
        bot.send_message(
            chat_id,
            msg,
            reply_markup=build_subscriptions_keyboard()
        )
        return

    if text == "⚙️ الإعدادات":
        info_text = (
            "⚙️ <b>الإعدادات ومعلومات حسابك</b>\n\n"
            f"{describe_user_brief(user_id)}\n\n"
            f"{get_user_subscription_text(user_id)}"
        )
        bot.send_message(chat_id, info_text)
        return

    if text == "🛠 لوحة التحكم" and user_id == ADMIN_ID:
        bot.send_message(
            chat_id,
            "🛠 <b>لوحة تحكم الأدمن</b>\n\n"
            "اختر ما تريد من الأزرار:",
            reply_markup=build_admin_panel_keyboard()
        )
        return

    if text == "✂️ قص مقطع":
        # فتح وضع القص (يتطلب اشتراك)
        if not is_user_subscriber(user_id):
            bot.send_message(
                chat_id,
                "⛔️ هذا البوت مدفوع.\n"
                "يرجى الاشتراك بإحدى الباقات من زر <b>📦 الاشتراكات</b> لاستخدام خدمة قص المقاطع."
            )
            return
        reset_session(chat_id)
        bot.send_message(
            chat_id,
            "🎬 أرسل الآن رابط فيديو يوتيوب (عادي أو بث محفوظ) لبدء عملية القص."
        )
        return

    # لو أرسل رابط يوتيوب مباشرة
    if "youtu.be" in text or "youtube.com" in text:
        if not is_user_subscriber(user_id):
            bot.send_message(
                chat_id,
                "⛔️ هذا البوت مدفوع.\n"
                "يرجى الاشتراك بإحدى الباقات من زر <b>📦 الاشتراكات</b> أولاً."
            )
            return

        url = extract_url(text)
        user_sessions[chat_id] = {
            "step": "await_start",
            "url": url,
            "pending_plan": user_sessions.get(chat_id, {}).get("pending_plan"),
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

    # إن لم تكن جلسة موجودة، نطلب منه /start أو زر قص مقطع
    session = user_sessions.get(chat_id)
    if not session:
        bot.reply_to(
            message,
            "⚠️ أرسل أولاً /start أو استخدم زر «✂️ قص مقطع» ثم أرسل رابط يوتيوب."
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
        user_sessions[chat_id] = session
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
        user_sessions[chat_id] = session
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
        user_sessions[chat_id] = session

        # الآن فحص الجودات
        bot.reply_to(message, "⏳ يتم فحص الجودات المتاحة للفيديو…")

        video_url = session["url"]
        try:
            heights = get_available_qualities(video_url)
        except Exception as e:
            logger.error("Error getting qualities from YouTube", exc_info=e)
            session["quality_height"] = 360
            session["step"] = "processing"
            user_sessions[chat_id] = session
            bot.send_message(
                chat_id,
                "❌ حدث خطأ أثناء فحص الجودات من يوتيوب.\n"
                "سيتم استخدام جودة <b>360p</b> افتراضياً."
            )
            start_cutting(chat_id)
            return

        if not heights:
            session["quality_height"] = 360
            session["step"] = "processing"
            user_sessions[chat_id] = session
            bot.send_message(
                chat_id,
                "⚠️ لم أجد جودات قياسية (144p–1080p) مع صوت.\n"
                "سيتم استخدام جودة <b>360p</b> افتراضياً."
            )
            start_cutting(chat_id)
            return

        session["available_heights"] = heights
        session["step"] = "choose_quality"
        user_sessions[chat_id] = session

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
    user_sessions[chat_id] = session

    bot.answer_callback_query(call.id, f"تم اختيار الجودة: {height}p ✅", show_alert=False)

    try:
        bot.edit_message_text(
            f"✅ تم اختيار الجودة: <b>{height}p</b>\n"
            "سيتم الآن تحميل الفيديو وقص المقطع وإرساله…",
            chat_id=chat_id,
            message_id=call.message.message_id
        )
    except Exception:
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
        input_file = download_video(url, quality_height, output_name="source")
        logger.info("Downloaded video file: %s", input_file)

        cut_file = cut_video_range(input_file, start_seconds, duration, output_file="cut_full.mp4")
        logger.info("Cut file created: %s", cut_file)

        parts = split_video_to_parts(cut_file, max_mb=MAX_TELEGRAM_MB)
        logger.info("Parts to send: %s", parts)

        total_parts = len(parts)
        if total_parts == 0:
            bot.send_message(chat_id, "❌ لم أستطع استخراج المقطع بعد القص.")
            return

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
            "تأكد أن رابط الفيديو يعمل، وأن متغير الكوكيز <b>YT_COOKIES_HEADER</b> (أو YT_COOKIES) صحيح ومحدث."
        )
    except Exception as e:
        logger.error("Unexpected error in start_cutting", exc_info=e)
        bot.send_message(
            chat_id,
            "❌ حدث خطأ غير متوقع أثناء القص أو التحميل."
        )
    finally:
        try:
            clean_files(input_file, cut_file, *parts)
        except Exception:
            pass


# ================= Callback لـ الباقات (اشتراك) =================

@bot.callback_query_handler(func=lambda call: call.data.startswith("plan_"))
def handle_plan_callback(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    # data بالشكل "plan_30_شهر"
    parts = call.data.split("_", 2)
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "⚠️ خطأ في اختيار الباقة.", show_alert=True)
        return

    try:
        days = int(parts[1])
    except ValueError:
        bot.answer_callback_query(call.id, "⚠️ خطأ في الباقة.", show_alert=True)
        return

    plan_name = parts[2]
    session = user_sessions.get(chat_id, {})
    session["pending_plan"] = {"name": plan_name, "days": days}
    user_sessions[chat_id] = session

    bot.answer_callback_query(call.id, f"تم اختيار الباقة: {plan_name} ✅", show_alert=False)

    try:
        bot.edit_message_text(
            f"📦 تم اختيار الباقة: <b>{plan_name}</b> ({days} يومًا)\n\n"
            "📸 الآن أرسل لقطة شاشة لإشعار الدفع ليتم مراجعة طلبك وتفعيل الاشتراك.",
            chat_id=chat_id,
            message_id=call.message.message_id
        )
    except Exception:
        pass


# ================= Callback لطلبات الدفع (تفعيل/رفض) =================

@bot.callback_query_handler(func=lambda call: call.data.startswith("payok:") or call.data.startswith("payno:"))
def handle_payment_decision(call):
    global db
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "هذه الأزرار مخصصة للأدمن فقط.", show_alert=True)
        return

    data = call.data
    if data.startswith("payok:"):
        # تفعيل الاشتراك مباشرة
        try:
            user_id = int(data.split(":", 1)[1])
        except ValueError:
            bot.answer_callback_query(call.id, "⚠️ بيانات غير صالحة.", show_alert=True)
            return

        uid = str(user_id)
        pending = db["pending"].get(uid)
        if not pending:
            bot.answer_callback_query(call.id, "⚠️ لا يوجد طلب معلق لهذا المستخدم.", show_alert=True)
            return

        plan_name = pending["plan_name"]
        plan_days = pending["plan_days"]

        info = activate_subscription(user_id, plan_name, plan_days)
        db["pending"].pop(uid, None)
        save_db(db)

        # تعديل رسالة الأدمن (إن أمكن)
        try:
            bot.edit_message_caption(
                caption=call.message.caption + "\n\n✅ تم تفعيل الاشتراك بنجاح.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id
            )
        except Exception:
            pass

        sub_text = get_user_subscription_text(user_id)

        # رسالة للأدمن
        bot.answer_callback_query(call.id, "✅ تم تفعيل الاشتراك.", show_alert=False)
        bot.send_message(
            ADMIN_ID,
            f"✅ تم تفعيل اشتراك المستخدم:\n{describe_user_brief(user_id)}\n\n{sub_text}"
        )

        # رسالة للمستخدم
        try:
            bot.send_message(
                user_id,
                "🎉 تم تفعيل باقتك بنجاح.\n\n" + sub_text
            )
        except Exception as e:
            logger.error("Error sending activation message to user: %s", e)

    elif data.startswith("payno:"):
        # رفض الطلب
        try:
            user_id = int(data.split(":", 1)[1])
        except ValueError:
            bot.answer_callback_query(call.id, "⚠️ بيانات غير صالحة.", show_alert=True)
            return

        uid = str(user_id)
        pending = db["pending"].pop(uid, None)
        save_db(db)

        try:
            bot.edit_message_caption(
                caption=(call.message.caption or "") + "\n\n❌ تم رفض هذا الطلب.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id
            )
        except Exception:
            pass

        bot.answer_callback_query(call.id, "❌ تم رفض الطلب.", show_alert=False)

        bot.send_message(
            ADMIN_ID,
            f"❌ تم رفض طلب الاشتراك للمستخدم:\n{describe_user_brief(user_id)}"
        )
        try:
            bot.send_message(
                user_id,
                "❌ تم إلغاء العملية بسبب عدم اكتمال الدفع أو وجود مشكلة في التحقق من الإشعار."
            )
        except Exception:
            pass


# ================= Callback لوحة التحكم والإحصائيات =================

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_"))
def handle_admin_callbacks(call):
    user_id = call.from_user.id
    if user_id != ADMIN_ID:
        bot.answer_callback_query(call.id, "هذه الأزرار مخصصة للأدمن فقط.", show_alert=True)
        return

    data = call.data

    if data == "adm_manual_activate":
        admin_sessions[user_id] = {"mode": "await_manual_id_for_activate"}
        bot.answer_callback_query(call.id, "أرسل ID المستخدم لتفعيل اشتراكه.", show_alert=False)
        bot.send_message(
            user_id,
            "🔑 أرسل الآن <b>ID</b> المستخدم الذي تريد تفعيل الاشتراك له:"
        )

    elif data == "adm_manual_cancel":
        admin_sessions[user_id] = {"mode": "await_manual_id_for_cancel"}
        bot.answer_callback_query(call.id, "أرسل ID المستخدم لإلغاء اشتراكه.", show_alert=False)
        bot.send_message(
            user_id,
            "⛔️ أرسل الآن <b>ID</b> المستخدم الذي تريد إلغاء اشتراكه:"
        )

    elif data == "adm_stats":
        bot.answer_callback_query(call.id, "إحصائيات البوت", show_alert=False)
        bot.send_message(
            user_id,
            "📊 اختر نوع الإحصائية:",
            reply_markup=build_admin_stats_keyboard()
        )

    elif data == "adm_stats_visitors":
        bot.answer_callback_query(call.id, "إجمالي الزوار", show_alert=False)
        total = db["stats"].get("total_visitors", 0)
        bot.send_message(
            user_id,
            f"👥 <b>إجمالي عدد زوار البوت:</b> {total}"
        )

    elif data == "adm_stats_subscribers":
        bot.answer_callback_query(call.id, "إجمالي المشتركين", show_alert=False)
        total = db["stats"].get("total_subscribers", 0)
        bot.send_message(
            user_id,
            f"⭐️ <b>إجمالي عدد الاشتراكات المُفعّلة (مع إعادة الاشتراك):</b> {total}"
        )

    elif data == "adm_stats_last":
        bot.answer_callback_query(call.id, "آخر المشتركين", show_alert=False)
        last_list = db["stats"].get("last_subscribers", [])
        if not last_list:
            bot.send_message(user_id, "🆕 لا يوجد مشتركين بعد.")
            return
        lines = []
        for uid in reversed(last_list):  # آخر واحد في الأسفل
            try:
                uid_int = int(uid)
            except ValueError:
                continue
            lines.append(describe_user_brief(uid_int))
        text = "🆕 <b>آخر المشتركين في البوت (بحد أقصى 20):</b>\n\n" + "\n\n".join(lines)
        bot.send_message(user_id, text)

    elif data == "adm_stats_today":
        bot.answer_callback_query(call.id, "زوار اليوم", show_alert=False)
        today = get_today_str()
        count = db["stats"]["visitors_by_date"].get(today, 0)
        bot.send_message(
            user_id,
            f"📅 <b>عدد الزوار اليوم ({today}):</b> {count}"
        )


# ================= تشغيل البوت مع معالجة أخطاء polling =================
if __name__ == "__main__":
    logger.info("🔥 Bot is running…")

    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            logger.error("Polling error from Telegram: %s", e)
            # ملاحظة: لو ظهر خطأ 409 فهذا يعني أن هناك نسخة أخرى من البوت تعمل بنفس التوكن
            # يجب إيقاف أي Instance أخرى للبوت (في Koyeb أو أي مكان آخر).
            time.sleep(5)
