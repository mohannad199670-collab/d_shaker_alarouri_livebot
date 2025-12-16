FROM python:3.11-slim

# تثبيت ffmpeg + ffprobe
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# مجلد العمل
WORKDIR /app

# نسخ الملفات
COPY . .

# تثبيت مكتبات بايثون
RUN pip install --no-cache-dir -r requirements.txt

# تشغيل البوت
CMD ["python", "bot.py"]
