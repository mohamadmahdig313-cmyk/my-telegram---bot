# -*- coding: utf-8 -*-
import os, re, json, time, threading, subprocess, urllib.request
import http.server
import socketserver
import requests, yt_dlp

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_URL = f"https://api.splus.ir/bot{BOT_TOKEN}" 

DOWNLOAD_DIR = "/tmp/yt_downloads" 
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# تنظیمات فیلترشکن Xray (پورت 10809)
XRAY_PROXY = "socks5://127.0.0.1:10809"

YT_RE = re.compile(r'(https?://)?(www\.)?(youtube\.com/(watch\?v=|shorts/)|youtu\.be/)[\w-]+')
IG_RE = re.compile(r'(https?://)?(www\.)?(instagram\.com/(p|reel|reels|tv)/[\w-]+)')

jobs, pending_links, pending_images, pending_send = {}, {}, {}, {}
DL_SLOTS = threading.Semaphore(2)

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        httpd.serve_forever()

def keep_alive_worker():
    time.sleep(60)
    url = os.environ.get("RENDER_EXTERNAL_URL", "https://my-telegram-bot-2v2u.onrender.com")
    while True:
        try:
            urllib.request.urlopen(url, timeout=10)
        except Exception:
            pass
        time.sleep(14 * 60)

def get_name(c): return (c.get("first_name") or c.get("username") or "دوست عزیز").strip()

FORMATS = {
    "🎬 360p": "bestvideo[height<=360]+bestaudio/best[height<=360]/best",
    "🎬 480p": "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
    "🎬 720p": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
    "🎬 1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    "🎵 فقط صدا": "bestaudio/best",
}

# ================= کیبوردها =================
def kb_inline_main_menu():
    return {"inline_keyboard": [[{"text": "📥 دانلود از یوتیوب", "callback_data": "menu_yt"}], [{"text": "📥 دانلود از اینستاگرام", "callback_data": "menu_ig"}]]}

def kb_inline_video_quality():
    return {"inline_keyboard": [[{"text": "🎬 360p", "callback_data": "qv|360"}, {"text": "🎬 480p", "callback_data": "qv|480"}], [{"text": "🎬 720p", "callback_data": "qv|720"}, {"text": "🎬 1080p", "callback_data": "qv|1080"}], [{"text": "🎵 فقط صدا", "callback_data": "qv|audio"}]]}

def kb_inline_image_quality():
    return {"inline_keyboard": [[{"text": "🌟 کیفیت اصلی", "callback_data": "qi|0"}], [{"text": "📱 1080p", "callback_data": "qi|1080"}, {"text": "💻 720p", "callback_data": "qi|720"}], [{"text": "📉 480p", "callback_data": "qi|480"}]]}

def kb_inline_send_choice(f_type):
    return {"inline_keyboard": [[{"text": "🎬 ارسال مستقیم", "callback_data": f"send|direct|{f_type}"}, {"text": "📁 ارسال فایل", "callback_data": f"send|file|{f_type}"}]]}

def kb_inline_rating():
    return {"inline_keyboard": [[{"text": "1 ⭐", "callback_data": "rate|1"}, {"text": "2 ⭐", "callback_data": "rate|2"}, {"text": "3 ⭐", "callback_data": "rate|3"}], [{"text": "4 ⭐", "callback_data": "rate|4"}, {"text": "5 ⭐", "callback_data": "rate|5"}]]}

# ================= API =================
def api_post(method, data=None, files=None, timeout=120):
    try:
        if files:
            clean = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in (data or {}).items()}
            return requests.post(f"{API_URL}/{method}", data=clean, files=files, timeout=timeout).json()
        return requests.post(f"{API_URL}/{method}", json=data or {}, timeout=timeout).json()
    except Exception as e:
        print(f"[api] {method}: {e}"); return {"ok": False}

def send_message(chat_id, text, kb=None):
    payload = {"chat_id": chat_id, "text": text}
    if kb: payload["reply_markup"] = json.dumps(kb, ensure_ascii=False)
    res = requests.post(f"{API_URL}/sendMessage", data=payload, timeout=15).json()
    return res["result"]["message_id"] if res.get("ok") else None

def edit_message(chat_id, mid, text, kb=None):
    if not mid: return
    payload = {"chat_id": chat_id, "message_id": mid, "text": text}
    if kb is not None: payload["reply_markup"] = json.dumps(kb, ensure_ascii=False)
    requests.post(f"{API_URL}/editMessageText", data=payload, timeout=10)

def answer_cb(cb_id, text):
    requests.post(f"{API_URL}/answerCallbackQuery", data={"callback_query_id": cb_id, "text": text})

