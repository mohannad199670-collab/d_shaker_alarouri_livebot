import telebot
import yt_dlp
import subprocess
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

user_steps = {}

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "🎬 أرسل رابط فيديو يوتيوب أو تيك توك لقصّه.")

@bot.message_handler(func=lambda m: True)
def handle_url(message):
    chat_id = message.chat.id
    text = message.text

    if chat_id not in user_steps:
        if "youtube.com" in text or "youtu.be" in text or "tiktok.com" in text:
            user_steps[chat_id] = {"url": text}
            bot.send_message(chat_id, "⏱️ أرسل **وقت البداية** (مثال 00:10)")
        return

    if "start" not in user_steps[chat_id]:
        user_steps[chat_id]["start"] = text
        bot.send_message(chat_id, "⏱️ أرسل **وقت النهاية** (مثال 00:20)")
        return

    if "end" not in user_steps[chat_id]:
        user_steps[chat_id]["end"] = text
        bot.send_message(chat_id, "⏳ جاري القص…")

        url = user_steps[chat_id]["url"]
        start_t = user_steps[chat_id]["start"]
        end_t = user_steps[chat_id]["end"]

        download_video(chat_id, url, start_t, end_t)
        del user_steps[chat_id]


def download_video(chat_id, url, start_t, end_t):
    output = "video.mp4"
    cut = "cut.mp4"

    bot.send_message(chat_id, "⬇️ جاري تنزيل الفيديو…")

    ydl_opts = {
        "format": "mp4",
        "outtmpl": output
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except:
        bot.send_message(chat_id, "❌ فشل التحميل")
        return

    bot.send_message(chat_id, "✂️ جاري القص…")

    cmd = [
        "ffmpeg", "-i", output, "-ss", start_t, "-to", end_t,
        "-c", "copy", cut
    ]

    try:
        subprocess.run(cmd, check=True)
        bot.send_video(chat_id, open(cut, "rb"))
    except:
        bot.send_message(chat_id, "❌ فشل القص")

    os.remove(output)
    os.remove(cut)


bot.polling()
