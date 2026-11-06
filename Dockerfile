FROM python:3.10-slim

# نصب پیش‌نیازها
RUN apt-get update && apt-get install -y \
    ffmpeg \
    nodejs \
    npm \
    curl \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# نصب ابزارهای WARP (wgcf و wireproxy)
RUN wget -O /usr/local/bin/wgcf https://github.com/ViRb3/wgcf/releases/download/v2.2.22/wgcf_2.2.22_linux_amd64 && \
    chmod +x /usr/local/bin/wgcf

RUN wget -O /tmp/wireproxy.tar.gz https://github.com/pufferffish/wireproxy/releases/download/v1.0.9/wireproxy_linux_amd64.tar.gz && \
    tar -xzf /tmp/wireproxy.tar.gz -C /usr/local/bin/ wireproxy && \
    chmod +x /usr/local/bin/wireproxy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# ساخت اسکریپت راه‌اندازی برای فعال‌سازی خودکار WARP قبل از روشن شدن ربات
RUN echo '#!/bin/bash\n\
if [ ! -f wgcf-account.toml ]; then\n\
    wgcf register --accept-tos\n\
fi\n\
wgcf generate\n\
echo -e "\n[Socks5]\nBindAddress = 127.0.0.1:25344" >> wgcf-profile.conf\n\
wireproxy -c wgcf-profile.conf &\n\
sleep 3\n\
python insta_bot.py\n\
' > /start.sh && chmod +x /start.sh

CMD ["/start.sh"]