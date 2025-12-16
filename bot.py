import os
import re
import math
import time
import json
import logging
import subprocess
from datetime import datetime, date, timedelta

import requests
import yt_dlp
from yt_dlp.utils import DownloadError

import telebot
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telebot.apihelper import ApiTelegramException

# ================= إعداد اللوج =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ================ إعداد التوكن و ID الأدمن ================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # يمكنك حذفه إذا تضع التوكن من المتغيرات فقط

ADMIN_ENV = os.getenv("ADMIN_ID", "").strip()
try:
    ADMIN_ID = int(ADMIN_ENV) if ADMIN_ENV else 604494923
except ValueError:
    ADMIN_ID = 604494923
    logger.warning("⚠️ قيمة ADMIN_ID في البيئة غير صالحة، سيتم استخدام 604494923 كأدمن افتراضي")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ================ بيانات الدفع ================
PAYEER_ACCOUNT = "P1058635648"  # حساب Payeer الخاص بك

# ================ إعدادات الحجم =================
MAX_TELEGRAM_MB = 48  # الحد الأقصى المستهدف لكل جزء

# ================ ملف قاعدة البيانات البسيطة =================
DB_FILE = "database.json"

DEFAULT_DB = {
    "users": {},
    "visitors_today": 0,
    "last_reset_date": "",
}

def load_db():
    if not os.path.exists(DB_FILE):
        return DEFAULT_DB.copy()
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
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
    uid = str(user_id)
    users = db.setdefault("users", {})
    user = users.get(uid) or {}
    user.setdefault("subscription", None)
    user.setdefault("total_visits", 0)
    user.setdefault("joined_at", today_str())

    user["first_name"] = first_name or ""
    user["username"] = username or ""
    user["last_seen"] = today_str()
    user["total_visits"] = int(user.get("total_visits", 0)) + 1

    users[uid] = user
    db["users"] = users

def register_visit(user_id: int, first_name: str, username: str):
    db = load_db()
    ensure_daily_reset(db)
    db["visitors_today"] = int(db.get("visitors_today", 0)) + 1
    ensure_user(db, user_id, first_name, username)
    save_db(db)

# ================ نظام الإشتراكات =================
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
# لكل مستخدم نخزن:
# step, url, start, end, duration, quality_height, mode,
# pending_plan, admin_chosen_plan, ...
user_sessions = {}

def reset_session(chat_id: int):
    user_sessions[chat_id] = {
        "step": "await_url",
        "pending_plan": None,
        "admin_chosen_plan": None,
    }

# ================ دوال مساعدة للواجهة ================
def build_main_keyboard(chat_id: int):
    kb = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    kb.row(
        KeyboardButton("✂️ قص مقطع يوتيوب"),
        KeyboardButton("📦 الاشتراكات"),
    )
    if is_admin(chat_id):
        kb.row(
            KeyboardButton("⚙️ الإعدادات"),
            KeyboardButton("🛠 لوحة التحكم"),
        )
    else:
        kb.row(KeyboardButton("⚙️ الإعدادات"))
    return kb

def build_plans_keyboard(for_admin_manual: bool = False):
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

def build_settings_keyboard(chat_id: int):
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_to_main"))
    return markup

def build_admin_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("➕ إضافة مشترك", callback_data="admin_add_sub"),
        InlineKeyboardButton("➖ إزالة مشترك", callback_data="admin_rem_sub"),
        InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats"),
        InlineKeyboardButton("📢 رسالة للكل", callback_data="admin_broadcast"),
        InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="back_to_main"),
    )
    return markup

# ================ دوال مساعدة لمعالجة الفيديو ================
def extract_url(text: str) -> str | None:
    match = re.search(r"(https?://[^'\s]+)", text)
    return match.group(1) if match else None

