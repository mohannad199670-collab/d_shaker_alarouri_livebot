import telebot
import subprocess
import yt_dlp
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

# -------------------------------------------
# تحويل الوقت تلقائياً (يدخل 10 ، 1:20 ، 01:02:33)
# -------------------------------------------
def parse_time(t):
    parts = t.split(":")
    parts = list(map(int, parts))
    if len(parts) == 1:
        return int(parts[0])
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0

# -------------------------------------------
# استخراج رابط البث أو الفيديو الحقيقي
# -------------------------------------------
def get_stream_url(video_url, quality_code):
    ydl_opts = {
        "quiet": True,
        "format": f"bestvideo[height={quality_code}]+bestaudio/best",
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        return info["url"]

# -------------------------------------------
# قص مباشر بدون تحميل
# -------------------------------------------
def cut_video(stream_url, start, end):
    output_file = "result.mp4"
    duration = end - start

    command = [
        "ffmpeg",
        "-ss", str(start),
        "-i", stream_url,
        "-t", str(duration),
        "-c", "copy",
        output_file,
        "-y"
    ]

    subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return output_file

# -------------------------------------------
# بدء البوت
# -------------------------------------------
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "🎬 أرسل رابط الفيديو أو البث الآن:")
    bot.register_next_step_handler(message, ask_start_time)

# حفظ الرابط
user_links = {}
user_times = {}
user_quality = {}

# الخطوة 2 — بداية القص
def ask_start_time(message):
    chat_id = message.chat.id
    user_links[chat_id] = message.text

    bot.send_message(chat_id, "⏱️ أرسل وقت البداية (مثال: 10 أو 1:20)")
    bot.register_next_step_handler(message, ask_end_time)

# الخطوة 3 — نهاية القص
def ask_end_time(message):
    chat_id = message.chat.id
    start_t = parse_time(message.text)
    user_times[chat_id] = {"start": start_t}

    bot.send_message(chat_id, "⏱️ أرسل وقت النهاية (مثال: 5:00 أو 1:10:00)")
    bot.register_next_step_handler(message, ask_quality)

# الخطوة 4 — اختيار الجودة
def ask_quality(message):
    chat_id = message.chat.id
    end_t = parse_time(message.text)
    user_times[chat_id]["end"] = end_t

    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    qs = ["144", "240", "360", "480", "720", "1080"]
    for q in qs:
        markup.add(f"{q}p")

    bot.send_message(chat_id, "🎚️ اختر الجودة:", reply_markup=markup)
    bot.register_next_step_handler(message, start_cutting)

# الخطوة 5 — تنفيذ القص
def start_cutting(message):
    chat_id = message.chat.id
    quality = message.text.replace("p", "")
    user_quality[chat_id] = int(quality)

    bot.send_message(chat_id, "🔄 جاري تجهيز الرابط…")

    url = user_links[chat_id]
    start = user_times[chat_id]["start"]
    end = user_times[chat_id]["end"]
    q = user_quality[chat_id]

    try:
        stream = get_stream_url(url, q)
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطأ في استخراج الفيديو:\n{e}")
        return

    bot.send_message(chat_id, "✂️ جاري قص الفيديو… قد يستغرق بعض الوقت")

    output = cut_video(stream, start, end)

    bot.send_message(chat_id, "📤 جاري إرسال الملف…")

    # إرسال كـ Document ليقبل الحجم الكبير
    with open(output, "rb") as f:
        bot.send_document(chat_id, f)

    os.remove(output)
    bot.send_message(chat_id, "✅ تم الإرسال بنجاح!", reply_markup=telebot.types.ReplyKeyboardRemove())

# تشغيل البوت
print("🔥 Bot is running…")
bot.infinity_polling(skip_pending=True)
