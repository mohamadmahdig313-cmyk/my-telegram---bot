# -*- coding: utf-8 -*-
"""
بات دانلودر یوتیوب و اینستاگرام برای سروش (Splus)
نسخه بهبودیافته - رفع باگ‌های race condition، thread-safety و پایداری
"""
import os
import re
import json
import time
import logging
import threading
import subprocess
import urllib.request
import http.server
import socketserver
from pathlib import Path

import requests
import yt_dlp

# ================= تنظیمات =================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_URL = f"https://api.splus.ir/bot{BOT_TOKEN}"
DOWNLOAD_DIR = Path("/tmp/yt_downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

XRAY_PROXY = "socks5://127.0.0.1:10809"

# لاگ
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("bot")

# الگوهای لینک
YT_RE = re.compile(
    r"(https?://)?(www\.)?(youtube\.com/(watch\?v=|shorts/)|youtu\.be/)[\w-]+",
    re.IGNORECASE
)
IG_RE = re.compile(
    r"(https?://)?(www\.)?(instagram\.com/(p|reel|reels|tv)/[\w-]+)",
    re.IGNORECASE
)

# state مشترک با قفل
_lock = threading.RLock()
jobs = {}
pending_links = {}
pending_images = {}
pending_send = {}
DL_SLOTS = threading.Semaphore(2)

FORMATS = {
    "🎬 360p": "bestvideo[height<=360]+bestaudio/best[height<=360]/best",
    "🎬 480p": "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
    "🎬 720p": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
    "🎬 1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    "🎵 فقط صدا": "bestaudio/best",
}

# ================= کیبوردها =================
def kb_inline_main_menu():
    return {
        "inline_keyboard": [
            [{"text": "📥 دانلود از یوتیوب", "callback_data": "menu_yt"}],
            [{"text": "📥 دانلود از اینستاگرام", "callback_data": "menu_ig"}],
        ]
    }

def kb_inline_video_quality(sizes=None):
    sizes = sizes or {}
    return {
        "inline_keyboard": [
            [
                {"text": f"🎬 360p {sizes.get('360p', '')}", "callback_data": "qv|360"},
                {"text": f"🎬 480p {sizes.get('480p', '')}", "callback_data": "qv|480"},
            ],
            [
                {"text": f"🎬 720p {sizes.get('720p', '')}", "callback_data": "qv|720"},
                {"text": f"🎬 1080p {sizes.get('1080p', '')}", "callback_data": "qv|1080"},
            ],
            [{"text": f"🎵 فقط صدا {sizes.get('audio', '')}", "callback_data": "qv|audio"}],
        ]
    }

def kb_inline_image_quality():
    return {
        "inline_keyboard": [
            [{"text": "🌟 کیفیت اصلی", "callback_data": "qi|0"}],
            [
                {"text": "📱 1080p", "callback_data": "qi|1080"},
                {"text": "💻 720p", "callback_data": "qi|720"},
            ],
            [{"text": "📉 480p", "callback_data": "qi|480"}],
        ]
    }

def kb_inline_send_choice(f_type):
    return {
        "inline_keyboard": [
            [
                {"text": "🎬 ارسال مستقیم", "callback_data": f"send|direct|{f_type}"},
                {"text": "📁 ارسال فایل", "callback_data": f"send|file|{f_type}"},
            ]
        ]
    }

def kb_inline_rating():
    return {
        "inline_keyboard": [
            [
                {"text": "1 ⭐", "callback_data": "rate|1"},
                {"text": "2 ⭐", "callback_data": "rate|2"},
                {"text": "3 ⭐", "callback_data": "rate|3"},
            ],
            [
                {"text": "4 ⭐", "callback_data": "rate|4"},
                {"text": "5 ⭐", "callback_data": "rate|5"},
            ],
        ]
    }

# ================= API =================
def api_post(method, data=None, files=None, timeout=120):
    try:
        if files:
            clean = {
                k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                for k, v in (data or {}).items()
            }
            r = requests.post(f"{API_URL}/{method}", data=clean, files=files, timeout=timeout)
        else:
            r = requests.post(f"{API_URL}/{method}", json=data or {}, timeout=timeout)
        return r.json()
    except Exception as e:
        log.error(f"api {method}: {e}")
        return {"ok": False}

def send_message(chat_id, text, kb=None):
    payload = {"chat_id": chat_id, "text": text}
    if kb:
        payload["reply_markup"] = json.dumps(kb, ensure_ascii=False)
    res = requests.post(f"{API_URL}/sendMessage", data=payload, timeout=15).json()
    return res["result"]["message_id"] if res.get("ok") else None

def edit_message(chat_id, mid, text, kb=None):
    if not mid:
        return
    payload = {"chat_id": chat_id, "message_id": mid, "text": text}
    if kb is not None:
        payload["reply_markup"] = json.dumps(kb, ensure_ascii=False)
    try:
        requests.post(f"{API_URL}/editMessageText", data=payload, timeout=10)
    except Exception as e:
        log.warning(f"edit_message: {e}")

def answer_cb(cb_id, text=""):
    try:
        requests.post(
            f"{API_URL}/answerCallbackQuery",
            data={"callback_query_id": cb_id, "text": text},
            timeout=5,
        )
    except Exception:
        pass

def delete_message(chat_id, mid):
    if mid:
        try:
            requests.post(
                f"{API_URL}/deleteMessage",
                data={"chat_id": chat_id, "message_id": mid},
                timeout=5,
            )
        except Exception:
            pass

# ================= ارسال فایل (با پاکسازی امن) =================
def perform_send(chat_id, path, title, mode, is_video, is_audio):
    path = Path(path)
    try:
        if not path.exists():
            send_message(chat_id, "❌ فایل پیدا نشد (احتمالاً پاک شده).")
            return

        if is_audio:
            if mode == "direct":
                ogg_path = path.with_suffix(".ogg")
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", str(path),
                        "-ac", "1", "-ar", "48000",
                        "-c:a", "libopus", "-b:a", "64k",
                        str(ogg_path),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=120,
                )
                if ogg_path.exists():
                    with open(ogg_path, "rb") as f:
                        res = api_post(
                            "sendVoice",
                            {"chat_id": chat_id, "caption": title},
                            files={"voice": f},
                            timeout=300,
                        )
                    try:
                        ogg_path.unlink()
                    except Exception:
                        pass
                    if res.get("ok"):
                        return
                with open(path, "rb") as f:
                    api_post(
                        "sendAudio",
                        {"chat_id": chat_id, "caption": title},
                        files={"audio": f},
                        timeout=300,
                    )
            else:
                with open(path, "rb") as f:
                    api_post(
                        "sendDocument",
                        {"chat_id": chat_id, "caption": title},
                        files={"document": f},
                        timeout=300,
                    )
        elif is_video:
            with open(path, "rb") as f:
                if mode == "direct":
                    api_post(
                        "sendVideo",
                        {
                            "chat_id": chat_id,
                            "caption": title,
                            "supports_streaming": True,
                        },
                        files={"video": f},
                        timeout=600,
                    )
                else:
                    api_post(
                        "sendDocument",
                        {"chat_id": chat_id, "caption": title},
                        files={"document": f},
                        timeout=600,
                    )
        else:
            with open(path, "rb") as f:
                if mode == "direct":
                    api_post(
                        "sendPhoto",
                        {"chat_id": chat_id, "caption": title},
                        files={"photo": f},
                        timeout=120,
                    )
                else:
                    api_post(
                        "sendDocument",
                        {"chat_id": chat_id, "caption": title},
                        files={"document": f},
                        timeout=120,
                    )
    except Exception as e:
        log.error(f"perform_send error: {e}")
        send_message(chat_id, f"⚠️ خطا در ارسال فایل: {e}")
    finally:
        try:
            if path.exists():
                path.unlink()
            parent = path.parent
            if parent.exists() and parent != DOWNLOAD_DIR and not any(parent.iterdir()):
                parent.rmdir()
        except Exception as e:
            log.warning(f"cleanup: {e}")