def parse_time_to_seconds(time_str: str) -> int:
    parts = list(map(int, time_str.split(':')))
    seconds = 0
    if len(parts) == 3:
        seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        seconds = parts[0] * 60 + parts[1]
    elif len(parts) == 1:
        seconds = parts[0]
    else:
        raise ValueError("صيغة الوقت غير صحيحة")
    return seconds

def get_available_qualities(video_url: str) -> list[int]:
    try:
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'force_generic_extractor': True,
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(video_url, download=False)
            if not info_dict:
                return []
            formats = info_dict.get('formats', [])

            heights = set()
            for f in formats:
                if f.get('height') and f.get('ext') == 'mp4':
                    heights.add(f['height'])

            return sorted(list(heights), reverse=True)

    except DownloadError as e:
        logger.error("Video is unavailable or download error with yt-dlp: %s", e)
        return []
    except Exception as e:
        logger.error("Error getting qualities with yt-dlp: %s", e)
        return []

def split_video_to_parts(input_file: str, max_mb: int) -> list[str]:
    duration_str = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            input_file,
        ]
    ).decode("utf-8").strip()
    total_duration = float(duration_str)
    file_size_mb = os.path.getsize(input_file) / (1024 * 1024)

    if file_size_mb <= max_mb:
        return [input_file]

    num_parts = math.ceil(file_size_mb / max_mb)
    part_duration = math.ceil(total_duration / num_parts)
    parts = []

    for i in range(num_parts):
        start_time = i * part_duration
        output_name = f"{os.path.splitext(input_file)[0]}_part{i+1}.mp4"
        command = [
            "ffmpeg",
            "-y",
            "-i",
            input_file,
            "-ss",
            str(start_time),
            "-t",
            str(part_duration),
            "-c",
            "copy",
            output_name,
        ]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        parts.append(output_name)

    return parts

def clean_files(*files):
    for f in files:
        if f and os.path.exists(f):
            try:
                os.remove(f)
            except Exception as e:
                logger.warning("Could not clean file %s: %s", f, e)

# ================ معالجات الأوامر والرسائل ================
@bot.message_handler(commands=['start'])
def handle_start(message):
    chat_id = message.chat.id
    register_visit(chat_id, message.from_user.first_name, message.from_user.username)
    reset_session(chat_id)
    bot.send_message(
        chat_id,
        "👋 أهلاً بك في بوت قص مقاطع يوتيوب!\n\n"
        "اختر أحد الخيارات من القائمة أدناه للبدء.",
        reply_markup=build_main_keyboard(chat_id),
    )

@bot.message_handler(func=lambda message: message.text == "✂️ قص مقطع يوتيوب")
def handle_cut_request(message):
    chat_id = message.chat.id
    if not has_active_subscription(chat_id):
        bot.send_message(chat_id, "⚠️ هذه الميزة للمشتركين فقط. يرجى الاشتراك أولاً.")
        return

    reset_session(chat_id)
    bot.send_message(chat_id, "🔗 أرسل رابط فيديو يوتيوب الذي تريد قص مقطع منه.")

@bot.message_handler(func=lambda message: message.text == "📦 الاشتراكات")
def handle_subscription_menu(message):
    chat_id = message.chat.id
    status = subscription_status_text(chat_id)
    bot.send_message(
        chat_id,
        f"<b>حالة اشتراكك:</b>\n{status}\n\n"
        "🧾 اختر باقة لتجديد أو تفعيل اشتراكك:",
        reply_markup=build_plans_keyboard(),
    )

@bot.message_handler(func=lambda message: message.text == "⚙️ الإعدادات")
def handle_settings(message):
    chat_id = message.chat.id
    bot.send_message(
        chat_id,
        "⚙️ هنا يمكنك إدارة إعداداتك الشخصية (سيتم إضافة المزيد من الخيارات لاحقاً).",
        reply_markup=build_settings_keyboard(chat_id),
    )

