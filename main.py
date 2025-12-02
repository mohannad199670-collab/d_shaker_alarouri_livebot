import os
import math
import time
import json
import logging
import subprocess
import datetime

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

# ================= إعداد التوكن و الأدمن =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN غير موجود في Environment variables")

# ID الأدمن (يمكن تغييره من متغير ADMIN_ID أو يظل الثابت هنا)
ADMIN_ID = int(os.getenv("ADMIN_ID", "604494923"))

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ================= إعداد الكوكيز =================
# متغير البيئة الذي تضع فيه هيدر الكوكيز الكامل:
# مثال: SID=...; HSID=...; SSID=...; APISID=...; SAPISID=...
YT_COOKIES_HEADER = os.getenv("YT_COOKIES_HEADER", os.getenv("YT_COOKIES", "")).strip()

# إلغاء استخدام ملف cookies.txt نهائياً
COOKIES_PATH = None

# ================= إعدادات الحجم =================
MAX_TELEGRAM_MB = 48  # الحد المستهدف لكل جزء (تقريباً 48 ميغا)

# ================= ملف قاعدة البيانات =================
DB_FILE = "database.json"

def default_db():
    return {
        "users": {},
        "visitors_today": 0,
        "last_reset_date": "",
        "new_subscribers": []
    }

def load_db():
    if not os.path.exists(DB_FILE):
        db = default_db()
        save_db(db)
        return db
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "users" not in data:
            data["users"] = {}
        if "visitors_today" not in data:
            data["visitors_today"] = 0
        if "last_reset_date" not in data:
            data["last_reset_date"] = ""
        if "new_subscribers" not in data:
            data["new_subscribers"] = []
        return data
    except Exception:
        return default_db()

def save_db(db):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Error saving DB: %s", e)

db = load_db()

def ensure_daily_reset():
    """تحديث عداد زوار اليوم عند تغير التاريخ."""
    global db
    today = datetime.date.today().isoformat()
    if db.get("last_reset_date") != today:
        db["visitors_today"] = 0
        db["last_reset_date"] = today
        save_db(db)

def register_visit(user_obj):
    """تسجيل زيارة مستخدم (للإحصائيات و الزوار اليوميين)."""
    global db
    ensure_daily_reset()
    uid = str(user_obj.id)
    today = datetime.date.today().isoformat()
    now_ts = int(time.time())

    u = db["users"].get(uid)
    if not u:
        u = {
            "id": user_obj.id,
            "first_name": user_obj.first_name or "",
            "username": user_obj.username or "",
            "is_subscribed": False,
            "plan": None,
            "expire_at": 0,
            "activated_at": 0,
            "last_seen": now_ts,
            "last_seen_date": today,
        }
        db["users"][uid] = u
        db["visitors_today"] += 1
    else:
        u["first_name"] = user_obj.first_name or u.get("first_name", "")
        u["username"] = user_obj.username or u.get("username", "")
        last_date = u.get("last_seen_date")
        if last_date != today:
            db["visitors_today"] += 1
            u["last_seen_date"] = today
        u["last_seen"] = now_ts

    save_db(db)

def get_user_record(user_id: int):
    global db
    uid = str(user_id)
    return db["users"].get(uid)

def is_user_subscribed(user_id: int) -> bool:
    """التحقق من اشتراك المستخدم (مع استثناء الأدمن)."""
    if user_id == ADMIN_ID:
        return True
    u = get_user_record(user_id)
    if not u or not u.get("is_subscribed"):
        return False
    exp = u.get("expire_at", 0)
    return exp > int(time.time())

# ================= تعريف الباقات =================
PLANS = {
    "plan_1m": {"label": "باقة شهر", "days": 30},
    "plan_3m": {"label": "باقة 3 أشهر", "days": 90},
    "plan_6m": {"label": "باقة 6 أشهر", "days": 180},
    "plan_12m": {"label": "باقة سنة", "days": 365},
}

