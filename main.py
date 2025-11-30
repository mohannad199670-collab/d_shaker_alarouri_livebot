import telebot
import subprocess
import yt_dlp
import os

BOT_TOKEN = "ضع_توكن_البوت_هنا"
bot = telebot.TeleBot(BOT_TOKEN)

def get_stream_url(video_url):
    ydl_opts = {
        "format": "best",
        "quiet": True,
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        return info["url"]  # رابط الستريم الحقيقي


def cut_video_stream(stream_url, start_time, duration):
    output_file = "cut.mp4"

    # قص مباشر دون تحميل كامل
    command = [
        "ffmpeg",
        "-ss", start_time,
        "-i", stream_url,
        "-t", duration,
        "-c", "copy",
        "-y",
        output_file
    ]

    subprocess.run(command)
    return output_file


@bot.message_handler(commands=['cut'])
def start_cut(message):
    bot.reply_to(message, "📹 أرسل رابط الفيديو الآن:")
    bot.register_next_step_handler(message, get_url)


def get_url(message):
    url = message.text.strip()
    bot.reply_to(message, "⏳ أرسل وقت البداية بصيغة:\n00:01:30")
    bot.register_next_step_handler(message, get_start, url)


def get_start(message, url):
    start = message.text.strip()
    bot.reply_to(message, "⏳ أرسل المدة المطلوبة بصيغة:\n00:05:00")
    bot.register_next_step_handler(message, process_cut, url, start)


def process_cut(message, url, start):
    duration = message.text.strip()

    try:
        bot.reply_to(message, "🎬 جاري تجهيز رابط البث…")
        stream = get_stream_url(url)

        bot.reply_to(message, "✂️ جاري القص… انتظر قليلاً")

        output = cut_video_stream(stream, start, duration)

        with open(output, "rb") as video:
            bot.send_video(message.chat.id, video)

        os.remove(output)

    except Exception as e:
        bot.reply_to(message, f"❌ خطأ: {e}")


print("🔥 Bot is running…")
bot.polling()
