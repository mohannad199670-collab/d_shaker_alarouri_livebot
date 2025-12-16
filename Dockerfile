FROM python:3.11-slim

# تثبيت ffmpeg + أدوات
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    unzip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# تثبيت deno (JavaScript runtime)
RUN curl -fsSL https://deno.land/install.sh | sh

# إضافة deno للـ PATH
ENV PATH="/root/.deno/bin:${PATH}"

WORKDIR /app
COPY . .
RUN pip install -r requirements.txt

CMD ["python", "bot.py"]
