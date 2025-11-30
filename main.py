###############################################
#         BOT VIDEO CUTTER v10 Ultimate       #
#            Telegram: pyTelegramBotAPI       #
#   Full Quality System + Time Parser + FFmpeg#
#      Large Files Support + Auto Re-Session  #
#          Developed for Mohannad ❤️          #
###############################################

import os
import subprocess
import yt_dlp
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.apihelper import ApiTelegramException

###############################################
#                  CONFIG                     #
###############################################

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ Error: BOT_TOKEN not found in Environment Variables!")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# جلسات المستخدم
user_sessions = {}   # chat_id → {"step":..., "url":..., "start":..., "end":..., "duration":..., "formats":{height:format_id}}


###############################################
#              TIME PARSING                   #
###############################################

def parse_time_to_seconds(t: str) -> int:
    """
    يقبل صيغ:
    10
    1:25
    00:05:20
    """
    t = t.strip()
    parts = t.split(":")
    if not all(p.isdigit() for p in parts):
        raise ValueError("Invalid time format")

    if len(parts) == 1:   # SS
        return int(parts[0])

    if len(parts) == 2:   # MM:SS
        m, s = map(int, parts)
        return m * 60 + s

    if len(parts) == 3:   # HH:MM:SS
        h, m, s = map(int, parts)
        return h * 3600 + m * 60 + s

    raise ValueError("Unsupported time format")


###############################################
#               QUALITY SCAN                  #
###############################################

def get_available_qualities(url: str):
    """إرجاع الجودات الموجودة فعلاً بالفيديو."""
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "no_warnings": True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = info.get("formats", [])
    desired_heights = [144, 240, 360, 480, 720, 1080]

    result = {}
    for f in formats:
        h = f.get("height")
        fmt = f.get("format_id")
        if h in desired_heights and fmt:
            result[h] = fmt

    return result


###############################################
#          DOWNLOAD VIDEO BY QUALITY          #
###############################################