# ================= ابزارهای کمکی =================
def get_name(c):
    return (c.get("first_name") or c.get("username") or "دوست عزیز").strip()

def extract_image_from_post(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        m = re.search(r'<meta property="og:image" content="([^"]+)"', r.text)
        return m.group(1) if m else None
    except Exception:
        return None

def apply_image_quality(url, size_str):
    if size_str == "0":
        return url
    if re.search(r"=s\d+", url):
        return re.sub(r"=s\d+", f"=s{size_str}", url)
    if re.search(r"=w\d+-h\d+", url):
        return re.sub(r"=w\d+-h\d+", f"=s{size_str}", url)
    if re.search(r"=w\d+", url):
        return re.sub(r"=w\d+", f"=w{size_str}", url)
    return url + f"=s{size_str}" if "?" not in url else url + f"&size=s{size_str}"

# ================= Job =================
class Cancelled(Exception):
    pass

class Job:
    def __init__(self):
        self.cancel = threading.Event()
        self.pause = threading.Event()
        self.pause.set()
        self.message_id = None
        self.last_text = ""
        self.progress = None
        self.title = ""
        self.status = "downloading"

    def is_cancelled(self):
        return self.cancel.is_set()

    def is_paused(self):
        return not self.pause.is_set()

    def check(self):
        while not self.pause.is_set():
            if self.cancel.is_set():
                raise Cancelled()
            time.sleep(0.1)
        if self.cancel.is_set():
            raise Cancelled()

class YtdlpLogger:
    def __init__(self, job):
        self.job = job

    def debug(self, msg):
        self.job.check()

    def info(self, msg):
        self.job.check()

    def warning(self, msg):
        self.job.check()

    def error(self, msg):
        self.job.check()

# ================= دانلود =================
def fetch_info(chat_id, url, name):
    msg_id = send_message(chat_id, f"🔍 {name} جان، در حال بررسی لینک...")
    try:
        opts_check = {
            "quiet": True,
            "noplaylist": True,
            "socket_timeout": 20,
            "retries": 3,
            "proxy": XRAY_PROXY,
        }
        with yt_dlp.YoutubeDL(opts_check) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = info.get("formats") or []
        sizes = {"360p": "", "480p": "", "720p": "", "1080p": "", "audio": ""}

        def fmt_size(b):
            if not b:
                return ""
            return f"({b / (1024 * 1024):.1f}MB)"

        best_audio = next(
            (f for f in reversed(formats) if f.get("acodec") != "none" and f.get("vcodec") == "none"),
            None,
        )
        a_size = (best_audio.get("filesize") or best_audio.get("filesize_approx") or 0) if best_audio else 0
        if a_size:
            sizes["audio"] = fmt_size(a_size)

        for q in [360, 480, 720, 1080]:
            v_fmt = next(
                (f for f in reversed(formats) if f.get("height") == q and f.get("vcodec") != "none"),
                None,
            )
            if v_fmt:
                v_size = v_fmt.get("filesize") or v_fmt.get("filesize_approx") or 0
                if v_size:
                    sizes[f"{q}p"] = fmt_size(v_size + (a_size or 0))

        with _lock:
            pending_links[chat_id] = {
                "url": url,
                "title": info.get("title", "بدون عنوان"),
                "thumb_url": info.get("thumbnail"),
            }

        delete_message(chat_id, msg_id)

        caption = (
            f"🎬 **{info.get('title')}**\n\n"
            "👇 کیفیت مورد نظر را انتخاب کنید:\n"
            "*(حجم‌ها تقریبی هستند)*"
        )
        if info.get("thumbnail"):
            try:
                requests.post(
                    f"{API_URL}/sendPhoto",
                    data={
                        "chat_id": chat_id,
                        "photo": info.get("thumbnail"),
                        "caption": caption,
                    },
                    timeout=30,
                )
            except Exception:
                send_message(chat_id, caption)
        else:
            send_message(chat_id, caption)

        send_message(chat_id, "منتظر انتخاب شما هستم...", kb=kb_inline_video_quality(sizes))
    except Exception as e:
        log.error(f"fetch_info: {e}")
        edit_message(chat_id, msg_id, f"⚠️ خطا در بررسی لینک:\n{e}")

def process_image_download(chat_id, url, size_str, label):
    send_message(chat_id, f"🔍 در حال دانلود عکس ({label})...")
    img_url = extract_image_from_post(url)
    if not img_url:
        send_message(chat_id, "❌ عکسی پیدا نشد.")
        return
    try:
        data = requests.get(apply_image_quality(img_url, size_str), timeout=30).content
        if len(data) < 1000:
            send_message(chat_id, "❌ عکس خراب است.")
            return
        user_dir = DOWNLOAD_DIR / str(chat_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        path = user_dir / "post_image.jpg"
        path.write_bytes(data)

        send_message(
            chat_id,
            f"✅ دانلود شد!\n🖼 **عکس ({label})**\n\nچطور بفرستم؟",
            kb=kb_inline_send_choice("photo"),
        )
        with _lock:
            pending_send[chat_id] = {
                "path": str(path),
                "title": f"🖼 عکس ({label})",
                "is_video": False,
                "is_audio": False,
            }
    except Exception as e:
        log.error(f"process_image: {e}")
        send_message(chat_id, f"❌ خطا: {e}")

def progress_loop(chat_id, job):
    while job.status == "downloading":
        d = job.progress
        if job.is_paused():
            text = "⏸ متوقف شد"
        elif d and d.get("status") == "finished":
            text = "✅ آماده‌سازی... 📤"
        elif d:
            eta = d.get("eta")
            if not eta:
                t = d.get("total_bytes") or d.get("total_bytes_estimate")
                done = d.get("downloaded_bytes", 0)
                sp = d.get("speed")
                if t and sp and sp > 0:
                    eta = int((t - done) / sp)
            pct = (d.get("_percent_str") or "").strip()
            text = f"⏳ {eta}s مانده ({pct})" if eta and eta > 0 else f"⏳ {pct}"
        else:
            text = "⏳ آماده‌سازی..."
        if text != job.last_text:
            edit_message(chat_id, job.message_id, text)
            job.last_text = text
        time.sleep(1.2)

def run_download(chat_id, url, format_str, is_audio_only):
    with _lock:
        job = jobs.get(chat_id)
    if not job:
        return

    def hook(d):
        job.check()
        job.progress = d

    user_dir = DOWNLOAD_DIR / str(chat_id)
    user_dir.mkdir(parents=True, exist_ok=True)

    opts = {
        "outtmpl": str(user_dir / "%(id)s.%(ext)s"),
        "format": format_str,
        "quiet": True,
        "noplaylist": True,
        "progress_hooks": [hook],
        "logger": YtdlpLogger(job),
        "proxy": XRAY_PROXY,
        "retries": 5,
        "socket_timeout": 30,
        "merge_output_format": "mp4",
    }
    if is_audio_only:
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}
        ]

    info = None
    with DL_SLOTS:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Cancelled:
            job.status = "cancelled"
            with _lock:
                jobs.pop(chat_id, None)
            send_message(chat_id, "❌ دانلود لغو شد.")
            return
        except Exception as e:
            log.error(f"download error: {e}")
            job.status = "cancelled"
            with _lock:
                jobs.pop(chat_id, None)
            edit_message(chat_id, job.message_id, f"⚠️ خطا در دانلود:\n{e}")
            return

    job.status = "finished"
    while job.is_paused():
        if job.is_cancelled():
            return
        time.sleep(0.5)

    with _lock:
        jobs.pop(chat_id, None)

    if not info:
        send_message(chat_id, "❌ اطلاعات فایل دریافت نشد.")
        return

    vid = info.get("id") or ""
    files = [
        p
        for p in user_dir.iterdir()
        if p.name.startswith(vid)
        and p.suffix.lower() in (
            ".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".opus", ".ogg",
            ".jpg", ".jpeg", ".png", ".webp"
        )
    ]
    if not files:
        send_message(chat_id, "❌ فایل دانلودشده پیدا نشد.")
        return

    job.title = info.get("title", "فایل")
    edit_message(chat_id, job.message_id, "✅ دانلود شد 🎉")

    videos = [p for p in files if p.suffix.lower() in (".mp4", ".mkv", ".webm")]
    photos = [p for p in files if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]

    with _lock:
        if is_audio_only:
            send_message(
                chat_id,
                f"🎵 **{job.title}**\n\nچطور بفرستم؟",
                kb=kb_inline_send_choice("audio"),
            )
            pending_send[chat_id] = {
                "path": str(files[0]),
                "title": job.title,
                "is_video": False,
                "is_audio": True,
            }
        elif videos:
            send_message(
                chat_id,
                f"🎬 **{job.title}**\n\nچطور بفرستم؟",
                kb=kb_inline_send_choice("video"),
            )
            pending_send[chat_id] = {
                "path": str(videos[0]),
                "title": job.title,
                "is_video": True,
                "is_audio": False,
            }
        elif photos:
            send_message(
                chat_id,
                f"🖼 **{job.title}**\n\nچطور بفرستم؟",
                kb=kb_inline_send_choice("photo"),
            )
            pending_send[chat_id] = {
                "path": str(photos[0]),
                "title": job.title,
                "is_video": False,
                "is_audio": False,
            }

# ================= هندلرها =================
def handle_callback(cb):
    try:
        chat_id = cb["message"]["chat"]["id"]
        mid = cb["message"]["message_id"]
        data = cb.get("data", "")
    except Exception:
        return

    if data == "menu_yt":
        answer_cb(cb["id"], "لینک یوتیوب را بفرستید")
        edit_message(chat_id, mid, "🎬 لطفاً لینک یوتیوب خود را ارسال کنید:")
    elif data == "menu_ig":
        answer_cb(cb["id"], "لینک اینستاگرام را بفرستید")
        edit_message(chat_id, mid, "📸 لطفاً لینک اینستاگرام خود را ارسال کنید:")
    elif data.startswith("qv|"):
        q = data.split("|")[1]
        q_map = {
            "360": "🎬 360p",
            "480": "🎬 480p",
            "720": "🎬 720p",
            "1080": "🎬 1080p",
            "audio": "🎵 فقط صدا",
        }
        with _