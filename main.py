import os
import telebot
import yt_dlp
import subprocess

# قراءة التوكن من Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)


# تحويل صيغة الوقت تلقائياً من 10 أو 1:20 أو 01:02:33
def normalize_time(t):
    try:
        parts = t.split(":")
        parts = [int(p) for p in parts]

        if len(parts) == 1:
            # ثواني فقط
            return f"00:00:{parts[0]:02d}"
        elif len(parts) == 2:
            # دقائق + ثواني
            return f"00:{parts[0]:02d}:{parts[1]:02d}"
        elif len(parts) == 3:
            # ساعات + دقائق + ثواني
            return f"{parts[0]:02d}:{parts[1]:02d}:{parts[2]:02d}"
        else:
            return t
    except:
        return t


# 📌 الحصول على رابط البث أو الفيديو الحقيقي بدون تحميل
def get_stream_url(video_url):
    ydl_opts = {
        "format": "best",
        "quiet": True,
        "noplaylist": True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        return info["url"]


# 📌 قص الفيديو بدون تحميله (مباشر من الرابط)
def cut_video_stream(stream_url, start_time, duration):
    output_file = "cut.mp4"

    command = [
        "ffmpeg",
        "-ss", start_time,
        "-i", stream_url,
        "-t", duration,
        "-c", "copy",
        output_file
    ]

    try:
        subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return output_file
    except Exception as e:
        return None


# 🚀 الرد على /start
@bot.message_handler(commands=['start'])
def start_message(message):
    bot.reply_to(message, "أرسل رابط فيديو أو بث مباشر من اليوتيوب.")


# 🚀 استقبال الرابط
@bot.message_handler(func=lambda m: "youtube.com" in m.text or "youtu.be" in m.text)
def process_link(message):
    chat_id = message.chat.id
    url = message.text.strip()

    bot.send_message(chat_id, "📌 أرسل وقت البداية (ثواني فقط أو نص مثل 1:20 أو 01:05:22):")
    bot.register_next_step_handler(message, lambda m: ask_end_time(m, url))


def ask_end_time(message, url):
    chat_id = message.chat.id
    start_time_raw = message.text.strip()
    start_time = normalize_time(start_time_raw)

    bot.send_message(chat_id, "⏳ أرسل وقت النهاية:")
    bot.register_next_step_handler(message, lambda m: start_cutting(m, url, start_time))


def start_cutting(message, url, start_time):
    chat_id = message.chat.id
    end_time_raw = message.text.strip()
    end_time = normalize_time(end_time_raw)

    bot.send_message(chat_id, "🔍 جاري تجهيز الرابط…")

    try:
        stream_url = get_stream_url(url)
    except:
        bot.send_message(chat_id, "❌ حدث خطأ أثناء الحصول على رابط الفيديو.")
        return

    # حساب مدة القص
    def to_seconds(t):
        h, m, s = t.split(":")
        return int(h)*3600 + int(m)*60 + int(s)

    duration_seconds = to_seconds(end_time) - to_seconds(start_time)
    duration = str(duration_seconds)

    bot.send_message(chat_id, "✂️ جاري قص المقطع… يرجى الانتظار")

    cut_file = cut_video_stream(stream_url, start_time, duration)

    if cut_file and os.path.exists(cut_file):
        bot.send_message(chat_id, "📤 جاري إرسال المقطع…")
        with open(cut_file, "rb") as video:
            bot.send_video(chat_id, video)
        os.remove(cut_file)
    else:
        bot.send_message(chat_id, "❌ حدث خطأ أثناء القص.")


# تشغيل البوت
if __name__ == "__main__":
    print("🔥 Bot is running…")
    bot.infinity_polling(skip_pending=True)
