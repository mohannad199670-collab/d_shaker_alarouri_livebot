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

# ================= إعداد التوكن =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN غير موجود في Environment variables")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ================= إعداد الكوكيز =================
# متغير البيئة الذي تضع فيه هيدر الكوكيز الكامل:
# مثال: SID=...; HSID=...; SSID=...; APISID=...; SAPISID=...
YT_COOKIES_HEADER = os.getenv("YT_COOKIES_HEADER", os.getenv("YT_COOKIES", "")).strip()

# إلغاء استخدام ملف cookies.txt نهائياً
COOKIES_PATH = None

# ================= إعدادات الحجم =================
MAX_TELEGRAM_MB = 48  # الحد المستهدف لكل جزء (تقريباً 48 ميغا)

# ================= إعداد الأدمـن والملفات =================
ADMIN_ID = 604494923  # ضع هنا ID حسابك أنت
SUBSCRIPTIONS_FILE = "subscriptions.json"
STATS_FILE = "stats.json"

# بنية الباقات
PLANS = {
    "month": {"name": "شهر واحد", "days": 30},
    "3months": {"name": "3 شهور", "days": 90},
    "6months": {"name": "6 شهور", "days": 180},
    "year": {"name": "سنة كاملة", "days": 365},
}


# ================= دوال مساعدة JSON / اشتراكات / إحصائيات =================

def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Error saving JSON file %s: %s", path, e)


def get_subscriptions():
    return load_json(SUBSCRIPTIONS_FILE, {})


def save_subscriptions(data):
    save_json(SUBSCRIPTIONS_FILE, data)


def get_stats():
    default = {"visitors": {}, "activations": []}
    return load_json(STATS_FILE, default)


def save_stats(data):
    save_json(STATS_FILE, data)


def is_admin(chat_id: int) -> bool:
    return chat_id == ADMIN_ID


def record_visitor(user):
    """
    حفظ الزائر في stats.json (مرة واحدة فقط لكل مستخدم)
    """
    stats = get_stats()
    visitors = stats.get("visitors", {})

    uid_str = str(user.id)
    if uid_str not in visitors:
        visitors[uid_str] = {
            "first_name": user.first_name or "",
            "username": user.username or "",
            "first_seen": date.today().isoformat(),
        }
        stats["visitors"] = visitors
        save_stats(stats)


def get_total_visitors():
    stats = get_stats()
    return len(stats.get("visitors", {}))


def get_today_visitors():
    stats = get_stats()
    visitors = stats.get("visitors", {})
    today_str = date.today().isoformat()
    return sum(1 for v in visitors.values() if v.get("first_seen") == today_str)


def log_activation(user_id: int, plan_key: str):
    stats = get_stats()
    activations = stats.get("activations", [])
    activations.append(
        {
            "user_id": user_id,
            "plan": plan_key,
            "activated_at": datetime.utcnow().isoformat(),
        }
    )
    stats["activations"] = activations[-200:]  # الاحتفاظ بآخر 200 فقط
    save_stats(stats)


def get_last_20_subscribers():
    stats = get_stats()
    activations = stats.get("activations", [])
    return activations[-20:][::-1]  # آخر 20 من الأحدث للأقدم


def is_user_subscribed(chat_id: int) -> tuple[bool, dict | None]:
    """
    يرجع (is_active, info_dict or None)
    info_dict = {"plan": "..", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
    """
    if is_admin(chat_id):
        # الأدمـن دائماً يعتبر مشترك
        return True, {
            "plan": "admin",
            "start": "",
            "end": "",
        }

    subs = get_subscriptions()
    info = subs.get(str(chat_id))
    if not info:
        return False, None

    try:
        end_date = datetime.strptime(info["end"], "%Y-%m-%d").date()
    except Exception:
        return False, info

    today = date.today()
    if end_date < today:
        return False, info

    return True, info


def activate_subscription(user_id: int, plan_key: str) -> dict:
    """
    تفعيل اشتراك لمستخدم معيّن حسب الباقة.
    يرجع dict فيه plan/start/end
    """
    if plan_key not in PLANS:
        raise ValueError("خطة غير معروفة")

    subs = get_subscriptions()
    today = date.today()
    days = PLANS[plan_key]["days"]
    end = today + timedelta(days=days)

    info = {
        "plan": plan_key,
        "start": today.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
    }

    subs[str(user_id)] = info
    save_subscriptions(subs)
    log_activation(user_id, plan_key)

    return info


