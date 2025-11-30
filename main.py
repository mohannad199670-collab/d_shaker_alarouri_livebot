import telebot
import subprocess
import yt_dlp
import os

# ===========================
# قراءة التوكن من متغير البيئة
# ===========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN غير موجود داخل Koyeb Environment Variables")

bot = telebot.TeleBot(BOT_TOKEN)

# ===========================
# استخراج رابط الستريم مباشرة (بدون تحميل)
# ===========================
def get_stream_url(video_url):
    ydl_opts = {
        "quiet": True,
        "format": "best",
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        return info["url"]


# ===========================
# قص الفيديو مباشرة من الستريم
# ===========================
def cut_video_stream(stream_url, start_time, duration):
    output_file = "cut.mp4"
    command = [
        "ffmpeg",
        "-ss", start_time,
        "-i", stream_url,
        "-t", duration,
        "-c", "copy",
        output_file,
        "-y",
    ]

    process = subprocess.run(command, capture_output=True, text=True)

    if process.returncode != 0:
        return None, process.stderr

    return output_file, None


# ===========================
#  استقبال أمر /cut
# ===========================
user_sessions = {}


@bot.message_handler(commands=['cut'])
def ask_video(message):
    bot.reply_to(message, "📹 أرسل رابط الفيديو الذي تريد قصه:")
    user_sessions[message.chat.id] = {"step": 1}


@bot.message_handler(func=lambda m: m.chat.id in user_sessions)
def process_steps(message):
    chat_id = message.chat.id
    step = user_sessions[chat_id]["step"]

    # الخطوة 1 – استقبال الرابط
    if step == 1:
        user_sessions[chat_id]["url"] = message.text
        bot.send_message(chat_id, "⏱️ أرسل وقت البداية (مثال: 00:01:30):")
        user_sessions[chat_id]["step"] = 2

    # الخطوة 2 – استقبال البداية
    elif step == 2:
        user_sessions[chat_id]["start"] = message.text
        bot.send_message(chat_id, "⏱️ أرسل وقت النهاية (مثال: 00:05:00):")
        user_sessions[chat_id]["step"] = 3

    # الخطوة 3 – استقبال النهاية والقص
    elif step == 3:
        start = user_sessions[chat_id]["start"]
        end = message.text

        bot.send_message(chat_id, "🔍 جاري تجهيز الستريم...")

        try:
            stream_url = get_stream_url(user_sessions[chat_id]["url"])
        except Exception as e:
            bot.send_message(chat_id, f"❌ فشل استخراج الستريم:\n{e}")
            user_sessions.pop(chat_id, None)
            return

        bot.send_message(chat_id, "✂️ جاري القص بدون تحميل كامل، انتظر...")

        # حساب المدة = النهاية - البداية
        duration = end

        output, error = cut_video_stream(stream_url, start, duration)

        if error:
            bot.send_message(chat_id, f"❌ حدث خطأ أثناء القص:\n{error}")
        else:
            with open(output, "rb") as vid:
                bot.send_video(chat_id, vid)

            os.remove(output)

        user_sessions.pop(chat_id, None)


print("🔥 Bot is running…")
bot.polling()
