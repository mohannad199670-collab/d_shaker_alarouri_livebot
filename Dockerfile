# هذا هو الجزء العلوي من ملف Dockerfile الخاص بك
FROM python:3.11-slim

# ... (الخطوات الأخرى مثل COPY و WORKDIR)

# تثبيت Node.js (وهو ما يوفر بيئة تشغيل JavaScript)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    # إضافة Node.js و npm
    nodejs \
    npm && \
    # تنظيف الكاش لتقليل حجم الصورة النهائية
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ... (بقية خطوات تثبيت المتطلبات وتشغيل الروبوت)