def deactivate_subscription(user_id: int) -> bool:
    subs = get_subscriptions()
    uid_str = str(user_id)
    if uid_str in subs:
        subs.pop(uid_str)
        save_subscriptions(subs)
        return True
    return False


def human_plan_name(plan_key: str) -> str:
    if plan_key == "admin":
        return "حساب إداري"
    if plan_key in PLANS:
        return PLANS[plan_key]["name"]
    return "غير معروف"


def calc_remaining_days(info: dict | None) -> int | None:
    if not info:
        return None
    try:
        end_date = datetime.strptime(info["end"], "%Y-%m-%d").date()
    except Exception:
        return None
    today = date.today()
    return max((end_date - today).days, 0)


# ================= إدارة جلسات المستخدم =================
# لكل مستخدم نخزن الحالة هنا
# مثال:
# {
#   chat_id: {
#       "step": "await_url" / "await_start" / "await_end" / "choose_quality" / "processing"
#       "url": "...",
#       "start": 10,
#       "end": 120,
#       "duration": 110,
#       "quality_height": 360,
#       "available_heights": [...],
#       -- للأدمـن:
#       "step": "admin_wait_id_activation" / "admin_wait_id_deactivation",
#       "selected_plan": "month"
#   }
# }
user_sessions = {}


def reset_session(chat_id: int):
    """إعادة تهيئة جلسة المستخدم."""
    user_sessions[chat_id] = {
        "step": "await_url"
    }


# ================= دوال مساعدة للقص =================

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
    إرجاع قائمة الجودات المتاحة (ارتفاع) للفيديو.
    إذا حصل خطأ نرمي استثناء ونتعامل معه خارج الدالة.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "geo_bypass": True,
    }

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

        # نضيف الارتفاع كخيار متاح (حتى لو فيديو فقط، التحميل سيضم صوتاً مع bestaudio)
        available.add(height)

    return sorted(list(available))