@bot.message_handler(func=lambda message: message.text == "🛠 لوحة التحكم" and is_admin(message.chat.id))
def handle_admin_panel(message):
    bot.send_message(
        message.chat.id,
        "🔐 أهلاً بك في لوحة تحكم الأدمن.",
        reply_markup=build_admin_keyboard(),
    )

# ================ معالج الصور (إثبات الدفع) ================
@bot.message_handler(content_types=['photo'])
def handle_payment_photo(message):
    chat_id = message.chat.id
    session = user_sessions.get(chat_id)
    if session and session.get("step") == "await_payment_proof" and session.get("pending_plan"):
        plan_key = session["pending_plan"]
        plan = PLANS.get(plan_key)
        if not plan:
            bot.reply_to(message, "⚠️ حدث خطأ في تحديد الباقة، أعد اختيار الباقة مرة أخرى.")
            reset_session(chat_id)
            return

        user = message.from_user
        user_id = user.id
        first_name = user.first_name or ""
        username = user.username or ""
        username_display = f"@{username}" if username else "لا يوجد"
        profile_link = f"https://t.me/{username}" if username else "لا يوجد رابط"

        caption = (
            "🧾 <b>طلب اشتراك جديد (دفع عبر Payeer)</b>\n\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"👤 الاسم: {first_name}\n"
            f"🪪 اليوزر: {username_display}\n"
            f"🔗 الرابط: {profile_link}\n\n"
            f"📦 الباقة المطلوبة: <b>{plan['name']}</b>\n"
            f"⏳ مدة الباقة: <b>{plan['days']}</b> يوم\n\n"
            f"💳 Payeer: <code>{PAYEER_ACCOUNT}</code>"
        )

        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("✅ تفعيل الاشتراك", callback_data=f"payok|{user_id}|{plan_key}"),
            InlineKeyboardButton("❌ رفض الطلب", callback_data=f"payno|{user_id}|{plan_key}"),
        )

        try:
            file_id = message.photo[-1].file_id
            bot.send_photo(
                ADMIN_ID,
                file_id,
                caption=caption,
                reply_markup=markup,
            )
        except Exception as e:
            logger.error("Error forwarding payment proof to admin: %s", e)

        bot.reply_to(
            message,
            "✅ تم استلام لقطة شاشة الدفع.\n"
            "📡 سيتم مراجعة طلبك من قبل الإدارة، وستصلك رسالة عند تفعيل الباقة أو رفض الطلب."
        )

        reset_session(chat_id)
    else:
        bot.reply_to(
            message,
            "📷 تم استلام الصورة.\n"
            "إن كنت قد دفعت، تأكد من اختيار الباقة أولاً من زر <b>📦 الاشتراكات</b>."
        )

# ================ كولباكات الباقات + لوحة التحكم + الدفع ================
@bot.callback_query_handler(func=lambda call: call.data.startswith("plan_"))
def handle_plan_selection(call):
    chat_id = call.message.chat.id
    parts = call.data.split("_")  # مثال: plan_p1_user أو plan_p3_admin
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "❌ بيانات غير صالحة.")
        return

    _, plan_key, target = parts
    is_admin_manual = (target == "admin")

    if is_admin_manual:
        # تفعيل يدوي من الأدمن
        if not is_admin(chat_id):
            bot.answer_callback_query(call.id, "هذه الأزرار خاصة بالإدارة.", show_alert=True)
            return

        session = user_sessions.setdefault(chat_id, {})
        session["admin_chosen_plan"] = plan_key
        session["step"] = "admin_await_user_id_for_plan"
        bot.edit_message_text(
            f"👍 تم اختيار الباقة: <b>{PLANS[plan_key]['name']}</b>\n"
            "الآن أرسل ID المستخدم الذي تريد تفعيلها له.",
            chat_id,
            call.message.message_id,
        )
        return

    # اختيار الباقة للمستخدم العادي
    plan = PLANS.get(plan_key)
    if not plan:
        bot.answer_callback_query(call.id, "❌ باقة غير صالحة.")
        return

    user_chat_id = call.from_user.id
    session = user_sessions.setdefault(user_chat_id, {})
    session["pending_plan"] = plan_key
    session["step"] = "await_payment_proof"

    bot.edit_message_text(
        f"✅ تم اختيار الباقة: <b>{plan['name']}</b>\n\n"
        "💳 <b>طريقة الدفع المتاحة:</b>\n"
        f"• Payeer: <code>{PAYEER_ACCOUNT}</code>\n\n"
        "📸 بعد إرسال المبلغ، قم بإرسال لقطة شاشة لإشعار الدفع هنا ليتم مراجعتها وتفعيل اشتراكك.",
        chat_id,
        call.message.message_id,
    )

