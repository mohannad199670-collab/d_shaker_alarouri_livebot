import os
import requests
from telegram.ext import Updater, MessageHandler, Filters, CommandHandler

# ------------------------------
#  قراءة المتغيّرات من Koyeb
# ------------------------------
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
ASSEMBLY_API = os.getenv("ASSEMBLYAI_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("❌ يجب وضع TELEGRAM_TOKEN في متغيرات البيئة")

if not ASSEMBLY_API:
    raise RuntimeError("❌ يجب وضع ASSEMBLYAI_API_KEY في متغيرات البيئة")

# ------------------------------
#  دالة الترحيب
# ------------------------------
def start(update, context):
    update.message.reply_text(
        "🎙️ أهلاً بك!\n"
        "أرسل لي أي *رسالة صوتية* أو *مقطع صوت* أو *فيديو* وسأقوم بتحويله إلى نص مكتوب 📄🔥"
    )

# ------------------------------
#  تحميل الملف من تليجرام
# ------------------------------
def download_file(file_id, bot):
    file = bot.get_file(file_id)
    file_path = "audio_input.ogg"
    file.download(file_path)
    return file_path

# ------------------------------
#  رفع الملف إلى AssemblyAI
# ------------------------------
def upload_to_assemblyai(file_path):
    headers = {"authorization": ASSEMBLY_API}
    with open(file_path, "rb") as f:
        response = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers=headers,
            data=f
        )
    return response.json()["upload_url"]

# ------------------------------
#  طلب التفريغ من AssemblyAI
# ------------------------------
def transcribe_audio(url):
    endpoint = "https://api.assemblyai.com/v2/transcript"
    json_data = {"audio_url": url, "language_code": "ar"}
    headers = {"authorization": ASSEMBLY_API}

    response = requests.post(endpoint, json=json_data, headers=headers)
    transcript_id = response.json()["id"]

    # الانتظار حتى يجهز التفريغ
    while True:
        status = requests.get(
            endpoint + "/" + transcript_id,
            headers=headers
        ).json()

        if status["status"] == "completed":
            return status["text"]

        if status["status"] == "error":
            return "❌ حدث خطأ أثناء التفريغ."

# ------------------------------
#  استقبال الملفات الصوتية
# ------------------------------
def handle_audio(update, context):
    bot = context.bot

    update.message.reply_text("⏳ جاري التفريغ... انتظر قليلاً 🔥")

    # اختيار نوع الملف
    file = None
    if update.message.voice:
        file = update.message.voice.file_id
    elif update.message.audio:
        file = update.message.audio.file_id
    elif update.message.video_note:
        file = update.message.video_note.file_id
    elif update.message.video:
        file = update.message.video.file_id
    else:
        update.message.reply_text("❌ الرجاء إرسال مقطع صوتي أو فيديو.")
        return

    file_path = download_file(file, bot)
    audio_url = upload_to_assemblyai(file_path)
    text = transcribe_audio(audio_url)

    update.message.reply_text("📄 *النص المستخرج:*\n\n" + text)


# ------------------------------
#  تشغيل البوت
# ------------------------------
def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.all, handle_audio))

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
