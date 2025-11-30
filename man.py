import telebot
import yt_dlp
import os
from telebot.types import ReplyKeyboardRemove

BOT_TOKEN = "8487554427:AAG6Mt-IaWy0JN2mCE-Fmh1SCrloL2WxSeQ"
bot = telebot.TeleBot(BOT_TOKEN)

# تخزين حالة كل مستخدم
user_states = {}

def download_video(url):
    ydl_opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": "source.%(ext)s"
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
        info = ydl.extract_info(url, download=False)
        return ydl.prepare_filename(info)

def cut_video(source, start, end, output="cut.mp4"):
    cmd = f"ffmpeg -i '{source}' -ss {start} -to {end} -c copy {output} -y"
    os.system(cmd)
    return output

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "🎬 أهلاً بك! أرسل رابط الفيديو من يوتيوب أو تيك توك.")
    user_states[message.chat.id] = {"step": "awaiting_url"}

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    chat_id = message.chat.id
    text = message.text.strip()

    if chat_id not in user_states:
        user_states[chat_id] = {"step": "awaiting_url"}

    step = user_states[chat_id]["step"]

    # 1 - استلام الرابط
    if step == "awaiting_url":
        if text.startswith("http"):
            user_states[chat_id]["url"] = text
            user_states[chat_id]["step"] = "await_start"

            bot.reply_to(chat_id, "⏱️ ممتاز! أرسل **وقت البداية**\nمثال: 00:10")
        else:
            bot.reply_to(chat_id, "❌ أرسل رابط صحيح.")
        return

    # 2 - استلام وقت البداية
    if step == "await_start":
        user_states[chat_id]["start"] = text
        user_states[chat_id]["step"] = "await_end"
        bot.reply_to(chat_id, "⏳ الآن أرسل **وقت النهاية**\nمثال: 05:00")
        return

    # 3 - استلام وقت النهاية
    if step == "await_end":
        user_states[chat_id]["end"] = text

        url = user_states[chat_id]["url"]
        start = user_states[chat_id]["start"]
        end = user_states[chat_id]["end"]

        bot.reply_to(chat_id, "🔧 جاري القص… انتظر قليلاً")

        try:
            src = download_video(url)
            output = cut_video(src, start, end)

            with open(output, "rb") as v:
                bot.send_video(chat_id, v)

            os.remove(src)
            os.remove(output)

        except Exception as e:
            bot.reply_to(chat_id, f"❌ حدث خطأ أثناء القص:\n{e}")

        user_states[chat_id]["step"] = "awaiting_url"
