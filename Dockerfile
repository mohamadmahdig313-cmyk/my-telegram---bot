FROM python:3.10-slim

# نصب پیش‌نیازهای سیستمی (ffmpeg برای چسباندن صدا و node برای یوتیوب)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# تنظیم پوشه کاری
WORKDIR /app

# کپی کردن فایل نیازمندی‌ها و نصب کتابخانه‌های پایتون
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کپی کردن بقیه فایل‌ها (کد ربات)
COPY . .

# دستور اجرای ربات
CMD ["python", "insta_bot.py"]
