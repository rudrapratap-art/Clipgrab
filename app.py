import os
import shutil
import tempfile
import re
import time
import threading
import uuid
import secrets
import smtplib
import socket
import ipaddress
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone

from flask import Flask, request, render_template, redirect, session, url_for
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp
from dotenv import load_dotenv
from supabase import create_client, Client
from werkzeug.security import generate_password_hash, check_password_hash

# Load local configuration
load_dotenv(dotenv_path="file.env")

app = Flask(__name__)

flask_secret_key = os.environ.get("FLASK_SECRET_KEY")
if not flask_secret_key:
    raise RuntimeError("FLASK_SECRET_KEY is missing.")

app.secret_key = flask_secret_key
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_COOKIE_SECURE", "0") == "1",
)

csrf = CSRFProtect(app)

# ============================================================
# Rate Limiting (Uses REDIS_URL if available, else memory)
# ============================================================
redis_storage = os.environ.get("REDIS_URL", "memory://")
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=redis_storage,
)


@limiter.request_filter
def _exempt_localhost_from_limits():
    return request.remote_addr in ("127.0.0.1", "::1")


@app.errorhandler(429)
def handle_rate_limit(e):
    message = "Too many attempts. Please wait a bit and try again."
    path = request.path

    if path == "/login":
        return render_template("login.html", error=message), 429
    if path == "/register":
        return render_template("register.html", error=message), 429
    if path == "/forgot-password":
        return render_template("forgot_password.html", error=message), 429
    if path.startswith("/reset-password/"):
        token = path.rsplit("/", 1)[-1]
        return render_template("reset_password.html", token=token, error=message), 429

    return message, 429


SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise RuntimeError("Supabase environment variables are missing.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Worker queue to prevent server crashing from unbounded threads
MAX_WORKERS = int(os.environ.get("MAX_DOWNLOAD_WORKERS", "2"))
download_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

STUCK_PROCESSING_TIMEOUT_MINUTES = int(os.environ.get("STUCK_PROCESSING_TIMEOUT_MINUTES", "30"))
STUCK_SWEEP_INTERVAL_SECONDS = int(os.environ.get("STUCK_SWEEP_INTERVAL_SECONDS", "60"))

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_FROM_EMAIL = os.environ.get("SMTP_FROM_EMAIL", SMTP_USER or "no-reply@example.com")
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Clipgrab")

VERIFY_TOKEN_EXPIRY_HOURS = 24
RESET_TOKEN_EXPIRY_MINUTES = 60

COOKIES_FILE = "cookies.txt"
cookies_content = os.environ.get("YOUTUBE_COOKIES")
cookie_file = COOKIES_FILE if (cookies_content or os.path.isfile(COOKIES_FILE)) else None

if cookies_content:
    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        f.write(cookies_content)

# ============================================================
# Security & Helpers
# ============================================================
def is_safe_url(url: str) -> bool:
    """Blocks SSRF and private IP ranges."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        
        # Resolve IP and verify it is globally reachable (not private/loopback)
        ip_addr = socket.gethostbyname(hostname)
        ip_obj = ipaddress.ip_address(ip_addr)
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved:
            return False
        return True
    except Exception:
        return False


def send_email(to_email, subject, html_body):
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        print(f"[email not sent] To: {to_email} | Subject: {subject}\n{html_body}")
        return False

    try:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
        message["To"] = to_email
        message.set_content("This email requires an HTML-capable email client to view.")
        message.add_alternative(html_body, subtype="html")

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(message)
        return True
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")
        return False


def get_account_view(user_id):
    result = (
        supabase.table("users")
        .select("id, name, email, email_verified, created_at")
        .eq("id", user_id)
        .execute()
    )
    return result.data[0] if result.data else None


def friendly_download_error(message):
    text = (message or "").lower()
    if "private video" in text:
        return "This video is private and can't be downloaded."
    if "sign in to confirm your age" in text or ("age" in text and "restrict" in text):
        return "This video is age-restricted and requires sign-in — not supported."
    if any(k in text for k in ("video unavailable", "has been removed", "no longer available")):
        return "This video has been deleted or is no longer available."
    if "not available in your" in text or ("geo" in text and "restrict" in text):
        return "This video isn't available in your region."
    if "unsupported url" in text or "no video formats found" in text:
        return "That link isn't from a supported site, or has no downloadable video."
    if "login required" in text or "sign in" in text:
        return "This video requires sign-in — not supported."
    if "copyright" in text:
        return "This video isn't available due to a copyright claim."
    return "We couldn't process that link. Double-check the URL and try again."


def resolve_formats(url):
    if not is_safe_url(url):
        raise ValueError("Invalid or restricted URL provided.")

    ydl_opts = {
        "cookiefile": cookie_file,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    duration = info.get("duration") or 0
    all_formats = info.get("formats", [])

    def size_label(fmt, extra_bytes=0):
        exact = fmt.get("filesize")
        size = exact or fmt.get("filesize_approx")
        approx = not exact

        if not size:
            tbr = fmt.get("tbr")
            if tbr and duration:
                size = int(tbr * 1000 / 8 * duration)
                approx = True

        if not size:
            return "N/A"

        total_mb = round((size + extra_bytes) / (1024 * 1024), 1)
        prefix = "~" if (approx or extra_bytes) else ""
        return f"{prefix}{total_mb} MB"

    audio_only_formats = [
        f for f in all_formats
        if f.get("vcodec") == "none" and f.get("acodec") != "none"
    ]
    best_audio = max(audio_only_formats, key=lambda f: f.get("abr") or 0, default=None)
    best_audio_bytes = 0
    if best_audio:
        best_audio_bytes = best_audio.get("filesize") or best_audio.get("filesize_approx") or 0
        if not best_audio_bytes and best_audio.get("tbr") and duration:
            best_audio_bytes = int(best_audio["tbr"] * 1000 / 8 * duration)

    formats = []
    for f in all_formats:
        if f.get("vcodec") == "none":
            continue

        format_id = str(f.get("format_id", ""))
        ext = f.get("ext", "mp4")
        height = f.get("height")
        if not format_id or not height:
            continue

        has_audio = f.get("acodec") != "none"
        extra_bytes = 0 if has_audio else best_audio_bytes

        formats.append({
            "format_id": format_id,
            "ext": ext,
            "quality": f"{height}p",
            "height": height,
            "filesize": size_label(f, extra_bytes=extra_bytes),
            "has_audio": has_audio,
        })

    formats.sort(key=lambda x: x["height"], reverse=True)

    unique_formats = []
    seen = set()
    for item in formats:
        key = (item["quality"], item["ext"])
        if key not in seen:
            seen.add(key)
            unique_formats.append(item)

    audio_option = None
    if best_audio:
        audio_option = {
            "format_id": "mp3",
            "quality": "MP3",
            "filesize": size_label(best_audio),
        }

    return unique_formats, audio_option, info.get("title", "Video")


# ============================================================
# Download Background Worker
# ============================================================
def run_download_job(download_id, user_id, video_url, format_id, has_audio):
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        info_opts = {
            "cookiefile": cookie_file,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)

        video_title = info.get("title", "Video")
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", video_title).strip("_") or "video"
        output_template = os.path.join(temp_dir, f"{safe_name}.%(ext)s")

        is_audio_only = format_id == "mp3"
        if is_audio_only:
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": output_template,
                "cookiefile": cookie_file,
                "quiet": False,
                "no_warnings": False,
                "noplaylist": True,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
                "retries": 5,
                "socket_timeout": 30,
            }
        else:
            format_selector = format_id if has_audio else f"{format_id}+bestaudio/best"
            ydl_opts = {
                "format": format_selector,
                "outtmpl": output_template,
                "cookiefile": cookie_file,
                "quiet": False,
                "no_warnings": False,
                "noplaylist": True,
                "merge_output_format": "mp4",
                "retries": 5,
                "socket_timeout": 30,
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            dl_info = ydl.extract_info(video_url, download=True)
            downloaded_file = ydl.prepare_filename(dl_info)

        temp_file = downloaded_file
        base_name = os.path.splitext(downloaded_file)[0]
        possible_files = [
            f"{base_name}.mp3", f"{base_name}.mp4", f"{base_name}.webm",
            f"{base_name}.mkv", f"{base_name}.m4a", downloaded_file,
        ]
        for path in possible_files:
            if os.path.exists(path):
                temp_file = path
                break

        if not temp_file or not os.path.exists(temp_file):
            for filename in os.listdir(temp_dir):
                path = os.path.join(temp_dir, filename)
                if os.path.isfile(path):
                    temp_file = path
                    break

        if not temp_file or not os.path.exists(temp_file):
            raise Exception("Downloaded video file was not found.")

        extension = os.path.splitext(temp_file)[1].lower() or (".mp3" if is_audio_only else ".mp4")
        file_name = f"{uuid.uuid4().hex}{extension}"
        storage_path = f"{user_id}/{download_id}/{file_name}"

        content_type = (
            "audio/mpeg" if extension == ".mp3"
            else "video/mp4" if extension == ".mp4"
            else "video/webm" if extension == ".webm"
            else "application/octet-stream"
        )

        with open(temp_file, "rb") as file:
            supabase.storage.from_("videos").upload(
                storage_path, file, {"content-type": content_type, "upsert": "true"}
            )

        supabase.table("downloads").update({
            "storage_path": storage_path,
            "status": "completed",
            "preview_url": None,
        }).eq("id", download_id).eq("user_id", user_id).execute()

    except Exception as e:
        print(f"Background download {download_id} failed:", e)
        try:
            supabase.table("downloads").update({
                "status": "failed",
                "error_message": friendly_download_error(str(e)),
                "preview_url": None,
            }).eq("id", download_id).eq("user_id", user_id).execute()
        except Exception as update_error:
            print("Could not mark download as failed:", update_error)
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def sweep_stuck_downloads():
    while True:
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=STUCK_PROCESSING_TIMEOUT_MINUTES)).isoformat()
            supabase.table("downloads").update({
                "status": "failed",
                "error_message": f"Timed out after {STUCK_PROCESSING_TIMEOUT_MINUTES} minutes stuck in processing.",
                "preview_url": None,
            }).eq("status", "processing").lt("created_at", cutoff).execute()
        except Exception as e:
            print("Stuck-download sweep error:", e)
        time.sleep(STUCK_SWEEP_INTERVAL_SECONDS)


threading.Thread(target=sweep_stuck_downloads, daemon=True).start()

# ============================================================
# Routes
# ============================================================
@app.route("/", methods=["GET"])
def home():
    return render_template("home.html")


@app.route("/download", methods=["POST"])
@limiter.limit("20 per minute")
def download():
    url = request.form.get("url", "").strip()
    if not url:
        return render_template("home.html", error="Please enter a video URL.")

    if not is_safe_url(url):
        return render_template("home.html", error="Invalid or disallowed video URL.")

    try:
        unique_formats, audio_option, video_title = resolve_formats(url)
        if not unique_formats and not audio_option:
            return render_template("home.html", error="No downloadable video or audio formats found.")

        return render_template(
            "home.html",
            formats=unique_formats,
            audio_option=audio_option,
            video_title=video_title,
            video_url=url,
        )
    except Exception as e:
        return render_template("home.html", error=friendly_download_error(str(e)))


@app.route("/download-selected", methods=["POST"])
def download_selected():
    if "user_id" not in session:
        # Save request in session to resume seamlessly after login
        session["pending_download"] = {
            "video_url": request.form.get("video_url", ""),
            "format_id": request.form.get("format_id", ""),
            "quality": request.form.get("quality", ""),
            "format": request.form.get("format", ""),
        }
        return redirect(url_for("login", next="/dashboard"))

    user_id = session["user_id"]
    video_url = request.form.get("video_url", "").strip()
    format_id = request.form.get("format_id", "").strip()
    quality = request.form.get("quality", "").strip()
    ext_format = request.form.get("format", "mp4").strip()
    retry_id = request.form.get("retry_id", "").strip()

    if not video_url or not format_id:
        return redirect("/")

    if not is_safe_url(video_url):
        return render_template("home.html", error="Invalid or disallowed video URL.")

    download_id = None
    try:
        ydl_info_opts = {"cookiefile": cookie_file, "quiet": True, "no_warnings": True, "noplaylist": True}
        with yt_dlp.YoutubeDL(ydl_info_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)

        video_title = info.get("title", "Video")
        is_audio_only = format_id == "mp3"
        has_audio = True if is_audio_only else False
        preview_url = None

        if not is_audio_only:
            selected_format = next(
                (item for item in info.get("formats", [])
                 if str(item.get("format_id", "")) == format_id and item.get("vcodec") != "none"),
                None,
            )
            if not selected_format:
                return render_template("home.html", error="Selected quality is no longer available.")
            has_audio = selected_format.get("acodec") != "none"
            preview_url = selected_format.get("url")

        if retry_id:
            download_id = retry_id
            supabase.table("downloads").update({
                "status": "processing",
                "error_message": None,
                "preview_url": preview_url,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", download_id).eq("user_id", user_id).execute()
        else:
            download_record = supabase.table("downloads").insert({
                "user_id": user_id,
                "video_title": video_title,
                "original_url": video_url,
                "thumbnail_url": info.get("thumbnail"),
                "format_id": format_id,
                "format": "mp3" if is_audio_only else ext_format,
                "quality": "MP3" if is_audio_only else quality,
                "status": "processing",
                "preview_url": preview_url,
            }).execute()

            if not download_record.data:
                return render_template("home.html", error="Could not create download record.")
            download_id = download_record.data[0]["id"]

        download_executor.submit(
            run_download_job, download_id, user_id, video_url, format_id, has_audio
        )
        return redirect("/dashboard")

    except Exception as e:
        return render_template("home.html", error=friendly_download_error(str(e)))


def _safe_next_url(candidate):
    """Only allow redirecting to a same-site relative path. Blocks
    open-redirect payloads like '/login?next=https://evil.com' or
    the protocol-relative '//evil.com' trick."""
    if not candidate:
        return "/dashboard"
    if not candidate.startswith("/") or candidate.startswith("//"):
        return "/dashboard"
    return candidate


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    next_url = _safe_next_url(request.args.get("next"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            return render_template("login.html", error="Email and password are required.")

        try:
            result = supabase.table("users").select("id, name, email, password_hash").eq("email", email).execute()
            if not result.data or not check_password_hash(result.data[0]["password_hash"], password):
                return render_template("login.html", error="Invalid email or password.")

            user = result.data[0]
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_email"] = user["email"]

            supabase.table("users").update({
                "last_login": datetime.now(timezone.utc).isoformat()
            }).eq("id", user["id"]).execute()

            return redirect(_safe_next_url(request.form.get("next")) if request.form.get("next") else next_url)
        except Exception as e:
            return render_template("login.html", error=f"Login failed: {str(e)}")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]
    search_query = request.args.get("q", "").strip().lower()
    status_filter = request.args.get("status", "all").strip().lower()
    sort_order = request.args.get("sort", "newest").strip().lower()
    page_size = 8

    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1

    escaped_search = search_query.replace("%", r"\%").replace("_", r"\_")

    def build_query():
        q = supabase.table("downloads").select("*", count="exact").eq("user_id", user_id)
        if search_query:
            q = q.ilike("video_title", f"%{escaped_search}%")
        if status_filter in {"processing", "completed", "failed"}:
            q = q.eq("status", status_filter)
        return q.order("created_at", desc=(sort_order != "oldest"))

    try:
        start = (page - 1) * page_size
        end = start + page_size - 1
        result = build_query().range(start, end).execute()

        total_downloads = result.count or 0
        total_pages = max(1, (total_downloads + page_size - 1) // page_size)
        downloads = result.data or []

        return render_template(
            "dashboard.html",
            user_name=session.get("user_name", "User"),
            downloads=downloads,
            search_query=search_query,
            status_filter=status_filter,
            sort_order=sort_order,
            page=page,
            total_pages=total_pages,
            total_downloads=total_downloads,
        )
    except Exception as e:
        return render_template("dashboard.html", user_name=session.get("user_name", "User"), downloads=[], error=f"Unable to load downloads: {str(e)}")


@app.route("/retry/<download_id>")
def retry_download(download_id):
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]
    try:
        result = supabase.table("downloads").select("*").eq("id", download_id).eq("user_id", user_id).execute()
        video = result.data[0] if result.data else None

        if not video or video.get("status") != "failed":
            return redirect("/dashboard")

        original_url = (video.get("original_url") or "").strip()
        unique_formats, audio_option, video_title = resolve_formats(original_url)

        return render_template(
            "home.html",
            formats=unique_formats,
            audio_option=audio_option,
            video_title=video_title,
            video_url=original_url,
            retry_id=download_id,
        )
    except Exception as e:
        return render_template("home.html", error=friendly_download_error(str(e)))


@app.route("/video/<download_id>")
def watch_video(download_id):
    if "user_id" not in session:
        return redirect("/login")

    try:
        result = supabase.table("downloads").select("*").eq("id", download_id).eq("user_id", session["user_id"]).execute()
        video = result.data[0] if result.data else None
        if not video:
            return "Video not found", 404

        title = video.get("video_title", "Video")
        status = video.get("status")

        if status == "completed":
            storage_path = video.get("storage_path")
            if not storage_path:
                return "Video file not found.", 404
            # Expiry extended to 4 hours (14400s) to prevent mid-stream failure
            signed = supabase.storage.from_("videos").create_signed_url(storage_path, 14400)
            video_url = signed.get("signedURL") or signed.get("signedUrl")
            return render_template("watch.html", title=title, video_url=video_url, download_id=download_id, is_preview=False)

        if status == "processing" and video.get("preview_url"):
            return render_template("watch.html", title=title, video_url=video.get("preview_url"), download_id=download_id, is_preview=True)

        if status == "processing":
            return "Video is still processing. Please check back shortly.", 202

        return "Video is not available.", 400
    except Exception as e:
        return f"Unable to watch video: {e}", 500


@app.route("/video/<download_id>/download")
def download_saved_video(download_id):
    if "user_id" not in session:
        return redirect("/login")

    try:
        result = supabase.table("downloads").select("*").eq("id", download_id).eq("user_id", session["user_id"]).execute()
        video = result.data[0] if result.data else None
        if not video or video.get("status") != "completed":
            return "Video is not ready yet.", 400

        storage_path = video.get("storage_path")
        if not storage_path:
            return "Video file not found.", 404
        signed = supabase.storage.from_("videos").create_signed_url(storage_path, 14400)
        video_url = signed.get("signedURL") or signed.get("signedUrl")
        return redirect(video_url)
    except Exception as e:
        return f"Unable to download video: {e}", 500


@app.route("/delete/<download_id>", methods=["POST"])
def delete_video(download_id):
    if "user_id" not in session:
        return redirect("/login")
    user_id = session["user_id"]

    try:
        result = supabase.table("downloads").select("id, storage_path").eq("id", download_id).eq("user_id", user_id).execute()
        video = result.data[0] if result.data else None
        if video and video.get("storage_path"):
            try:
                supabase.storage.from_("videos").remove([video["storage_path"]])
            except Exception:
                pass
        supabase.table("downloads").delete().eq("id", download_id).eq("user_id", user_id).execute()
        return redirect("/dashboard")
    except Exception as e:
        print(f"Failed to delete download {download_id}:", e)
        return redirect("/dashboard")


@app.route("/delete-bulk", methods=["POST"])
def delete_bulk():
    if "user_id" not in session:
        return redirect("/login")
    user_id = session["user_id"]
    selected_ids = [item.strip() for item in request.form.getlist("selected_ids") if item.strip()]

    if selected_ids:
        try:
            result = supabase.table("downloads").select("id, storage_path").in_("id", selected_ids).eq("user_id", user_id).execute()
            paths = [row["storage_path"] for row in (result.data or []) if row.get("storage_path")]
            if paths:
                try:
                    supabase.storage.from_("videos").remove(paths)
                except Exception as storage_error:
                    print("Bulk storage delete warning:", storage_error)
            supabase.table("downloads").delete().in_("id", selected_ids).eq("user_id", user_id).execute()
        except Exception as e:
            print("Bulk delete failed:", e)
    return redirect("/dashboard")


@app.route("/api/download-status/<download_id>")
def download_status(download_id):
    if "user_id" not in session:
        return {"error": "Login required"}, 401

    result = supabase.table("downloads").select("id, status, storage_path").eq("id", download_id).eq("user_id", session["user_id"]).execute()
    video = result.data[0] if result.data else None
    if not video:
        return {"error": "Video not found"}, 404

    return {
        "id": video["id"],
        "status": video.get("status", "processing"),
        "ready": video.get("status") == "completed" and bool(video.get("storage_path")),
    }


# ============================================================
# Auth Routes (Register, Reset, Verify, Account)
# ============================================================
@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not name or not email or not password:
            return render_template("register.html", error="All fields are required.")
        if password != confirm_password:
            return render_template("register.html", error="Passwords do not match.")
        if len(password) < 6:
            return render_template("register.html", error="Password must be at least 6 characters.")

        try:
            existing = supabase.table("users").select("id").eq("email", email).execute()
            if existing.data:
                return render_template("register.html", error="An account with this email already exists.")

            token = secrets.token_urlsafe(32)
            expires = (datetime.now(timezone.utc) + timedelta(hours=VERIFY_TOKEN_EXPIRY_HOURS)).isoformat()
            supabase.table("users").insert({
                "name": name,
                "email": email,
                "password_hash": generate_password_hash(password),
                "email_verified": False,
                "verification_token": token,
                "verification_token_expires": expires,
            }).execute()

            verify_link = f"{request.url_root.rstrip('/')}/verify-email/{token}"
            send_email(email, "Verify your Clipgrab email", f"<p>Verify account: <a href='{verify_link}'>{verify_link}</a></p>")
            return render_template("register.html", success="Account created! Check your email for the verification link.")
        except Exception as e:
            return render_template("register.html", error=f"Registration failed: {str(e)}")

    return render_template("register.html")


@app.route("/verify-email/<token>")
def verify_email(token):
    try:
        result = supabase.table("users").select("id, verification_token_expires").eq("verification_token", token).execute()
        if not result.data:
            return render_template("login.html", error="Verification link is invalid or already used.")

        user = result.data[0]
        expires = user.get("verification_token_expires")
        if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")) < datetime.now(timezone.utc):
            return render_template("login.html", error="Verification link has expired.")

        supabase.table("users").update({
            "email_verified": True,
            "verification_token": None,
            "verification_token_expires": None,
        }).eq("id", user["id"]).execute()
        return render_template("login.html", success="Your email is verified. You can log in now.")
    except Exception as e:
        return render_template("login.html", error=f"Verification failed: {str(e)}")


@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            return render_template("forgot_password.html", error="Enter your email.")

        try:
            result = supabase.table("users").select("id, name, email").eq("email", email).execute()
            if result.data:
                token = secrets.token_urlsafe(32)
                expires = (datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRY_MINUTES)).isoformat()
                supabase.table("users").update({
                    "reset_token": token,
                    "reset_token_expires": expires,
                }).eq("id", result.data[0]["id"]).execute()

                reset_link = f"{request.url_root.rstrip('/')}/reset-password/{token}"
                send_email(email, "Reset your Clipgrab password", f"<p>Reset link: <a href='{reset_link}'>{reset_link}</a></p>")

            return render_template("forgot_password.html", success="If an account exists, a reset link was sent.")
        except Exception as e:
            return render_template("forgot_password.html", error=f"Error: {str(e)}")

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def reset_password(token):
    try:
        result = supabase.table("users").select("id, reset_token_expires").eq("reset_token", token).execute()
        if not result.data:
            return render_template("login.html", error="Reset link is invalid or used.")

        user = result.data[0]
        expires = user.get("reset_token_expires")
        if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")) < datetime.now(timezone.utc):
            return render_template("login.html", error="Reset link has expired.")
    except Exception as e:
        return render_template("login.html", error=f"Error: {str(e)}")

    if request.method == "POST":
        new_pw = request.form.get("password", "")
        confirm_pw = request.form.get("confirm_password", "")
        if not new_pw or new_pw != confirm_pw:
            return render_template("reset_password.html", token=token, error="Passwords do not match.")
        if len(new_pw) < 6:
            return render_template("reset_password.html", token=token, error="Password must be at least 6 characters.")

        try:
            supabase.table("users").update({
                "password_hash": generate_password_hash(new_pw),
                "reset_token": None,
                "reset_token_expires": None,
            }).eq("id", user["id"]).execute()
            return render_template("login.html", success="Password updated. You can log in now.")
        except Exception as e:
            return render_template("reset_password.html", token=token, error=f"Something went wrong: {str(e)}")

    return render_template("reset_password.html", token=token)


@app.route("/account")
def account():
    if "user_id" not in session:
        return redirect("/login")
    user = get_account_view(session["user_id"])
    if not user:
        session.clear()
        return redirect("/login")
    return render_template("account.html", user=user)


@app.route("/account/profile", methods=["POST"])
def update_profile():
    if "user_id" not in session:
        return redirect("/login")
    user_id = session["user_id"]
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()

    if not name or not email:
        return render_template("account.html", user=get_account_view(user_id), error="Name and email are required.")

    try:
        current = get_account_view(user_id)
        email_changed = current and current.get("email") != email

        if email_changed:
            existing = supabase.table("users").select("id").eq("email", email).neq("id", user_id).execute()
            if existing.data:
                return render_template("account.html", user=current, error="That email is already in use by another account.")

        update_payload = {"name": name, "email": email}
        verify_link = None

        if email_changed:
            # Changing the email means it has to be verified again.
            token = secrets.token_urlsafe(32)
            expires = (datetime.now(timezone.utc) + timedelta(hours=VERIFY_TOKEN_EXPIRY_HOURS)).isoformat()
            update_payload.update({
                "email_verified": False,
                "verification_token": token,
                "verification_token_expires": expires,
            })
            verify_link = f"{request.url_root.rstrip('/')}/verify-email/{token}"

        supabase.table("users").update(update_payload).eq("id", user_id).execute()
        session["user_name"] = name
        session["user_email"] = email

        success_msg = "Profile updated."
        if email_changed and verify_link:
            send_email(
                email,
                "Verify your new Clipgrab email",
                f"<p>Confirm your new email address: <a href='{verify_link}'>{verify_link}</a></p>"
            )
            success_msg += " We've sent a verification link to your new email address."

        return render_template("account.html", user=get_account_view(user_id), success=success_msg)

    except Exception as e:
        return render_template("account.html", user=get_account_view(user_id), error=f"Unable to update profile: {str(e)}")


@app.route("/account/password", methods=["POST"])
def change_password():
    if "user_id" not in session:
        return redirect("/login")
    user_id = session["user_id"]
    current_pw = request.form.get("current_password", "")
    new_pw = request.form.get("new_password", "")
    confirm_pw = request.form.get("confirm_password", "")

    try:
        result = supabase.table("users").select("password_hash").eq("id", user_id).execute()
        user = result.data[0] if result.data else None
        if not user or not check_password_hash(user["password_hash"], current_pw):
            return render_template("account.html", user=get_account_view(user_id), error="Current password incorrect.")

        if not new_pw or new_pw != confirm_pw or len(new_pw) < 6:
            return render_template("account.html", user=get_account_view(user_id), error="New password must be at least 6 characters and match the confirmation.")

        supabase.table("users").update({"password_hash": generate_password_hash(new_pw)}).eq("id", user_id).execute()
        return render_template("account.html", user=get_account_view(user_id), success="Password changed successfully.")

    except Exception as e:
        return render_template("account.html", user=get_account_view(user_id), error=f"Unable to change password: {str(e)}")


@app.route("/account/resend-verification", methods=["POST"])
@limiter.limit("3 per 10 minutes")
def resend_verification():
    if "user_id" not in session:
        return redirect("/login")
    user_id = session["user_id"]

    try:
        user = get_account_view(user_id)
        if not user:
            return redirect("/login")

        if user.get("email_verified"):
            return render_template("account.html", user=user, success="Your email is already verified.")

        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=VERIFY_TOKEN_EXPIRY_HOURS)).isoformat()
        supabase.table("users").update({
            "verification_token": token,
            "verification_token_expires": expires,
        }).eq("id", user_id).execute()

        verify_link = f"{request.url_root.rstrip('/')}/verify-email/{token}"
        send_email(
            user["email"],
            "Verify your Clipgrab email",
            f"<p>Verify your Clipgrab email address: <a href='{verify_link}'>{verify_link}</a></p>"
        )

        return render_template("account.html", user=user, success="Verification email sent — check your inbox.")

    except Exception as e:
        return render_template("account.html", user=get_account_view(user_id), error=f"Unable to resend verification email: {str(e)}")


@app.route("/account/delete", methods=["POST"])
def delete_account():
    if "user_id" not in session:
        return redirect("/login")
    user_id = session["user_id"]
    password = request.form.get("password", "")

    try:
        result = supabase.table("users").select("password_hash").eq("id", user_id).execute()
        user = result.data[0] if result.data else None
        if not user or not check_password_hash(user["password_hash"], password):
            return render_template("account.html", user=get_account_view(user_id), error="Password incorrect. Your account was not deleted.")

        downloads = supabase.table("downloads").select("storage_path").eq("user_id", user_id).execute().data or []
        storage_paths = [item["storage_path"] for item in downloads if item.get("storage_path")]
        if storage_paths:
            try:
                supabase.storage.from_("videos").remove(storage_paths)
            except Exception as storage_error:
                print("Account deletion storage cleanup warning:", storage_error)

        supabase.table("downloads").delete().eq("user_id", user_id).execute()
        supabase.table("users").delete().eq("id", user_id).execute()
        session.clear()
        return render_template("login.html", success="Your account and all your videos have been deleted.")

    except Exception as e:
        return render_template("account.html", user=get_account_view(user_id), error=f"Unable to delete account: {str(e)}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