def set_subscription(user_id: int, plan_key: str):
    """تفعيل اشتراك لمستخدم معيّن."""
    global db
    now_ts = int(time.time())
    plan = PLANS.get(plan_key)
    if not plan:
        raise ValueError("خطة غير معروفة")

    days = plan["days"]
    label = plan["label"]
    expire_at = now_ts + days * 86400

    uid = str(user_id)
    today = datetime.date.today().isoformat()

    u = db["users"].get(uid)
    if not u:
        u = {
            "id": user_id,
            "first_name": "",
            "username": "",
            "is_subscribed": True,
            "plan": label,
            "expire_at": expire_at,
            "activated_at": now_ts,
            "last_seen": now_ts,
            "last_seen_date": today,
        }
        db["users"][uid] = u
    else:
        u["is_subscribed"] = True
        u["plan"] = label
        u["expire_at"] = expire_at
        u["activated_at"] = now_ts
        if not u.get("last_seen_date"):
            u["last_seen_date"] = today
        if not u.get("last_seen"):
            u["last_seen"] = now_ts

    # تسجيله ضمن آخر المشتركين
    if "new_subscribers" not in db:
        db["new_subscribers"] = []
    db["new_subscribers"].append(user_id)
    if len(db["new_subscribers"]) > 200:
        db["new_subscribers"] = db["new_subscribers"][-200:]

    save_db(db)
    return label, expire_at, days

def cancel_subscription(user_id: int):
    """إلغاء اشتراك المستخدم."""
    global db
    uid = str(user_id)
    u = db["users"].get(uid)
    if not u:
        return False
    u["is_subscribed"] = False
    u["plan"] = None
    u["expire_at"] = 0
    save_db(db)
    return True

# ================= إدارة جلسات المستخدم =================
# لكل مستخدم نخزن حالة القص هنا
user_sessions = {}

# حالات خاصة للأدمن (تفعيل/إلغاء اشتراك)
admin_states = {}

# حالات طلب إثبات دفع من المستخدم
user_payment_states = {}

def reset_session(chat_id: int):
    """إعادة تهيئة جلسة القص للمستخدم."""
    user_sessions[chat_id] = {
        "step": "await_url"
    }

# ================= كيبوردات =================
def main_menu(chat_id: int):
    """الكيبورد الرئيسي."""
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("✂️ قص مقطع جديد"))
    markup.row(KeyboardButton("📦 الاشتراكات"))
    markup.row(KeyboardButton("⚙️ الإعدادات"))
    if chat_id == ADMIN_ID:
        markup.row(KeyboardButton("🛠 لوحة التحكم"))
    return markup

def subscriptions_keyboard(for_admin=False):
    """كيبورد باقات الاشتراك (inline)."""
    mk = InlineKeyboardMarkup()
    mk.row(
        InlineKeyboardButton("باقة شهر", callback_data=("admin_plan_1m" if for_admin else "user_plan_1m")),
        InlineKeyboardButton("باقة 3 أشهر", callback_data=("admin_plan_3m" if for_admin else "user_plan_3m")),
    )
    mk.row(
        InlineKeyboardButton("باقة 6 أشهر", callback_data=("admin_plan_6m" if for_admin else "user_plan_6m")),
        InlineKeyboardButton("باقة سنة", callback_data=("admin_plan_12m" if for_admin else "user_plan_12m")),
    )
    return mk

