import telebot
import yt_dlp
import dlplebot
import subprocess

TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = telebot.TeleBot(TOKEN)

sessions = {}

def download_video(url):
    ydl_opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": "video_source.%(ext)s"
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)

def cut_video(source, start, end):
    output = "cut_video.mp4"
    cmd = [
        "ffmpeg", "-i", source,
        "-ss", start, "-to", end,
        "-c", "copy",
        output, "-y"
    ]
    subprocess.run(cmd)
    return output

@bot.message_handler(commands=['cut'])
def ask_link(message):
    bot.reply_to(message, "🎥 أرسل رابط الفيديو الآن:")
    sessions[message.chat.id] = {"step": 1}

@bot.message_handler(func=lambda m: True)
def handle_steps(message):
    chat_id = message.chat.id
    if chat_id not in sessions:
        return

    step = sessions[chat_id]["step"]

    # Step 1 — الرابط
    if step == 1:
        sessions[chat_id]["url"] = message.text
        bot.reply_to(message, "⏳ أرسل وقت البداية (ثوانٍ فقط)")
        sessions[chat_id]["step"] = 2

    # Step 2 — البداية
    elif step == 2:
        sessions[chat_id]["start"] = message.text
        bot.reply_to(message, "⏳ أرسل وقت النهاية (ثوانٍ)")
        sessions[chat_id]["step"] = 3

    # Step 3 — النهاية
    elif step == 3:
        sessions[chat_id]["end"] = message.text
        url = sessions[chat_id]["url"]
        start = sessions[chat_id]["start"]
        end = sessions[chat_id]["end"]

        bot.reply_to(message, "🔁 جاري القص… يرجى الانتظار")

        try:
            src = download_video(url)
            out = cut_video(src, start, end)

            with open(out, "rb") as v:
                bot.send_video(chat_id, v)

            os.remove(src)
            os.remove(out)
        except Exception as e:
            bot.reply_to(message, f"❌ خطأ أثناء المعالجة:\n{e}")

        del sessions[chat_id]

print("🔥 Bot started...")
bot.polling(non_stop=True)