def perform_send(chat_id, path, title, mode, is_video, is_audio):
    try:
        if is_audio:
            if mode == "direct":
                ogg_path = path.rsplit(".", 1)[0] + ".ogg"
                subprocess.run(["ffmpeg", "-y", "-i", path, "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "64k", ogg_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if os.path.exists(ogg_path):
                    with open(ogg_path, "rb") as f: res = api_post("sendVoice", {"chat_id": chat_id, "caption": title}, files={"voice": f}, timeout=300)
                    try: os.remove(ogg_path)
                    except: pass
                    if res.get("ok"): return
                with open(path, "rb") as f: api_post("sendAudio", {"chat_id": chat_id, "caption": title}, files={"audio": f}, timeout=300)
            else:
                with open(path, "rb") as f: api_post("sendDocument", {"chat_id": chat_id, "caption": title}, files={"document": f}, timeout=300)
        elif is_video:
            with open(path, "rb") as f:
                if mode == "direct": api_post("sendVideo", {"chat_id": chat_id, "caption": title, "supports_streaming": True}, files={"video": f}, timeout=600)
                else: api_post("sendDocument", {"chat_id": chat_id, "caption": title}, files={"document": f}, timeout=600)
        else:
            with open(path, "rb") as f:
                if mode == "direct": api_post("sendPhoto", {"chat_id": chat_id, "caption": title}, files={"photo": f}, timeout=120)
                else: api_post("sendDocument", {"chat_id": chat_id, "caption": title}, files={"document": f}, timeout=120)
    except Exception as e: print(f"perform_send error: {e}")

def extract_image_from_post(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        m = re.search(r'<meta property="og:image" content="([^"]+)"', r.text)
        return m.group(1) if m else None
    except: return None

def apply_image_quality(url, size_str):
    if size_str == "0": return url
    if re.search(r'=s\d+', url): return re.sub(r'=s\d+', f'=s{size_str}', url)
    elif re.search(r'=w\d+-h\d+', url): return re.sub(r'=w\d+-h\d+', f'=s{size_str}', url)
    elif re.search(r'=w\d+', url): return re.sub(r'=w\d+', f'=w{size_str}', url)
    return url + f"=s{size_str}" if "?" not in url else url + f"&size=s{size_str}"

class Cancelled(Exception): pass
class Job:
    def __init__(self):
        self.cancel = threading.Event(); self.pause = threading.Event(); self.pause.set()
        self.message_id = None; self.last_text = ""; self.progress = None; self.title = ""; self.status = "downloading"
    def is_cancelled(self): return self.cancel.is_set()
    def is_paused(self): return not self.pause.is_set()
    def check(self):
        while not self.pause.is_set():
            if self.cancel.is_set(): raise Cancelled()
            time.sleep(0.1)
        if self.cancel.is_set(): raise Cancelled()

class YtdlpLogger:
    def __init__(self, job): self.job = job
    def debug(self, msg): self.job.check()
    def info(self, msg): self.job.check()
    def warning(self, msg): self.job.check()
    def error(self, msg): self.job.check()

def fetch_info(chat_id, url, name):
    msg_id = send_message(chat_id, f"🔍 {name} جان، در حال بررسی لینک...")
    try:
        opts_check = {
            "quiet": True, "noplaylist": True, "socket_timeout": 15, "retries": 2,
            "proxy": XRAY_PROXY,  # <--- استفاده از فیلترشکن Xray
        }
        with yt_dlp.YoutubeDL(opts_check) as ydl: info = ydl.extract_info(url, download=False)
        pending_links[chat_id] = {"url": url, "title": info.get("title", "بدون عنوان"), "thumb_url": info.get("thumbnail")}
        requests.post(f"{API_URL}/deleteMessage", data={"chat_id": chat_id, "message_id": msg_id})
        caption = f"🎬 **{info.get('title')}**\n\n👇 کیفیت مورد نظر را انتخاب کنید:"
        if info.get("thumbnail"): requests.post(f"{API_URL}/sendPhoto", data={"chat_id": chat_id, "photo": info.get("thumbnail"), "caption": caption}, timeout=30)
        else: send_message(chat_id, caption)
        send_message(chat_id, "منتظر انتخاب شما هستم...", kb=kb_inline_video_quality())
    except Exception as e:
        edit_message(chat_id, msg_id, f"⚠️ خطا: {e}")

def process_image_download(chat_id, url, size_str, label):
    send_message(chat_id, f"🔍 در حال دانلود عکس ({label})...")
    img_url = extract_image_from_post(url)
    if not img_url: return send_message(chat_id, "❌ عکسی پیدا نشد.")
    try:
        data = requests.get(apply_image_quality(img_url, size_str), timeout=30).content
        if len(data) < 1000: return send_message(chat_id, "❌ عکس خرابه.")
        user_dir = os.path.join(DOWNLOAD_DIR, str(chat_id))
        os.makedirs(user_dir, exist_ok=True)
        path = os.path.join(user_dir, "post_image.jpg")
        with open(path, "wb") as f: f.write(data)
        send_message(chat_id, f"✅ دانلود شد!\n🖼 **عکس ({label})**\n\nچطور بفرستم؟", kb=kb_inline_send_choice("photo"))
        pending_send[chat_id] = {"path": path, "title": f"🖼 عکس ({label})", "is_video": False, "is_audio": False, "state": "ask_mode"}
    except Exception as e: send_message(chat_id, f"❌ خطا: {e}")

def progress_loop(chat_id, job):
    while job.status == "downloading":
        d = job.progress
        if job.is_paused(): text = "⏸ متوقف شد"
        elif d and d.get("status") == "finished": text = "✅ آماده‌سازی... 📤"
        elif d:
            eta = d.get("eta")
            if not eta:
                t = d.get("total_bytes") or d.get("total_bytes_estimate"); done = d.get("downloaded_bytes", 0); sp = d.get("speed")
                if t and sp and sp > 0: eta = int((t - done) / sp)
            text = f"⏳ {eta}s مانده ({d.get('_percent_str','').strip()})" if eta and eta > 0 else f"⏳ {d.get('_percent_str','').strip()}"
        else: text = "⏳ آماده‌سازی..."
        if text != job.last_text: edit_message(chat_id, job.message_id, text); job.last_text = text
        time.sleep(1)

def run_download(chat_id, url, format_str, is_audio_only):
    job = jobs.get(chat_id)
    if not job: return
    def hook(d): job.check(); job.progress = d
    
    user_dir = os.path.join(DOWNLOAD_DIR, str(chat_id))
    os.makedirs(user_dir, exist_ok=True)

    opts = {
        "outtmpl": os.path.join(user_dir, "%(id)s.%(ext)s"), "format": format_str,
        "quiet": True, "noplaylist": True, "progress_hooks": [hook], "logger": YtdlpLogger(job),
        "proxy": XRAY_PROXY,  # <--- استفاده از فیلترشکن Xray
        "retries": 5, "socket_timeout": 30, "merge_output_format": "mp4",
    }
    if is_audio_only: opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]
    
    with DL_SLOTS:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl: info = ydl.extract_info(url, download=True)
        except Cancelled:
            job.status = "cancelled"; jobs.pop(chat_id, None); send_message(chat_id, "❌ لغو شد."); return
        except Exception as e:
            job.status = "cancelled"; jobs.pop(chat_id, None); edit_message(chat_id, job.message_id, f"⚠️ خطا: {e}"); return
    
    job.status = "finished"
    while job.is_paused():
        if job.is_cancelled(): return
        time.sleep(0.5)
    jobs.pop(chat_id, None)
    
    files = [os.path.join(user_dir, f) for f in os.listdir(user_dir) if f.startswith(info.get("id")) and f.rsplit(".",1)[-1].lower() in ("mp4","mkv","webm","m4a","mp3","opus","ogg","jpg","jpeg","png","webp")]
    if not files: return send_message(chat_id, "❌ فایل پیدا نشد.")
    
    job.title = info.get("title", "فایل")
    edit_message(chat_id, job.message_id, f"✅ دانلود شد 🎉")
    
    videos = [p for p in files if p.rsplit(".",1)[-1].lower() in ("mp4","mkv","webm")]
    photos = [p for p in files if p.rsplit(".",1)[-1].lower() in ("jpg","jpeg","png","webp")]
    
    if is_audio_only:
        send_message(chat_id, f"🎵 **{job.title}**\n\nچطور بفرستم؟", kb=kb_inline_send_choice("audio"))
        pending_send[chat_id] = {"path": files[0], "title": job.title, "is_video": False, "is_audio": True, "state": "ask_mode"}
    elif videos:
        send_message(chat_id, f"🎬 **{job.title}**\n\nچطور بفرستم؟", kb=kb_inline_send_choice("video"))
        pending_send[chat_id] = {"path": videos[0], "title": job.title, "is_video": True, "is_audio": False, "state": "ask_mode"}
    elif photos:
        send_message(chat_id, f"🖼 **{job.title}**\n\nچطور بفرستم؟", kb=kb_inline_send_choice("photo"))
        pending_send[chat_id] = {"path": photos[0], "title": job.title, "is_video": False, "is_audio": False, "state": "ask_mode"}

def handle_callback(cb):
    try:
        chat_id = cb["message"]["chat"]["id"]; mid = cb["message"]["message_id"]; data = cb.get("data", "")
    except: return
    
    if data == "menu_yt":
        answer_cb(cb["id"], "لینک یوتیوب را بفرستید")
        edit_message(chat_id, mid, "🎬 لطفاً لینک یوتیوب خود را ارسال کنید:")
    elif data == "menu_ig":
        answer_cb(cb["id"], "لینک اینستاگرام را بفرستید")
        edit_message(chat_id, mid, "📸 لطفاً لینک اینستاگرام خود را ارسال کنید:")
    elif data.startswith("qv|"):
        q = data.split("|")[1]
        q_map = {"360": "🎬 360p", "480": "🎬 480p", "720": "🎬 720p", "1080": "🎬 1080p", "audio": "🎵 فقط صدا"}
        if chat_id in pending_links:
            info = pending_links.pop(chat_id); jobs[chat_id] = Job()
            jobs[chat_id].message_id = send_message(chat_id, f"⏳ دانلود {q_map[q]} شروع شد...")
            threading.Thread(target=progress_loop, args=(chat_id, jobs[chat_id]), daemon=True).start()
            threading.Thread(target=run_download, args=(chat_id, info["url"], FORMATS[q_map[q]], q=="audio"), daemon=True).start()
            answer_cb(cb["id"], f"شروع دانلود {q_map[q]}")
    elif data.startswith("qi|"):
        q = data.split("|")[1]
        if chat_id in pending_images:
            url = pending_images.pop(chat_id)
            q_map = {"0": "🌟 کیفیت اصلی", "1080": "📱 1080p", "720": "💻 720p", "480": "📉 480p"}
            answer_cb(cb["id"], "در حال دانلود عکس...")
            threading.Thread(target=process_image_download, args=(chat_id, url, q, q_map[q]), daemon=True).start()
    elif data.startswith("send|"):
        parts = data.split("|")
        mode, f_type = parts[1], parts[2]
        if chat_id in pending_send:
            p = pending_send.pop(chat_id)
            answer_cb(cb["id"], "در حال ارسال...")
            threading.Thread(target=perform_send, args=(chat_id, p["path"], p["title"], mode, p["is_video"], p["is_audio"]), daemon=True).start()
            time.sleep(1)
            send_message(chat_id, "✨ فایل ارسال شد!\n\n⭐ کیفیت کار بات را ۱ تا ۵ امتیاز دهید:", kb=kb_inline_rating())
            try: 
                os.remove(p["path"])
                os.rmdir(os.path.dirname(p["path"]))
            except: pass
    elif data.startswith("rate|"):
        score = data.split("|")[1]
        answer_cb(cb["id"], "ممنون از امتیاز شما!")
        edit_message(chat_id, mid, f"✅ امتیاز شما ({score} ⭐) ثبت شد! ممنون 🙏")

def handle_message(msg):
    chat_id = msg["chat"]["id"]; name = get_name(msg["chat"]); text = msg.get("text", "").strip()

    if text.startswith("/start") or text.startswith("/help"):
        send_message(chat_id, f"سلام {name} جان! 🌹✨\n\n🤖 من بات دانلودر هستم!\nلطفاً یک گزینه را انتخاب کنید یا لینک خود را بفرستید:", kb=kb_inline_main_menu())
        return

    if text == "/cancel" or text == "❌ لغو":
        if chat_id in jobs: jobs[chat_id].cancel.set(); jobs[chat_id].pause.set(); send_message(chat_id, "❌ لغو شد.")
        else: send_message(chat_id, "🤷 دانلودی نیست.")
        return

    if "youtube.com/post/" in text:
        pending_images[chat_id] = text; send_message(chat_id, "🖼 کیفیت عکس را انتخاب کنید:", kb=kb_inline_image_quality()); return

    if YT_RE.search(text) or IG_RE.search(text):
        if chat_id in jobs: send_message(chat_id, "⚠️ یه دانلود فعال داری!"); return
        threading.Thread(target=fetch_info, args=(chat_id, text, name), daemon=True).start(); return

    send_message(chat_id, "🤔 متوجه نشدم! لینک بفرست یا /start بزن.")

def main():
    print("🤖 بات آماده است...")
    threading.Thread(target=run_dummy_server, daemon=True).start()
    threading.Thread(target=keep_alive_worker, daemon=True).start()
    
    res = requests.get(f"{API_URL}/getUpdates", params={"offset": -1, "timeout": 0}).json()
    offset = (res["result"][-1]["update_id"] + 1) if (res.get("ok") and res.get("result")) else None
    if offset: requests.get(f"{API_URL}/getUpdates", params={"offset": offset, "timeout": 0})
    while True:
        try:
            params = {"timeout": 25}
            if offset: params["offset"] = offset
            res = requests.get(f"{API_URL}/getUpdates", params=params, timeout=35).json()
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                if "callback_query" in upd: handle_callback(upd["callback_query"])
                elif "message" in upd: handle_message(upd["message"])
        except requests.exceptions.RequestException as e: print(f"⚠️ Network Error: {e}"); time.sleep(5)
        except Exception as e: print(f"❌ Error: {e}"); time.sleep(2)

if __name__ == "__main__": main()