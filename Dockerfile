FROM python:3.11-slim

# تثبيت الأدوات الأساسية
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# تحديث yt-dlp
RUN pip install --upgrade yt-dlp

# تثبيت المكتبات
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ الكود
COPY . .

# تعريف JS runtime لـ yt-dlp
ENV YTDLP_JS_RUNTIME=node

CMD ["python", "bot.py"]
