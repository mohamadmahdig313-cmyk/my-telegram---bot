FROM python:3.10-slim

# نصب پیش‌نیازها
RUN apt-get update && apt-get install -y \
    ffmpeg \
    nodejs \
    npm \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# نصب هسته Xray (فیلترشکن)
RUN curl -L -o xray.zip https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip && \
    unzip xray.zip && \
    chmod +x xray && \
    mv xray /usr/local/bin/

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# اجرای فیلترشکن Xray با فایل کانفیگ و سپس ربات
CMD xray run -c config.json & python insta_bot.py