def admin_panel_keyboard():
    mk = InlineKeyboardMarkup()
    mk.row(InlineKeyboardButton("➕ تفعيل اشتراك", callback_data="admin_activate"))
    mk.row(InlineKeyboardButton("🚫 إلغاء اشتراك", callback_data="admin_deactivate"))
    mk.row(InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats"))
    return mk

def admin_stats_keyboard():
    mk = InlineKeyboardMarkup()
    mk.row(InlineKeyboardButton("👥 إجمالي الزوار", callback_data="admin_stats_total"))
    mk.row(InlineKeyboardButton("✅ إجمالي المشتركين", callback_data="admin_stats_subscribers"))
    mk.row(InlineKeyboardButton("🆕 آخر 20 مشترك", callback_data="admin_stats_last20"))
    mk.row(InlineKeyboardButton("📈 زوار اليوم", callback_data="admin_stats_today"))
    return mk

# ================= دوال مساعدة للوقت و الروابط =================
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

# ================= دوال الجودات و التحميل =================
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

# ================= قص و تقطيع و تحويل =================
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

def get_media_duration(input_file: str) -> float:
    """
    إرجاع مدة الفيديو/الصوت بالثواني باستخدام ffprobe.
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

def split_media_to_parts(input_file: str, max_mb: int = MAX_TELEGRAM_MB):
    """
    تقسيم ملف فيديو/صوت إلى أجزاء حسب الحجم المستهدف (تقريبياً).
    نعتمد على تقسيم المدة إلى N أجزاء (ceiling) حتى لا يضيع الجزء الأخير الصغير.
    """
    limit_bytes = max_mb * 1024 * 1024
    size_bytes = os.path.getsize(input_file)

    if size_bytes <= limit_bytes:
        return [input_file]

    duration = get_media_duration(input_file)

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

def convert_to_mp3(input_file: str, output_file: str = "cut_audio.mp3") -> str:
    """
    تحويل فيديو إلى صوت mp3.
    """
    command = [
        "ffmpeg",
        "-y",
        "-i",
        input_file,
        "-vn",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "4",
        output_file,
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output_file

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

# ================= رسائل مساعدة =================
def locked_message_text() -> str:
    return (
        "🔒 <b>الوصول مقيد</b>\n\n"
        "لا يمكنك استخدام خدمة قص المقاطع حالياً.\n"
        "للاستخدام، قم بالاشتراك أولاً من خلال زر <b>📦 الاشتراكات</b>، "
        "ثم انتظر حتى يتم تفعيل باقتك.\n\n"
        "بعد التفعيل يمكنك قص أي جزء من فيديو يوتيوب بجودة تختارها "
        "وتحميله كفيديو أو صوت فقط."
    )

def send_user_settings(chat_id: int):
    global db
    u = get_user_record(chat_id)
    if not u:
        text = (
            "⚙️ <b>الإعدادات</b>\n\n"
            "لا توجد بيانات مسجلة لك بعد.\n"
            "أرسل /start لإعادة بدء البوت."
        )
        bot.send_message(chat_id, text, reply_markup=main_menu(chat_id))
        return

    now_ts = int(time.time())
    first_name = u.get("first_name") or "غير معروف"
    username = u.get("username") or "بدون يوزر"
    uid = u.get("id", chat_id)

    if is_user_subscribed(chat_id):
        expire_at = u.get("expire_at", 0)
        plan = u.get("plan", "غير معروف")
        remaining_days = max(0, int((expire_at - now_ts) / 86400))
        end_date = datetime.datetime.fromtimestamp(expire_at).strftime("%Y-%m-%d")
        status = "✅ مفعل"
        sub_info = (
            f"📦 الباقة: <b>{plan}</b>\n"
            f"📅 ينتهي بتاريخ: <b>{end_date}</b>\n"
            f"⏳ الأيام المتبقية: <b>{remaining_days}</b> يوم"
        )
    else:
        status = "❌ لا يوجد اشتراك فعال"
        sub_info = (
            "يمكنك الاشتراك من خلال زر <b>📦 الاشتراكات</b> "
            "ثم إرسال لقطة شاشة لإثبات الدفع."
        )

    profile_link = f"https://t.me/{username}" if username != "بدون يوزر" else "لا يوجد رابط"

    text = (
        "⚙️ <b>إعدادات حسابك</b>\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"👤 الاسم: {first_name}\n"
        f"🪪 اليوزر: @{username}\n"
        f"🔗 الرابط: {profile_link}\n\n"
        f"📌 حالة الاشتراك: {status}\n"
        f"{sub_info}"
    )
    bot.send_message(chat_id, text, reply_markup=main_menu(chat_id))

def send_subscriptions_menu(chat_id: int):
    text = (
        "📦 <b>باقات الاشتراك المتاحة</b>\n\n"
        "🔹 باقة شهر واحد\n"
        "🔹 باقة 3 أشهر\n"
        "🔹 باقة 6 أشهر\n"
        "🔹 باقة سنة كاملة\n\n"
        "اختر الباقة المناسبة لك من الأزرار بالأسفل، ثم أرسل لقطة شاشة "
        "لإشعار الدفع ليتم تفعيل اشتراكك.\n\n"
        "بعد التفعيل يمكنك قص مقاطع يوتيوب بجودة عالية وتحميلها كفيديو أو صوت فقط."
    )
    bot.send_message(chat_id, text, reply_markup=subscriptions_keyboard(for_admin=False))

def send_admin_panel(chat_id: int):
    bot.send_message(
        chat_id,
        "🛠 <b>لوحة التحكم</b>\n\n"
        "اختر من الأزرار بالأسفل:",
        reply_markup=admin_panel_keyboard()
    )

def send_admin_stats(chat_id: int):
    bot.send_message(
        chat_id,
        "📊 <b>قسم الإحصائيات</b>\n\n"
        "اختر نوع الإحصائية التي تريد عرضها:",
        reply_markup=admin_stats_keyboard()
    )

# ================= منطق البوت: /start =================
@bot.message_handler(commands=["start"])
def handle_start_cmd(message):
    chat_id = message.chat.id
    user = message.from_user

    # تسجيل الزيارة في قاعدة البيانات
    register_visit(user)

    # إشعار الأدمن بدخول شخص جديد إلى البوت
    try:
        user_id = user.id
        first_name = user.first_name or ""
        username = user.username or "بدون يوزر"
        profile_link = f"https://t.me/{username}" if username != "بدون يوزر" else "لا يوجد رابط"

        bot.send_message(
            ADMIN_ID,
            f"📥 <b>شخص دخل البوت الآن</b>\n\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"👤 الاسم: {first_name}\n"
            f"🪪 اليوزر: @{username}\n"
            f"🔗 الرابط: {profile_link}"
        )
    except Exception:
        pass

    # بدء جلسة القص للمستخدم
    reset_session(chat_id)

    # رسالة ترحيبية أنيقة
    welcome_text = (
        "👋 أهلاً بك في بوت <b>قص مقاطع يوتيوب</b>.\n\n"
        "يسمح لك البوت بقص أي جزء من فيديو يوتيوب بدقة عالية، مع اختيار الجودة، "
        "والتحميل كـ <b>فيديو</b> أو <b>صوت فقط (MP3)</b>.\n\n"
        "🔐 لاستخدام خدمة القص، يجب أن يكون لديك <b>اشتراك مفعل</b>.\n"
        "استخدم زر <b>📦 الاشتراكات</b> للاطلاع على الباقات وطريقة الدفع.\n\n"
        "ℹ️ ملاحظة: إذا تجاوز حجم المقطع الناتج <b>48 ميغابايت</b> فسيتم تقسيمه "
        "تلقائيًا إلى عدة أجزاء وإرسالها لك بالترتيب."
    )

    bot.send_message(
        chat_id,
        welcome_text,
        reply_markup=main_menu(chat_id)
    )

# ================= منطق البوت: النصوص =================
@bot.message_handler(func=lambda m: m.text is not None)
def handle_text(message):
    chat_id = message.chat.id
    text = message.text.strip()

    # تجاهل الأوامر الأخرى (غير /start الذي له هاندلر خاص)
    if text.startswith("/"):
        return

    # أولاً: معالجة حالات الأدمن الخاصة (تفعيل/إلغاء اشتراك)
    if chat_id == ADMIN_ID and chat_id in admin_states:
        state = admin_states.get(chat_id, {})
        mode = state.get("mode")

        # إدخال ID المستخدم لتفعيل اشتراك
        if mode == "activate_wait_user_id":
            plan_key = state.get("plan_key")
            try:
                target_id = int(text)
            except ValueError:
                bot.send_message(chat_id, "⚠️ يرجى إرسال رقم ID صحيح (أرقام فقط).")
                return

            label, expire_at, days = set_subscription(target_id, plan_key)
            end_date = datetime.datetime.fromtimestamp(expire_at).strftime("%Y-%m-%d")

            # رسالة للأدمن
            bot.send_message(
                chat_id,
                f"✅ تم تفعيل اشتراك المستخدم <code>{target_id}</code>\n"
                f"الباقة: <b>{label}</b>\n"
                f"ينتهي بتاريخ: <b>{end_date}</b> ({days} يومًا)."
            )

            # محاولة إرسال رسالة للمستخدم
            try:
                bot.send_message(
                    target_id,
                    f"✅ تم تفعيل باقتك بنجاح.\n\n"
                    f"📦 الباقة: <b>{label}</b>\n"
                    f"📅 ينتهي اشتراكك بتاريخ: <b>{end_date}</b>.\n"
                    f"يمكنك الآن استخدام البوت لقص مقاطع يوتيوب بكل حرية 🎯."
                )
            except Exception:
                bot.send_message(
                    chat_id,
                    "ℹ️ تعذر إرسال رسالة للمستخدم (قد لا يكون قد بدأ البوت)."
                )

            admin_states.pop(chat_id, None)
            return

        # إدخال ID المستخدم لإلغاء الاشتراك
        if mode == "deactivate_wait_user_id":
            try:
                target_id = int(text)
            except ValueError:
                bot.send_message(chat_id, "⚠️ يرجى إرسال رقم ID صحيح (أرقام فقط).")
                return

            ok = cancel_subscription(target_id)
            if ok:
                bot.send_message(
                    chat_id,
                    f"✅ تم إلغاء اشتراك المستخدم <code>{target_id}</code>."
                )
                try:
                    bot.send_message(
                        target_id,
                        "⚠️ تم إلغاء اشتراكك في البوت.\n"
                        "يمكنك إعادة الاشتراك في أي وقت من خلال زر <b>📦 الاشتراكات</b>."
                    )
                except Exception:
                    pass
            else:
                bot.send_message(chat_id, "⚠️ لا يوجد اشتراك مسجل لهذا المستخدم.")

            admin_states.pop(chat_id, None)
            return

    # ثانياً: أزرار القائمة الرئيسية
    if text in ["📦 الاشتراكات", "الاشتراكات"]:
        send_subscriptions_menu(chat_id)
        return

    if text in ["⚙️ الإعدادات", "الاعدادات", "الإعدادات"]:
        send_user_settings(chat_id)
        return

    if text in ["🛠 لوحة التحكم", "لوحة التحكم"] and chat_id == ADMIN_ID:
        send_admin_panel(chat_id)
        return

    if text in ["✂️ قص مقطع جديد", "قص مقطع جديد"]:
        # تحقق من الاشتراك
        if not is_user_subscribed(chat_id):
            bot.send_message(chat_id, locked_message_text(), reply_markup=main_menu(chat_id))
            return
        reset_session(chat_id)
        bot.send_message(
            chat_id,
            "📹 أرسل الآن رابط فيديو يوتيوب الذي تريد قصه.",
            reply_markup=main_menu(chat_id),
        )
        return

    # ثالثاً: لو المستخدم في حالة انتظار إثبات دفع (إرسال لقطة شاشة فالمفترض في photo handler)
    # النص هنا لا يهم في هذه الحالة، لذا لا شيء خاص

    # رابعاً: منطق القص (الاشتراك شرط أساسي)
    session = user_sessions.get(chat_id)

    # لو أرسل رابط يوتيوب في أي لحظة
    if "youtu.be" in text or "youtube.com" in text:
        if not is_user_subscribed(chat_id):
            bot.send_message(chat_id, locked_message_text(), reply_markup=main_menu(chat_id))
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

    # إن لم تكن جلسة موجودة
    if not session:
        if not is_user_subscribed(chat_id):
            bot.send_message(chat_id, locked_message_text(), reply_markup=main_menu(chat_id))
        else:
            bot.send_message(
                chat_id,
                "📹 لبدء القص أرسل رابط فيديو يوتيوب، أو اضغط على زر <b>✂️ قص مقطع جديد</b>.",
                reply_markup=main_menu(chat_id)
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
            session["step"] = "choose_type"  # سنسأل عن نوع الملف بعد قليل
            bot.send_message(
                chat_id,
                "❌ حدث خطأ أثناء فحص الجودات من يوتيوب.\n"
                "سيتم استخدام جودة <b>360p</b> افتراضياً.\n\n"
                "الآن اختر نوع الملف: فيديو أم صوت فقط.",
            )
            send_type_choice(chat_id)
            return

        if not heights:
            # نفس الشيء: لو ما وجد أي جودة "مع صوت"
            session["quality_height"] = 360
            session["step"] = "choose_type"
            bot.send_message(
                chat_id,
                "⚠️ لم أجد جودات قياسية (144p–1080p) مع صوت.\n"
                "سيتم استخدام جودة <b>360p</b> افتراضياً.\n\n"
                "الآن اختر نوع الملف: فيديو أم صوت فقط.",
            )
            send_type_choice(chat_id)
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

    elif step in ("choose_quality", "choose_type", "processing"):
        bot.reply_to(
            message,
            "⌛ يتم حالياً تجهيز المقطع أو اختيار الإعدادات.\n"
            "انتظر حتى ينتهي أو أرسل رابط يوتيوب جديد لبدء عملية جديدة."
        )

# ================= استقبال صور (إثبات الدفع) =================
@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    chat_id = message.chat.id

    state = user_payment_states.get(chat_id)
    if not state or state.get("mode") != "await_payment":
        # صورة عادية لا علاقة لها بالدفع
        return

    plan_key = state.get("plan_key")
    plan = PLANS.get(plan_key, {})
    label = plan.get("label", "باقة غير معروفة")

    user = message.from_user
    user_id = user.id
    first_name = user.first_name or ""
    username = user.username or "بدون يوزر"
    profile_link = f"https://t.me/{username}" if username != "بدون يوزر" else "لا يوجد رابط"

    # تحويل الصورة إلى الأدمن (فوروارد)
    try:
        bot.forward_message(ADMIN_ID, chat_id, message.message_id)
        bot.send_message(
            ADMIN_ID,
            "📥 <b>إشعار دفع جديد</b>\n\n"
            f"🧾 الباقة المطلوبة: <b>{label}</b>\n\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"👤 الاسم: {first_name}\n"
            f"🪪 اليوزر: @{username}\n"
            f"🔗 الرابط: {profile_link}\n\n"
            "✅ تمت إعادة توجيه لقطة الشاشة، يمكنك الآن تفعيل الاشتراك من لوحة التحكم."
        )
    except Exception as e:
        logger.error("Error forwarding payment proof: %s", e)

    bot.send_message(
        chat_id,
        "✅ تم استلام لقطة شاشة الدفع.\n"
        "سيتم مراجعتها وتفعيل اشتراكك قريباً بإذن الله."
    )

    user_payment_states.pop(chat_id, None)

# ================= كول باك: اختيار الجودة =================
def send_type_choice(chat_id: int):
    mk = InlineKeyboardMarkup()
    mk.row(
        InlineKeyboardButton("🎬 فيديو", callback_data="t_video"),
        InlineKeyboardButton("🎧 صوت فقط (MP3)", callback_data="t_audio")
    )
    bot.send_message(
        chat_id,
        "🎛 <b>اختر نوع الملف المطلوب:</b>",
        reply_markup=mk
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
    session["step"] = "choose_type"

    bot.answer_callback_query(call.id, f"تم اختيار الجودة: {height}p ✅", show_alert=False)

    try:
        bot.edit_message_text(
            f"✅ تم اختيار الجودة: <b>{height}p</b>\n"
            "الآن اختر نوع الملف: فيديو أم صوت فقط.",
            chat_id=chat_id,
            message_id=call.message.message_id
        )
    except Exception:
        pass

    send_type_choice(chat_id)

@bot.callback_query_handler(func=lambda call: call.data in ["t_video", "t_audio"])
def handle_type_callback(call):
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id)

    if not session:
        bot.answer_callback_query(call.id, "انتهت الجلسة، أرسل رابطاً جديداً.", show_alert=True)
        return

    if call.data == "t_video":
        session["output_type"] = "video"
        label = "فيديو"
    else:
        session["output_type"] = "audio"
        label = "صوت فقط (MP3)"

    session["step"] = "processing"

    bot.answer_callback_query(call.id, f"تم اختيار النوع: {label} ✅", show_alert=False)

    try:
        h = session.get("quality_height", "أفضل جودة متاحة")
        h_text = f"{h}p" if isinstance(h, int) else h
        bot.edit_message_text(
            f"✅ تم اختيار الجودة: <b>{h_text}</b>\n"
            f"✅ تم اختيار النوع: <b>{label}</b>\n\n"
            "سيتم الآن تحميل الفيديو وقص المقطع وتجهيزه لك…",
            chat_id=chat_id,
            message_id=call.message.message_id
        )
    except Exception:
        pass

    start_cutting(chat_id)

# ================= كول باك: باقات المستخدم (اختيار لإرسال الدفع) =================
@bot.callback_query_handler(func=lambda call: call.data.startswith("user_plan_"))
def handle_user_plan_callback(call):
    chat_id = call.message.chat.id
    plan_key = call.data.replace("user_plan_", "plan_")
    plan = PLANS.get(plan_key)

    if not plan:
        bot.answer_callback_query(call.id, "⚠️ خطة غير معروفة.", show_alert=True)
        return

    label = plan["label"]
    bot.answer_callback_query(call.id, f"تم اختيار {label}", show_alert=False)

    user_payment_states[chat_id] = {
        "mode": "await_payment",
        "plan_key": plan_key,
    }

    bot.send_message(
        chat_id,
        f"🧾 اخترت: <b>{label}</b>.\n\n"
        "💳 الرجاء إرسال لقطة شاشة لإشعار الدفع الآن.\n"
        "سيتم مراجعتها من قبل الإدارة ثم تفعيل اشتراكك بإذن الله."
    )

# ================= كول باك: لوحة التحكم و الإحصائيات و تفعيل/إلغاء =================
@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_"))
def handle_admin_callbacks(call):
    chat_id = call.message.chat.id
    if chat_id != ADMIN_ID:
        bot.answer_callback_query(call.id, "غير مصرح لك بالدخول إلى لوحة التحكم.", show_alert=True)
        return

    data = call.data

    if data == "admin_activate":
        bot.answer_callback_query(call.id)
        bot.send_message(
            chat_id,
            "➕ اختر الباقة التي تريد تفعيلها للمستخدم:",
            reply_markup=subscriptions_keyboard(for_admin=True)
        )
        return

    if data == "admin_deactivate":
        bot.answer_callback_query(call.id)
        admin_states[chat_id] = {"mode": "deactivate_wait_user_id"}
        bot.send_message(
            chat_id,
            "🚫 أرسل الآن <b>ID</b> المستخدم الذي تريد إلغاء اشتراكه."
        )
        return

    if data == "admin_stats":
        bot.answer_callback_query(call.id)
        send_admin_stats(chat_id)
        return

    if data.startswith("admin_plan_"):
        bot.answer_callback_query(call.id)
        # اختيار خطة لتفعيلها لمستخدم
        plan_suffix = data.replace("admin_plan_", "")
        plan_key = f"plan_{plan_suffix}"
        plan = PLANS.get(plan_key)
        if not plan:
            bot.send_message(chat_id, "⚠️ خطة غير معروفة.")
            return

        label = plan["label"]
        admin_states[chat_id] = {
            "mode": "activate_wait_user_id",
            "plan_key": plan_key,
        }
        bot.send_message(
            chat_id,
            f"✅ اخترت: <b>{label}</b>.\n\n"
            "الآن أرسل <b>ID</b> المستخدم الذي تريد تفعيل الاشتراك له."
        )
        return

    # إحصائيات
    global db
    ensure_daily_reset()
    users = db.get("users", {})

    if data == "admin_stats_total":
        bot.answer_callback_query(call.id)
        total_visitors = len(users)
        bot.send_message(
            chat_id,
            f"👥 <b>إجمالي الزوار:</b> {total_visitors}"
        )
        return

    if data == "admin_stats_subscribers":
        bot.answer_callback_query(call.id)
        now_ts = int(time.time())
        total_subs = 0
        for u in users.values():
            if u.get("is_subscribed") and u.get("expire_at", 0) > now_ts:
                total_subs += 1
        bot.send_message(
            chat_id,
            f"✅ <b>إجمالي المشتركين الحاليين:</b> {total_subs}"
        )
        return

    if data == "admin_stats_last20":
        bot.answer_callback_query(call.id)
        new_list = db.get("new_subscribers", [])
        if not new_list:
            bot.send_message(chat_id, "🆕 لا توجد سجلات مشتركين حتى الآن.")
            return
        last_20 = new_list[-20:]
        lines = []
        for uid in reversed(last_20):
            u = users.get(str(uid), {})
            name = u.get("first_name") or "غير معروف"
            username = u.get("username") or "بدون يوزر"
            lines.append(f"• <code>{uid}</code> — {name} (@{username})")

        text = "🆕 <b>آخر 20 مشترك:</b>\n\n" + "\n".join(lines)
        bot.send_message(chat_id, text)
        return

    if data == "admin_stats_today":
        bot.answer_callback_query(call.id)
        visitors_today = db.get("visitors_today", 0)
        bot.send_message(
            chat_id,
            f"📈 <b>زوار اليوم:</b> {visitors_today}"
        )
        return

# ================= تنفيذ القص و الإرسال =================
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
    output_type = session.get("output_type", "video")  # video / audio

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

        # قص المقطع المطلوب من الفيديو الأصلي
        cut_file = cut_video_range(input_file, start_seconds, duration, output_file="cut_full.mp4")
        logger.info("Cut file created: %s", cut_file)

        if output_type == "audio":
            # تحويل إلى MP3
            audio_file = convert_to_mp3(cut_file, output_file="cut_audio.mp3")
            logger.info("Audio file created: %s", audio_file)

            # تقسيم الصوت إلى أجزاء إن لزم
            parts = split_media_to_parts(audio_file, max_mb=MAX_TELEGRAM_MB)
            total_parts = len(parts)
            if total_parts == 0:
                bot.send_message(chat_id, "❌ لم أستطع استخراج المقطع الصوتي.")
                return

            for idx, part in enumerate(parts, start=1):
                bot.send_message(
                    chat_id,
                    f"📤 جاري إرسال الجزء {idx}/{total_parts} (صوت)…"
                )
                with open(part, "rb") as f:
                    try:
                        bot.send_audio(
                            chat_id,
                            f,
                            caption=f"🎧 الجزء {idx}/{total_parts}",
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

        else:
            # فيديو
            parts = split_media_to_parts(cut_file, max_mb=MAX_TELEGRAM_MB)
            logger.info("Parts to send: %s", parts)

            total_parts = len(parts)
            if total_parts == 0:
                bot.send_message(chat_id, "❌ لم أستطع استخراج المقطع بعد القص.")
                return

            for idx, part in enumerate(parts, start=1):
                bot.send_message(
                    chat_id,
                    f"📤 جاري إرسال الجزء {idx}/{total_parts} (فيديو)…"
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
            "يمكنك الآن إرسال رابط يوتيوب جديد لقص مقطع آخر 🎯.",
            reply_markup=main_menu(chat_id)
        )
        reset_session(chat_id)

    except DownloadError as e:
        logger.error("DownloadError from YouTube", exc_info=e)
        bot.send_message(
            chat_id,
            "❌ حدث خطأ أثناء تحميل الفيديو من يوتيوب.\n"
            "تأكد أن رابط الفيديو يعمل، وأن متغير الكوكيز "
            "<b>YT_COOKIES_HEADER</b> (أو YT_COOKIES) صحيح ومحدث."
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
