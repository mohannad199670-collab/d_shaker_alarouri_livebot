import os
import logging
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

# ---------------------------
#     المتغيرات المطلوبة
# ---------------------------
TELEGRAM_TOKEN = os.getenv("TOKEN")
ASSEMBLYAI_KEY = os.getenv("ASSEMBLYAI_API_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ مفقود TOKEN في Koyeb")

if not ASSEMBLYAI_KEY:
    raise RuntimeError("❌ مفقود ASSEMBLYAI_API_KEY في Koyeb")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(bot)

logging.basicConfig(level=logging.INFO)

# ---------------------------
#  دالة رفع الملف لـ AssemblyAI
# ---------------------------
def upload_to_assemblyai(file_path: str):
    headers = {"authorization": ASSEMBLYAI_KEY}
    with open(file_path, "rb") as f:
        response = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers=headers,
            data=f
        )
    return response.json().get("upload_url")

# ---------------------------
#  دالة بدء التفريغ
# ---------------------------
def start_transcription(audio_url: str):
    endpoint = "https://api.assemblyai.com/v2/transcript"
    json_data = {"audio_url": audio_url}
    headers = {"authorization": ASSEMBLYAI_KEY}
    response = requests.post(endpoint, json=json_data, headers=headers)
    return response.json().get("id")

# ---------------------------
#  دالة جلب النص النهائي
# ---------------------------
def get_transcription_result(transcript_id: str):
    endpoint = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
    headers = {"authorization": ASSEMBLYAI_KEY}
    return requests.get(endpoint, headers=headers).json()

# ---------------------------
# استقبال صوت / فيديو
# ---------------------------
@dp.message_handler(content_types=[
    "voice", "audio", "video", "video_note"
])
async def handle_audio(message: types.Message):

    await message.reply("⏳ جاري معالجة الملف…")

    # تحميل الملف
    file_info = await bot.get_file(message.voice.file_id if message.voice else (
        message.audio.file_id if message.audio else (
            message.video.file_id if message.video else message.video_note.file_id
        )
    ))

    file_path = file_info.file_path
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"

    # تحميل الملف مؤقتًا
    file_data = requests.get(file_url)
    local_file = "temp_audio_file"

    with open(local_file, "wb") as f:
        f.write(file_data.content)

    # رفعه إلى AssemblyAI
    upload_url = upload_to_assemblyai(local_file)

    # بدء التفريغ
    transcript_id = start_transcription(upload_url)

    await message.reply("🎙️ جاري التفريغ… قد يستغرق 10–40 ثانية…")

    # الانتظار إلى أن يجهز النص
    while True:
        result = get_transcription_result(transcript_id)
        status = result.get("status")

        if status == "completed":
            text = result.get("text", "")
            return await message.reply(f"📝 التفريغ جاهز:\n\n{text}")

        elif status == "error":
            return await message.reply("❌ حدث خطأ أثناء التفريغ.")

# ---------------------------
# تشغيل البوت
# ---------------------------
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