@bot.callback_query_handler(func=lambda call: call.data == "back_to_main")
def handle_back_to_main(call):
    chat_id = call.message.chat.id
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass
    dummy_message = call.message
    dummy_message.text = "/start"
    handle_start(dummy_message)

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_"))
def handle_admin_actions(call):
    chat_id = call.message.chat.id
    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, "هذه الأزرار خاصة بالإدارة.", show_alert=True)
        return

    action = call.data.split("_", 1)[1]

    if action == "stats":
        stats = get_stats_text()
        bot.edit_message_text(stats, chat_id, call.message.message_id, reply_markup=build_admin_keyboard())
    elif action == "add_sub":
        bot.edit_message_text(
            "اختر الباقة التي تريد إضافتها:",
            chat_id,
            call.message.message_id,
            reply_markup=build_plans_keyboard(for_admin_manual=True),
        )
    elif action == "rem_sub":
        session = user_sessions.setdefault(chat_id, {})
        session["step"] = "admin_await_rem_sub_id"
        bot.edit_message_text(
            "أرسل ID المستخدم الذي تريد إزالة اشتراكه.",
            chat_id,
            call.message.message_id,
        )
    elif action == "broadcast":
        session = user_sessions.setdefault(chat_id, {})
        session["step"] = "admin_await_broadcast_msg"
        bot.edit_message_text(
            "أرسل الرسالة التي تريد إرسالها لجميع المستخدمين.",
            chat_id,
            call.message.message_id,
        )

# ================ كولباكات الدفع (تفعيل/رفض من الأدمن) ================
@bot.callback_query_handler(func=lambda call: call.data.startswith("payok|") or call.data.startswith("payno|"))
def handle_payment_decision(call):
    chat_id = call.message.chat.id
    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, "هذه الأزرار خاصة بالإدارة.", show_alert=True)
        return

    try:
        action, user_id_str, plan_key = call.data.split("|", 2)
        target_id = int(user_id_str)
    except Exception:
        bot.answer_callback_query(call.id, "بيانات الطلب غير صالحة.", show_alert=True)
        return

    plan = PLANS.get(plan_key)
    if not plan:
        bot.answer_callback_query(call.id, "الباقة غير معروفة.", show_alert=True)
        return

    if action == "payok":
        # تفعيل الاشتراك
        set_subscription(target_id, plan_key)
        status = subscription_status_text(target_id)

        # رسالة للمستخدم
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
                caption=(call.message.caption or "") + "\n\n✅ <b>تم تفعيل الاشتراك لهذا المستخدم.</b>",
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=None
            )
        except Exception:
            pass

        bot.answer_callback_query(call.id, "تم تفعيل الاشتراك 👍")

    elif action == "payno":
        # رفض الطلب
        try:
            bot.send_message(
                target_id,
                "❌ تم رفض طلب الاشتراك.\n"
                "إن كنت تعتقد أن هذا خطأ، تواصل مع الإدارة."
            )
        except Exception:
            pass

        try:
            bot.edit_message_caption(
                caption=(call.message.caption or "") + "\n\n❌ <b>تم رفض هذا الطلب.</b>",
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=None
            )
        except Exception:
            pass

        bot.answer_callback_query(call.id, "تم رفض الطلب.")