def build_format_string_for_height(height: int | None) -> str:
    """
    صيغة الفورمات لـ yt-dlp بحيث يختار فيديو+صوت
    مع fallback في حال عدم توفر نفس الارتفاع بالضبط.
    """
    if height is None:
        return "bv*+ba/best"

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
    تقسيم ملف (فيديو/صوت) إلى أجزاء حسب الحجم المستهدف تقريبياً.
    نعتمد على تقسيم المدة إلى N أجزاء (ceiling).
    """
    limit_bytes = max_mb * 1024 * 1024
    size_bytes = os.path.getsize(input_file)

    if size_bytes <= limit_bytes:
        return [input_file]

    duration = get_media_duration(input_file)

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


# ================= الكيبوردات =================

def make_main_keyboard(chat_id: int) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("✂️ قص مقطع يوتيوب", "📦 الاشتراكات")
    kb.row("⚙️ الإعدادات")
    if is_admin(chat_id):
        kb.row("🛠 لوحة التحكم")
    return kb


def make_admin_panel_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ تفعيل اشتراك", callback_data="admin_activate"),
        InlineKeyboardButton("⛔ إلغاء اشتراك", callback_data="admin_deactivate"),
    )
    kb.row(
        InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")
    )
    return kb


def make_plans_keyboard(prefix: str) -> InlineKeyboardMarkup:
    """
    prefix مثل: 'plan_req' أو 'admin_plan'
    """
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("شهر واحد", callback_data=f"{prefix}_month"),
        InlineKeyboardButton("3 شهور", callback_data=f"{prefix}_3months"),
    )
    kb.row(
        InlineKeyboardButton("6 شهور", callback_data=f"{prefix}_6months"),
        InlineKeyboardButton("سنة كاملة", callback_data=f"{prefix}_year"),
    )
    return kb


# ================= منطق البوت =================

@bot.message_handler(commands=["start"])
def handle_start_cmd(message):
    chat_id = message.chat.id
    user = message.from_user

    # تسجيل الزائر
    record_visitor(user)

    # بدء الجلسة
    reset_session(chat_id)

    # رسالة ترحيبية
    welcome_text = (
        "👋 أهلاً بك في بوت <b>قص مقاطع يوتيوب الاحترافي</b>.\n\n"
        "هذا البوت يتيح لك قص أي جزء من فيديوهات يوتيوب وحفظه على شكل مقطع جاهز.\n\n"
        "🔐 لاستخدام كامل مزايا البوت، يلزم الاشتراك في إحدى الباقات المتاحة من زر <b>📦 الاشتراكات</b>.\n\n"
        "ℹ️ ملاحظة: إذا تجاوز حجم المقطع الناتج <b>48 ميغابايت</b> "
        "سيتم تقسيمه تلقائياً إلى عدة أجزاء وإرسالها لك كفيديوهات متتالية. 🎞️"
    )

    bot.send_message(chat_id, welcome_text, reply_markup=make_main_keyboard(chat_id))

    # معلومات المستخدم + حالة الاشتراك
    is_sub, info = is_user_subscribed(chat_id)
    if is_sub:
        plan_name = human_plan_name(info["plan"])
        remaining = calc_remaining_days(info)
        end_str = info["end"] if info.get("end") else "غير محدد"
        sub_text = (
            "✅ <b>حالة الاشتراك:</b> مفعّل\n"
            f"📦 الباقة: {plan_name}\n"
            f"⏳ ينتهي بتاريخ: <code>{end_str}</code>\n"
        )
        if remaining is not None:
            sub_text += f"🗓 الأيام المتبقية: <b>{remaining}</b>\n"
    else:
        sub_text = (
            "❌ <b>حالة الاشتراك:</b> غير مفعّل\n"
            "للاشتراك اضغط على زر <b>📦 الاشتراكات</b> واختر الباقة المناسبة.\n"
        )

    user_info_text = (
        "👤 <b>بيانات حسابك:</b>\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"👨‍💻 الاسم: {user.first_name or 'بدون اسم'}\n"
        f"🪪 اليوزر: @{user.username} " if user.username else "🪪 اليوزر: بدون يوزر\n"
    )

    bot.send_message(chat_id, user_info_text + "\n" + sub_text)


@bot.message_handler(func=lambda m: m.text is not None)
def handle_text(message):
    chat_id = message.chat.id
    text = message.text.strip()

    # أوامر التليجرام الأخرى تُهمل هنا (ما عدا /start في handler آخر)
    if text.startswith("/"):
        return

    # تأمين جلسة
    session = user_sessions.get(chat_id)
    if not session:
        reset_session(chat_id)
        session = user_sessions[chat_id]

    step = session.get("step", "await_url")

    # ================= منطق لوحة التحكم للأدمـن =================
    if is_admin(chat_id) and step == "admin_wait_id_activation":
        # هذا الإدخال هو ID المستخدم للتفعيل
        try:
            target_id = int(text)
        except ValueError:
            bot.reply_to(message, "⚠️ رجاءً أرسل رقم ID صحيح (أرقام فقط).")
            return

        plan_key = session.get("selected_plan")
        if not plan_key:
            bot.reply_to(message, "⚠️ لم يتم اختيار الباقة. اختر الباقة أولاً من لوحة التحكم.")
            return

        try:
            info = activate_subscription(target_id, plan_key)
        except Exception as e:
            logger.error("Error activating subscription: %s", e)
            bot.reply_to(message, "❌ حدث خطأ أثناء تفعيل الاشتراك.")
            session["step"] = "await_url"
            session.pop("selected_plan", None)
            return

        plan_name = human_plan_name(info["plan"])
        end_str = info["end"]
        remaining = calc_remaining_days(info) or 0

        bot.reply_to(
            message,
            "✅ تم تفعيل الاشتراك بنجاح.\n\n"
            f"🆔 المستخدم: <code>{target_id}</code>\n"
            f"📦 الباقة: {plan_name}\n"
            f"⏳ ينتهي بتاريخ: <code>{end_str}</code>\n"
            f"🗓 المدة المتبقية: <b>{remaining}</b> يومًا"
        )

        # محاولة إبلاغ المستخدم إن كان بدأ البوت
        try:
            bot.send_message(
                target_id,
                "🎉 تم تفعيل اشتراكك بنجاح!\n\n"
                f"📦 الباقة: {plan_name}\n"
                f"⏳ ينتهي بتاريخ: <code>{end_str}</code>\n"
                f"🗓 المدة المتبقية: <b>{remaining}</b> يومًا\n\n"
                "استمتع باستخدام خدمات البوت ❤️",
                parse_mode="HTML",
            )
        except Exception:
            # يمكن أن يفشل لو المستخدم لم يبدأ البوت بعد
            pass

        session["step"] = "await_url"
        session.pop("selected_plan", None)
        return

    if is_admin(chat_id) and step == "admin_wait_id_deactivation":
        try:
            target_id = int(text)
        except ValueError:
            bot.reply_to(message, "⚠️ رجاءً أرسل رقم ID صحيح (أرقام فقط).")
            return

        ok = deactivate_subscription(target_id)
        if ok:
            bot.reply_to(
                message,
                "✅ تم إلغاء الاشتراك لهذا المستخدم:\n"
                f"🆔 <code>{target_id}</code>"
            )
            try:
                bot.send_message(
                    target_id,
                    "⚠️ تم إلغاء اشتراكك في البوت.\n"
                    "إذا رغبت في العودة لاستخدام الخدمات، يمكنك الاشتراك مجددًا من خلال الباقات المتاحة."
                )
            except Exception:
                pass
        else:
            bot.reply_to(
                message,
                "ℹ️ لا يوجد اشتراك مسجل لهذا المستخدم."
            )

        session["step"] = "await_url"
        return

    # ================= أزرار الواجهة الرئيسية =================

    # زر الإعدادات
    if text == "⚙️ الإعدادات":
        user = message.from_user
        is_sub, info = is_user_subscribed(chat_id)
        if is_sub:
            plan_name = human_plan_name(info["plan"])
            remaining = calc_remaining_days(info)
            end_str = info["end"] if info.get("end") else "غير محدد"
            sub_text = (
                "✅ <b>حالة الاشتراك:</b> مفعّل\n"
                f"📦 الباقة: {plan_name}\n"
                f"⏳ ينتهي بتاريخ: <code>{end_str}</code>\n"
            )
            if remaining is not None:
                sub_text += f"🗓 الأيام المتبقية: <b>{remaining}</b>\n"
        else:
            sub_text = (
                "❌ <b>حالة الاشتراك:</b> غير مفعّل\n"
                "للاشتراك اضغط على زر <b>📦 الاشتراكات</b> واختر الباقة المناسبة.\n"
            )

        user_info_text = (
            "👤 <b>بيانات حسابك:</b>\n"
            f"🆔 ID: <code>{user.id}</code>\n"
            f"👨‍💻 الاسم: {user.first_name or 'بدون اسم'}\n"
            f"🪪 اليوزر: @{user.username} " if user.username else "🪪 اليوزر: بدون يوزر\n"
        )

        bot.reply_to(
            message,
            user_info_text + "\n" + sub_text,
            reply_markup=make_main_keyboard(chat_id)
        )
        return

    # زر الاشتراكات (للعملاء)
    if text == "📦 الاشتراكات":
        msg = (
            "📦 <b>باقات الاشتراك المتاحة:</b>\n\n"
            "1️⃣ شهر واحد\n"
            "2️⃣ 3 شهور\n"
            "3️⃣ 6 شهور\n"
            "4️⃣ سنة كاملة\n\n"
            "💳 بعد اختيار الباقة، سيطلب منك البوت إرسال لقطة شاشة لإثبات الدفع، "
            "وسيقوم الأدمن بتفعيل اشتراكك يدويًا.\n"
        )
        bot.reply_to(
            message,
            msg,
            reply_markup=make_main_keyboard(chat_id)
        )
        bot.send_message(
            chat_id,
            "⬇️ اختر الباقة التي ترغب بها:",
            reply_markup=make_plans_keyboard("plan_req")
        )
        return

    # زر لوحة التحكم (للأدمـن فقط)
    if text == "🛠 لوحة التحكم":
        if not is_admin(chat_id):
            bot.reply_to(message, "⚠️ هذه اللوحة متاحة للأدمـن فقط.")
            return

        bot.reply_to(
            message,
            "🛠 <b>لوحة التحكم الإدارية</b>\n\n"
            "اختر الإجراء المطلوب من الأزرار التالية:",
            reply_markup=make_admin_panel_keyboard()
        )
        return

    # زر قص مقطع يوتيوب
    if text == "✂️ قص مقطع يوتيوب":
        is_sub, _ = is_user_subscribed(chat_id)
        if not is_sub:
            bot.reply_to(
                message,
                "🔐 هذه الميزة متاحة للمشتركين فقط.\n"
                "للاشتراك اضغط على زر <b>📦 الاشتراكات</b> واختر الباقة المناسبة.",
                reply_markup=make_main_keyboard(chat_id)
            )
            return

        reset_session(chat_id)
        bot.reply_to(
            message,
            "🎬 أرسل الآن رابط فيديو يوتيوب (عادي أو بث محفوظ) لبدء عملية القص.",
            reply_markup=make_main_keyboard(chat_id)
        )
        return

    # ================= منطق القص الرئيسي =================

    # لو أرسل رابط يوتيوب في أي لحظة -> نبدأ جلسة جديدة مباشرة (مع التحقق من الاشتراك)
    if "youtu.be" in text or "youtube.com" in text:
        is_sub, _ = is_user_subscribed(chat_id)
        if not is_sub:
            bot.reply_to(
                message,
                "🔐 لا يمكنك استخدام القص قبل تفعيل الاشتراك.\n"
                "اضغط على <b>📦 الاشتراكات</b> للاطلاع على الباقات المتاحة.",
                reply_markup=make_main_keyboard(chat_id)
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
            "<code>00:01:20</code>",
            reply_markup=make_main_keyboard(chat_id)
        )
        return

    # إن لم تكن جلسة موجودة، نطلب منه رابط أو /start
    if not session:
        bot.reply_to(
            message,
            "⚠️ أرسل أولاً رابط فيديو يوتيوب أو استخدم الأمر /start.",
            reply_markup=make_main_keyboard(chat_id)
        )
        return

    step = session.get("step", "await_url")

    if step == "await_url":
        if "youtu" not in text:
            bot.reply_to(message, "⚠️ أرسل رابط يوتيوب صحيح لبدء القص.", reply_markup=make_main_keyboard(chat_id))
            return
        url = extract_url(text)
        session["url"] = url
        session["step"] = "await_start"
        bot.reply_to(
            message,
            "⏱️ أرسل وقت <b>البداية</b> بصيغة مثل:\n"
            "<code>80</code>\n<code>1:20</code>\n<code>00:01:20</code>",
            reply_markup=make_main_keyboard(chat_id)
        )

    elif step == "await_start":
        try:
            start_seconds = parse_time_to_seconds(text)
        except ValueError:
            bot.reply_to(message, "⚠️ صيغة وقت البداية غير صحيحة، أعد الإرسال.", reply_markup=make_main_keyboard(chat_id))
            return

        session["start"] = start_seconds
        session["step"] = "await_end"
        bot.reply_to(
            message,
            "⏱️ الآن أرسل وقت <b>النهاية</b> لنقطة القص بنفس الصيغ السابقة.\n"
            "مثال: <code>00:05:00</code> يعني بعد 5 دقائق من بداية الفيديو.",
            reply_markup=make_main_keyboard(chat_id)
        )

    elif step == "await_end":
        try:
            end_seconds = parse_time_to_seconds(text)
        except ValueError:
            bot.reply_to(message, "⚠️ صيغة وقت النهاية غير صحيحة، أعد الإرسال.", reply_markup=make_main_keyboard(chat_id))
            return

        start_seconds = session.get("start", 0)
        if end_seconds <= start_seconds:
            bot.reply_to(
                message,
                "⚠️ يجب أن يكون وقت النهاية أكبر من وقت البداية.\nأعد إرسال وقت النهاية.",
                reply_markup=make_main_keyboard(chat_id)
            )
            return

        duration = end_seconds - start_seconds
        session["end"] = end_seconds
        session["duration"] = duration

        # الآن فحص الجودات
        bot.reply_to(message, "⏳ يتم فحص الجودات المتاحة للفيديو…", reply_markup=make_main_keyboard(chat_id))

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
            session["quality_height"] = 360
            session["step"] = "processing"
            bot.send_message(
                chat_id,
                "⚠️ لم أجد جودات قياسية (144p–1080p) لهذا الفيديو.\n"
                "سيتم استخدام جودة <b>360p</b> افتراضياً."
            )
            start_cutting(chat_id)
            return

        session["available_heights"] = heights
        session["step"] = "choose_quality"

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
            "انتظر حتى ينتهي أو أرسل رابط يوتيوب جديد لبدء عملية جديدة.",
            reply_markup=make_main_keyboard(chat_id)
        )


# ================= كول باك للأزرار =================

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
        pass

    start_cutting(chat_id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("plan_req_"))
def handle_plan_request(call):
    """
    زر اختيار الباقة من جهة العميل
    """
    chat_id = call.message.chat.id
    user = call.from_user

    plan_key = call.data.replace("plan_req_", "")
    if plan_key not in PLANS:
        bot.answer_callback_query(call.id, "⚠️ باقة غير معروفة.", show_alert=True)
        return

    plan_name = PLANS[plan_key]["name"]

    bot.answer_callback_query(call.id, f"تم اختيار الباقة: {plan_name}", show_alert=False)

    bot.send_message(
        chat_id,
        f"📦 لقد اخترت باقة: <b>{plan_name}</b>\n\n"
        "💳 الرجاء إرسال لقطة شاشة لإثبات الدفع في هذه المحادثة مع ذكر <b>ID</b> الخاص بك.\n"
        "بعد التحقق، سيقوم الأدمـن بتفعيل اشتراكك.",
    )

    # إشعار للأدمـن بطلب هذه الباقة
    try:
        bot.send_message(
            ADMIN_ID,
            "📥 <b>طلب اشتراك جديد</b>\n\n"
            f"🆔 المستخدم: <code>{user.id}</code>\n"
            f"👤 الاسم: {user.first_name or ''}\n"
            f"🪪 اليوزر: @{user.username}" if user.username else "🪪 اليوزر: بدون يوزر\n"
            f"\n📦 الباقة المطلوبة: {plan_name}"
        )
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_"))
def handle_admin_callbacks(call):
    chat_id = call.message.chat.id

    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, "هذه الخيارات متاحة للأدمـن فقط.", show_alert=True)
        return

    data = call.data

    if data == "admin_activate":
        session = user_sessions.get(chat_id) or {}
        session["step"] = "admin_select_plan"
        user_sessions[chat_id] = session
        bot.answer_callback_query(call.id)
        bot.send_message(
            chat_id,
            "✅ اختر أولاً الباقة التي تريد تفعيلها للمستخدم:",
            reply_markup=make_plans_keyboard("admin_plan")
        )

    elif data == "admin_deactivate":
        session = user_sessions.get(chat_id) or {}
        session["step"] = "admin_wait_id_deactivation"
        user_sessions[chat_id] = session
        bot.answer_callback_query(call.id)
        bot.send_message(
            chat_id,
            "⛔ أرسل الآن رقم <b>ID</b> الخاص بالمستخدم الذي تريد إلغاء اشتراكه:"
        )

    elif data == "admin_stats":
        stats = get_stats()
        total_visitors = get_total_visitors()
        subs = get_subscriptions()
        total_subscribers = len(subs)
        today_visitors = get_today_visitors()
        last_activations = get_last_20_subscribers()

        text = (
            "📊 <b>إحصائيات البوت</b>\n\n"
            f"👥 إجمالي الزوار: <b>{total_visitors}</b>\n"
            f"👤 إجمالي المشتركين: <b>{total_subscribers}</b>\n"
            f"📆 زوار اليوم: <b>{today_visitors}</b>\n"
        )

        if last_activations:
            text += "\n🆕 <b>آخر 20 اشتراك مفعّل:</b>\n"
            for a in last_activations:
                uid = a.get("user_id")
                plan_key = a.get("plan")
                act_time = a.get("activated_at", "")[:19]
                text += f"- ID: <code>{uid}</code> | {human_plan_name(plan_key)} | في: {act_time}\n"

        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, text)
    else:
        bot.answer_callback_query(call.id, "أمر غير معروف.")


@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_plan_"))
def handle_admin_plan_choice(call):
    chat_id = call.message.chat.id

    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, "هذه الخيارات متاحة للأدمـن فقط.", show_alert=True)
        return

    plan_key = call.data.replace("admin_plan_", "")
    if plan_key not in PLANS:
        bot.answer_callback_query(call.id, "⚠️ باقة غير معروفة.", show_alert=True)
        return

    session = user_sessions.get(chat_id) or {}
    session["selected_plan"] = plan_key
    session["step"] = "admin_wait_id_activation"
    user_sessions[chat_id] = session

    plan_name = PLANS[plan_key]["name"]

    bot.answer_callback_query(call.id)
    bot.send_message(
        chat_id,
        f"📦 الباقة المختارة: <b>{plan_name}</b>\n\n"
        "✏️ أرسل الآن رقم <b>ID</b> الخاص بالمستخدم الذي تريد تفعيل هذه الباقة له:"
    )


# ================= تنفيذ القص =================

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
        parts = split_media_to_parts(cut_file, max_mb=MAX_TELEGRAM_MB)
        logger.info("Parts to send: %s", parts)

        total_parts = len(parts)
        if total_parts == 0:
            bot.send_message(chat_id, "❌ لم أستطع استخراج المقطع بعد القص.")
            return

        # إرسال الأجزاء كفيديو واحداً تلو الآخر
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
            "يمكنك الآن إرسال رابط يوتيوب جديد لقص مقطع آخر 🎯.",
            reply_markup=make_main_keyboard(chat_id)
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


# ================= تشغيل البوت مع معالجة أخطاء polling =================
if __name__ == "__main__":
    logger.info("🔥 Bot is running…")

    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            logger.error("Polling error from Telegram: %s", e)
            time.sleep(5)
