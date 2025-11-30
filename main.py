import telebot
import yt_dlp
import os
import subprocess

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = telebot.TeleBot(BOT_TOKEN)

users = {}  # لتخزين خطوات المحادثة لكل مستخدم

# ------------------------ استخراج قائمة الجودة ------------------------
def get_formats(url):
    try:
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "extractor_args": {
                "youtube": {"player_client": ["default"]}
            }
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get("formats", [])
            qualities = []

            for f in formats:
                if f.get("vcodec") != "none" and f.get("acodec") != "none":
                    if f.get("format_id") and f.get("resolution"):
                        qualities.append((f["format_id"], f["resolution"]))

            return qualities

    except Exception as e:
        return None

# ------------------------ قص الفيديو ------------------------
def cut_video(url, start_time, end_time, format_id):
    output = "cut.mp4"

    try:
        ydl_opts = {
            "format": format_id,
            "outtmpl": "source.%(ext)s",
            "extractor_args": {
                "youtube": {"player_client": ["default"]}
            }
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)

        # قص مقطع
        cmd = [
            "ffmpeg",
            "-ss", start_time,
            "-to", end_time,
            "-i", file_path,
            "-c", "copy",
            output,
            "-y"
        ]

        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        os.remove(file_path)
        return output

    except Exception as e:
        return None


# ------------------------ الأوامر ------------------------
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "أرسل رابط الفيديو من فضلك 🎬")
    users[message.chat.id] = {"step": "url"}


@bot.message_handler(func=lambda m: True)
def handler(message):
    chat_id = message.chat.id
    text = message.text

    if chat_id not in users:
        users[chat_id] = {"step": "url"}

    step = users[chat_id]["step"]

    # -------- الخطوة 1: وضع الرابط --------
    if step == "url":
        users[chat_id]["url"] = text
        bot.send_message(chat_id, "⏳ يتم فحص الجودة…")

        formats = get_formats(text)

        if not formats:
            bot.send_message(chat_id, "❌ فشل استخراج الجودة، أرسل رابطاً صالحاً.")
            return

        msg = "🎚 اختر الجودة:\n"
        for f in formats:
            msg += f"• {f[0]} — {f[1]}\n"

        bot.send_message(chat_id, msg)
        bot.send_message(chat_id, "✏ اكتب كود الجودة (format_id) مثل: 18 أو 22…")

        users[chat_id]["formats"] = formats
        users[chat_id]["step"] = "quality"
        return

    # -------- الخطوة 2: اختيار الجودة --------
    if step == "quality":
        format_id = text.strip()

        valid_formats = [f[0] for f in users[chat_id]["formats"]]
        if format_id not in valid_formats:
            bot.send_message(chat_id, "❌ جودة غير موجودة. أعد المحاولة.")
            return

        users[chat_id]["format_id"] = format_id
        bot.send_message(chat_id, "⏱ الآن أرسل وقت البداية (مثال: 00:01:20)")
        users[chat_id]["step"] = "start"
        return

    # -------- الخطوة 3: وقت البداية --------
    if step == "start":
        users[chat_id]["start"] = text.strip()
        bot.send_message(chat_id, "⏱ الآن أرسل وقت النهاية (مثال: 00:05:00)")
        users[chat_id]["step"] = "end"
        return

    # -------- الخطوة 4: وقت النهاية + التنفيذ --------
    if step == "end":
        url = users[chat_id]["url"]
        start_t = users[chat_id]["start"]
        end_t = text.strip()
        format_id = users[chat_id]["format_id"]

        bot.send_message(chat_id, "🔧 جاري القص… الرجاء الانتظار")

        result = cut_video(url, start_t, end_t, format_id)

        if result is None or not os.path.exists(result):
            bot.send_message(chat_id, "❌ فشل القص. حاول بجودة مختلفة.")
            return

        size = os.path.getsize(result)

        # إذا الملف كبير جداً → ارسال document
        if size > 45 * 1024 * 1024:
            with open(result, "rb") as f:
                bot.send_document(chat_id, f, caption="🎬 المقطع جاهز!")
        else:
            with open(result, "rb") as f:
                bot.send_video(chat_id, f, caption="🎬 المقطع جاهز!")

        os.remove(result)
        users.pop(chat_id, None)

        bot.send_message(chat_id, "✔ انتهى! أرسل رابط فيديو جديد.")

# ------------------------ تشغيل البوت ------------------------
print("🔥 Bot is running…")
bot.infinity_polling(skip_pending=True)