# ================ كولباك الجودات وأنواع الملفات ================
@bot.callback_query_handler(func=lambda call: call.data.startswith("quality_"))
def handle_quality_selection(call):
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id)
    if not session or session.get("step") != "await_quality":
        return

    quality = int(call.data.split("_")[1])
    session["quality_height"] = quality
    session["step"] = "await_mode"

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("فيديو 📹", callback_data="mode_video"),
        InlineKeyboardButton("صوت 🎵", callback_data="mode_audio"),
    )

    bot.edit_message_text(
        "🎬 اختر نوع الملف:",
        chat_id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("mode_"))
def handle_mode_selection(call):
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id)
    if not session or session.get("step") != "await_mode":
        return

    mode = call.data.split("_")[1]
    session["mode"] = mode
    session["step"] = "processing"
    bot.edit_message_text("⏳ طلبك قيد المعالجة...", chat_id, call.message.message_id)
    start_cutting(chat_id)

# ================ معالج النصوص العام ================
@bot.message_handler(content_types=['text'])
def handle_text(message):
    chat_id = message.chat.id
    text = message.text.strip()

    # تجاهل بعض الأزرار لأنها لها هاندلر خاص
    if text in ["✂️ قص مقطع يوتيوب", "📦 الاشتراكات", "⚙️ الإعدادات", "🛠 لوحة التحكم"]:
        return  # الهاندلر الخاص بها عالجها

    session = user_sessions.get(chat_id)
    if not session:
        handle_start(message)
        return

    step = session.get("step")

    # إدخالات الأدمن الخاصة
    if is_admin(chat_id):
        if step == "admin_await_rem_sub_id":
            try:
                target_id = int(text)
                clear_subscription(target_id)
                bot.send_message(chat_id, f"✅ تم إزالة اشتراك المستخدم {target_id}.")
                reset_session(chat_id)
            except ValueError:
                bot.send_message(chat_id, "⚠️ ID غير صالح.")
            return

        if step == "admin_await_user_id_for_plan" and session.get("admin_chosen_plan"):
            try:
                target_id = int(text)
                plan_key = session["admin_chosen_plan"]
                set_subscription(target_id, plan_key)
                bot.send_message(
                    chat_id,
                    f"✅ تم تفعيل اشتراك <b>{PLANS[plan_key]['name']}</b> للمستخدم ID: <code>{target_id}</code>."
                )
                try:
                    status = subscription_status_text(target_id)
                    bot.send_message(
                        target_id,
                        "✅ تم تفعيل اشتراكك من قبل الإدارة.\n\n" + status
                    )
                except Exception:
                    pass
                reset_session(chat_id)
            except ValueError:
                bot.send_message(chat_id, "⚠️ ID غير صالح.")
            return

        if step == "admin_await_broadcast_msg":
            db = load_db()
            users = db.get("users", {})
            sent_count = 0
            failed_count = 0
            bot.send_message(chat_id, f"📢 جاري إرسال الرسالة إلى {len(users)} مستخدم...")
            for uid in users.keys():
                try:
                    bot.send_message(int(uid), text)
                    sent_count += 1
                except Exception:
                    failed_count += 1
            bot.send_message(chat_id, f"✅ تم الإرسال.\n- نجح: {sent_count}\n- فشل: {failed_count}")
            reset_session(chat_id)
            return

    # خطوات القص
    if step == "await_url":
        url = extract_url(text)
        if not url:
            bot.send_message(chat_id, "⚠️ لم أجد رابطاً في رسالتك. أرسل رابط يوتيوب صالح.")
            return

        session["url"] = url
        session["step"] = "await_start_time"
        bot.send_message(chat_id, "⏰ الآن أرسل وقت بداية المقطع (مثال: 1:25).")

    elif step == "await_start_time":
        try:
            start_seconds = parse_time_to_seconds(text)
            session["start"] = start_seconds
            session["step"] = "await_end_time"
            bot.send_message(chat_id, "⏰ والآن أرسل وقت نهاية المقطع (مثال: 2:40).")
        except ValueError:
            bot.send_message(chat_id, "⚠️ صيغة الوقت غير صحيحة. أرسلها على شكل دقائق:ثواني (مثال: 1:25).")

    elif step == "await_end_time":
        try:
            end_seconds = parse_time_to_seconds(text)
            start_seconds = session.get("start", 0)
            if end_seconds <= start_seconds:
                bot.send_message(chat_id, "⚠️ وقت النهاية يجب أن يكون بعد وقت البداية.")
                return

            session["end"] = end_seconds
            session["duration"] = end_seconds - start_seconds
            session["step"] = "await_quality"

            qualities = get_available_qualities(session["url"])
            if not qualities:
                bot.send_message(
                    chat_id,
                    "⚠️ لم أجد أي جودات صالحة.\n"
                    "سيتم استخدام جودة 360p افتراضياً."
                )
                session["quality_height"] = 360
                session["step"] = "await_mode"
                markup = InlineKeyboardMarkup()
                markup.add(
                    InlineKeyboardButton("فيديو 📹", callback_data="mode_video"),
                    InlineKeyboardButton("صوت 🎵", callback_data="mode_audio"),
                )
                bot.send_message(chat_id, "🎬 اختر نوع الملف:", reply_markup=markup)
                return

            markup = InlineKeyboardMarkup()
            for q in qualities:
                markup.add(InlineKeyboardButton(f"{q}p", callback_data=f"quality_{q}"))

            bot.send_message(
                chat_id,
                "🎛️ اختر الجودة المطلوبة:",
                reply_markup=markup,
            )

        except ValueError:
            bot.send_message(chat_id, "⚠️ صيغة الوقت غير صحيحة. أرسلها على شكل دقائق:ثواني (مثال: 2:40).")

    else:
        # أي نص آخر بينما الخطوة مختلفة
        bot.send_message(
            chat_id,
            "ℹ️ إن أردت قص مقطع جديد:\n"
            "اضغط على زر <b>✂️ قص مقطع يوتيوب</b> من القائمة."
        )

