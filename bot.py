import os
import aiohttp
import asyncio
from aiogram import Bot, Dispatcher, executor, types
from assemblyai import AssemblyAI

# ============ الإعدادات ============

TELEGRAM_TOKEN = os.getenv("TOKEN")
ASSEMBLY_KEY = os.getenv("ASSEMBLYAI_API_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ يجب وضع TOKEN في متغيرات البيئة!")

if not ASSEMBLY_KEY:
    raise RuntimeError("❌ يجب وضع ASSEMBLYAI_API_KEY في متغيرات البيئة!")

bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

client = AssemblyAI(api_key=ASSEMBLY_KEY)

TMP_DIR = "tmp_audio"
os.makedirs(TMP_DIR, exist_ok=True)

# ============ دالة التفريغ ============

async def transcribe_audio(file_path: str) -> str:
    """
    ترفع الملف لـ AssemblyAI وترجع النص.
    """
    # 1) رفع الملف
    upload_url = client.upload(file_path)

    # 2) إرسال طلب التفريغ
    transcript = client.transcribe(upload_url)

    # 3) انتظار انتهاء التفريغ
    transcript = client.wait_for_completion(transcript.id)

    if transcript.status == "completed":
        return transcript.text

    return "⚠️ لم أستطع استخراج النص من الصوت."


# ============ المعالجة الأساسية ============

async def handle_audio(message: types.Message, tg_file):
    msg = await message.answer("⏳ جاري التفريغ…")

    # حفظ الملف مؤقتاً
    file_name = f"{message.from_user.id}_{message.message_id}.mp3"
    file_path = os.path.join(TMP_DIR, file_name)

    try:
        await tg_file.download(destination=file_path)

        # تنفيذ التفريغ
        text = await transcribe_audio(file_path)

        await msg.edit_text(
            f"✅ <b>تفريغ الصوت:</b>\n\n{text}"
        )

    except Exception as e:
        await msg.edit_text("❌ حدث خطأ أثناء التفريغ.")
        print("TRANSCRIBE ERROR:", e)

    finally:
        # حذف الملف
        if os.path.exists(file_path):
            os.remove(file_path)


# ============ أنواع الرسائل المدعومة ============

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    await message.answer(
        "👋 مرحباً!\n\n"
        "🎙️ أرسل لي:\n"
        "• فويس\n"
        "• ملف صوتي\n"
        "• فيديو نوت\n\n"
        "وسأرجع لك النص مكتوبًا بإذن الله."
    )


@dp.message_handler(content_types=[types.ContentType.VOICE])
async def voice_handler(message: types.Message):
    await handle_audio(message, message.voice)


@dp.message_handler(content_types=[types.ContentType.AUDIO])
async def audio_handler(message: types.Message):
    await handle_audio(message, message.audio)


@dp.message_handler(content_types=[types.ContentType.VIDEO_NOTE])
async def video_note_handler(message: types.Message):
    await handle_audio(message, message.video_note)


# ============ تشغيل البوت ============

def main():
    print("🤖 Voice Transcriber Bot is running...")
    executor.start_polling(dp, skip_updates=True)


if __name__ == "__main__":
    main()