def download_video(url: str, fmt_id: str):
    ydl_opts = {
        "format": fmt_id,
        "outtmpl": "source.%(ext)s",
        "quiet": True,
        "no_warnings": True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


###############################################
#                 CUT VIDEO                    #
###############################################

def cut_video(input_file, start_s, duration_s, output="cut.mp4"):
    """
    يستخدم إعادة ترميز (Re-encode) لضمان الصوت دائماً:
    - صوت AAC
    - فيديو libx264
    """
    command = [
        "ffmpeg",
        "-y",
        "-ss", str(start_s),
        "-i", input_file,
        "-t", str(duration_s),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-c:a", "aac",
        "-movflags", "+faststart",
        output
    ]
    subprocess.run(command, check=True)
    return output


###############################################
#       ENTRY: LISTEN TO ANY USER MESSAGE     #
###############################################

@bot.message_handler(func=lambda msg: True)
def handle_message(message):
    chat = message.chat.id
    text = message.text.strip()

    # إذا المستخدم جديد → ابدأ تلقائياً
    if chat not in user_sessions:
        user_sessions[chat] = {"step": "url"}
        bot.reply_to(message, "🎥 أرسل رابط فيديو يوتيوب لبدأ القص.")
        return

    step = user_sessions[chat]["step"]

    # ================ STEP 1: URL ================
    if step == "url":
        user_sessions[chat]["url"] = text
        user_sessions[chat]["step"] = "start"
        bot.reply_to(message, "⏱️ أرسل وقت البداية.")
        return

    # ========== STEP 2: START TIME ==========
    if step == "start":
        try:
            start_s = parse_time_to_seconds(text)
        except:
            bot.reply_to(message, "⚠️ صيغة وقت بداية غير صحيحة.\nأعد الإرسال.")
            return

        user_sessions[chat]["start"] = start_s
        user_sessions[chat]["step"] = "end"
        bot.reply_to(message, "⏱️ الآن أرسل وقت النهاية.")
        return

    # ========== STEP 3: END TIME ==========
    if step == "end":
        try:
            end_s = parse_time_to_seconds(text)
        except:
            bot.reply_to(message, "⚠️ صيغة وقت النهاية غير صحيحة.")
            return

        start = user_sessions[chat]["start"]
        if end_s <= start:
            bot.reply_to(message, "⚠️ وقت النهاية يجب أن يكون أكبر من وقت البداية.")
            return

        user_sessions[chat]["end"] = end_s
        user_sessions[chat]["duration"] = end_s - start

        bot.send_message(chat, "🔍 يتم الآن فحص الجودات…")
        try:
            qualities = get_available_qualities(user_sessions[chat]["url"])
        except:
            bot.send_message(chat, "❌ فشل في قراءة جودات الفيديو.")
            return

        # إذا لا توجد جودات قياسية:
        if not qualities:
            bot.send_message(chat, "⚠️ لا توجد جودات قياسية (144–1080p).\nسيتم اختيار أفضل جودة تلقائياً.")
            user_sessions[chat]["format"] = "best"
            return start_cutting(chat)

        user_sessions[chat]["formats"] = qualities
        user_sessions[chat]["step"] = "quality"

        # ========== Show Buttons ==========
        markup = InlineKeyboardMarkup(row_width=3)
        buttons = []

        for h in [144, 240, 360, 480, 720, 1080]:
            if h in qualities:
                buttons.append(InlineKeyboardButton(f"{h}p", callback_data=f"q_{h}"))

        markup.add(*buttons)
        bot.send_message(chat, "🎚️ اختر الجودة:", reply_markup=markup)
        return


###############################################
#            QUALITY BUTTON HANDLER           #
###############################################

@bot.callback_query_handler(func=lambda c: c.data.startswith("q_"))
def choose_quality(call):
    chat = call.message.chat.id
    session = user_sessions.get(chat)

    if not session:
        bot.answer_callback_query(call.id, "انتهت الجلسة. أرسل رابط جديد.")
        return

    height = int(call.data.split("_")[1])
    fmt = session["formats"][height]

    session["format"] = fmt
    bot.answer_callback_query(call.id, f"تم اختيار {height}p")
    bot.edit_message_text(f"⏳ يتم الآن القص بجودة {height}p…", chat, call.message.message_id)

    start_cutting(chat)


###############################################
#          MAIN CUTTING + SENDING             #
###############################################

def start_cutting(chat):
    session = user_sessions[chat]

    url     = session["url"]
    start_s = session["start"]
    duration_s = session["duration"]
    fmt_id  = session["format"]

    bot.send_message(chat, "🔧 جاري القص…")
    input_file = None
    output = "cut.mp4"

    try:
        input_file = download_video(url, fmt_id)
        cut_video(input_file, start_s, duration_s, output)

        bot.send_message(chat, "📤 جاري إرسال الفيديو…")

        size = os.path.getsize(output)

        if size < 48 * 1024 * 1024:
            with open(output, "rb") as f:
                bot.send_video(chat, f, caption="🎬 المقطع جاهز!")
        else:
            with open(output, "rb") as f:
                bot.send_document(chat, f, visible_file_name="video.mp4", caption="🎬 المقطع جاهز!")

        bot.send_message(chat, "✅ انتهى!\n🎥 أرسل رابطاً جديداً لقص مقطع آخر.")
        user_sessions[chat] = {"step": "url"}

    except ApiTelegramException as e:
        bot.send_message(chat, f"❌ خطأ من تلجرام:\n<code>{e}</code>")
        user_sessions[chat] = {"step": "url"}

    except Exception as e:
        bot.send_message(chat, "❌ حدث خطأ أثناء القص.")
        print("Error:", e)
        user_sessions[chat] = {"step": "url"}

    finally:
        try:
            if input_file and os.path.exists(input_file):
                os.remove(input_file)
            if os.path.exists(output):
                os.remove(output)
        except:
            pass


###############################################
#                RUN BOT                      #
###############################################

if __name__ == "__main__":
    print("🔥 BOT IS RUNNING — V10 ULTIMATE")
    bot.infinity_polling(skip_pending=True)