# ================ القص المباشر بالفيديو/الصوت ================
def start_cutting(chat_id: int):
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
        "🔧 جاري تحميل الفيديو من يوتيوب وقص المقطع…\n"
        "قد يستغرق ذلك بعض الوقت حسب طول المقطع والجودة."
    )

    cut_file = None
    parts = []
    audio_file = None

    try:
        if mode == "video":
            bot.send_message(chat_id, "🔍 جاري تحليل رابط الفيديو للحصول على بث مباشر للجودة المطلوبة...")

            try:
                ydl_opts = {
                    'quiet': True,
                    'skip_download': True,
                    'force_generic_extractor': True,
                    'format': f'bestvideo[height<={quality_height}][ext=mp4]+bestaudio[ext=m4a]/'
                              f'best[height<={quality_height}][ext=mp4]/best',
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_dict = ydl.extract_info(url, download=False)
                    if not info_dict:
                        raise RuntimeError("تعذر الحصول على معلومات الفيديو من يوتيوب.")

                    video_url_stream = None
                    audio_url_stream = None

                    # محاولة الحصول على فورمات مدمج
                    try:
                        best_combined = ydl.get_format_info(
                            info_dict,
                            f'best[height<={quality_height}][ext=mp4]/best'
                        )
                    except Exception:
                        best_combined = None

                    if best_combined and best_combined.get('url'):
                        video_url_stream = best_combined['url']
                        audio_url_stream = None
                    else:
                        # نحاول فيديو وصوت منفصلين
                        try:
                            best_video = ydl.get_format_info(
                                info_dict,
                                f'bestvideo[height<={quality_height}][ext=mp4]'
                            )
                        except Exception:
                            best_video = None
                        try:
                            best_audio = ydl.get_format_info(
                                info_dict,
                                'bestaudio[ext=m4a]'
                            )
                        except Exception:
                            best_audio = None

                        if best_video and best_video.get('url'):
                            video_url_stream = best_video['url']
                        if best_audio and best_audio.get('url'):
                            audio_url_stream = best_audio['url']

                    if not video_url_stream:
                        raise RuntimeError("لم يتم العثور على رابط بث للفيديو بالجودة المطلوبة.")

            except DownloadError as e:
                raise RuntimeError(f"فشل في تحليل رابط الفيديو: {e}")
            except Exception as e:
                raise RuntimeError(f"خطأ أثناء تحليل معلومات الفيديو: {e}")

            temp_cut_name = f"cut_full_{chat_id}_{int(time.time())}.mp4"
            cut_file = temp_cut_name

            command = [
                "ffmpeg",
                "-y",
                "-ss",
                str(start_seconds),
                "-i",
                video_url_stream,
            ]

            if audio_url_stream:
                command.extend(["-i", audio_url_stream])
                command.extend(["-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac"])
            else:
                command.extend(["-c", "copy"])

            command.extend([
                "-t",
                str(duration),
                "-f",
                "mp4",
                cut_file,
            ])

            bot.send_message(chat_id, "✂️ جاري القص المباشر للمقطع... (قد يستغرق وقتاً)")

            result = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            if result.returncode != 0:
                error_output = result.stderr.decode("utf-8", errors="ignore")
                logger.error("FFmpeg stream cutting failed: %s", error_output)
                raise RuntimeError(f"فشل في القص المباشر باستخدام FFmpeg.")

            if not os.path.exists(cut_file) or os.path.getsize(cut_file) == 0:
                raise RuntimeError("ملف الفيديو المقصوص فارغ أو غير موجود.")

            logger.info("Stream cut file created: %s", cut_file)

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

        elif mode == "audio":
            temp_audio_name = f"cut_audio_{chat_id}_{int(time.time())}.m4a"
            audio_file = temp_audio_name

            command = [
                "ffmpeg",
                "-y",
                "-ss",
                str(start_seconds),
                "-i",
                url,
                "-t",
                str(duration),
                "-vn",
                "-c:a",
                "aac",
                "-f",
                "mp4",
                audio_file,
            ]

            bot.send_message(chat_id, "🎧 جاري القص المباشر للمقطع الصوتي...")

            result = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            if result.returncode != 0:
                error_output = result.stderr.decode("utf-8", errors="ignore")
                logger.error("FFmpeg stream cutting audio failed: %s", error_output)
                bot.send_message(chat_id, f"❌ فشل في القص المباشر للصوت.")
                return

            if not os.path.exists(audio_file) or os.path.getsize(audio_file) == 0:
                bot.send_message(chat_id, "❌ ملف الصوت المقصوص فارغ أو غير موجود.")
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

    except RuntimeError as e:
        logger.error("Video processing error: %s", e)
        bot.send_message(
            chat_id,
            f"❌ حدث خطأ أثناء تحميل أو معالجة الفيديو:\n<code>{e}</code>"
        )
    except Exception as e:
        logger.error("Unexpected error in start_cutting", exc_info=e)
        bot.send_message(
            chat_id,
            "❌ حدث خطأ غير متوقع أثناء القص أو التحميل."
        )
    finally:
        try:
            clean_files(cut_file, audio_file, *parts)
            for part in parts:
                clean_files(part)
        except Exception:
            pass

# ================ تشغيل البوت ================
if __name__ == "__main__":
    logger.info("🔥 Bot is running…")
    try:
        bot.polling(none_stop=True)
    except Exception as e:
        logger.critical("Bot polling failed: %s", e)
