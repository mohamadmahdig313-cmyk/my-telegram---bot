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

# ساخت فایل تنظیمات Xray با کانفیگ VLESS (WebSocket) جدید
RUN echo '{\
  "log": {"loglevel": "warning"},\
  "inbounds": [{"port": 10809, "listen": "127.0.0.1", "protocol": "socks", "settings": {"udp": true}}],\
  "outbounds": [\
    {\
      "protocol": "vless",\
      "settings": {\
        "vnext": [{\
          "address": "146.103.118.189",\
          "port": 443,\
          "users": [{"id": "ce698f0c-a8ec-474a-b5d8-3be8032deeb8", "encryption": "none"}]\
        }]\
      },\
      "streamSettings": {\
        "network": "ws",\
        "security": "tls",\
        "tlsSettings": {"serverName": "nl2.574ck.xyz", "allowInsecure": false, "fingerprint": "firefox"},\
        "wsSettings": {"path": "/574ck-finland", "headers": {"Host": "nl2.574ck.xyz"}}\
      }\
    }\
  ]\
}' > config.json

# اجرای فیلترشکن Xray در پس‌زمینه و سپس اجرای ربات
CMD xray run -c config.json & python insta_bot.py