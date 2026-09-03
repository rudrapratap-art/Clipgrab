from flask import Flask, request, render_template, redirect, session
from flask_wtf.csrf import CSRFProtect
import os
import shutil
import tempfile
import re
import time
import threading
import uuid
import secrets
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
import yt_dlp
from dotenv import load_dotenv

# Load local configuration early; Render provides these values as environment variables.
load_dotenv(dotenv_path="file.env")

from supabase import create_client, Client
from werkzeug.security import generate_password_hash, check_password_hash

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

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise RuntimeError("Supabase environment variables are missing.")

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY
)

# ============================================================
# Background job settings
# ============================================================
# How long a download can sit in "processing" before the sweep
# thread below gives up on it and marks it "failed". Covers the
# case where the server restarts/crashes mid-download and the row
# is left stuck forever with no way to retry it.
STUCK_PROCESSING_TIMEOUT_MINUTES = int(
    os.environ.get("STUCK_PROCESSING_TIMEOUT_MINUTES", "30")
)
STUCK_SWEEP_INTERVAL_SECONDS = int(
    os.environ.get("STUCK_SWEEP_INTERVAL_SECONDS", "60")
)

# ============================================================
# Email (account verification / password reset)
# ============================================================
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_FROM_EMAIL = os.environ.get("SMTP_FROM_EMAIL", SMTP_USER or "no-reply@example.com")
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Clipgrab")

VERIFY_TOKEN_EXPIRY_HOURS = 24
RESET_TOKEN_EXPIRY_MINUTES = 60


def send_email(to_email, subject, html_body):
    """Best-effort email sender. If SMTP isn't configured (SMTP_HOST /
    SMTP_USER / SMTP_PASSWORD missing from file.env), the email is
    printed to the console instead of failing outright — so account
    flows are still testable locally before real SMTP is wired up."""
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        print(
            f"[email not sent — SMTP not configured] "
            f"To: {to_email} | Subject: {subject}\n{html_body}"
        )
        return False

    try:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
        message["To"] = to_email
        message.set_content(
            "This email requires an HTML-capable email client to view."
        )
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
    """Fetch the safe-to-render subset of a user's account row (never
    includes password_hash or the raw reset/verification tokens)."""
    result = (
        supabase
        .table("users")
        .select("id, name, email, email_verified, created_at")
        .eq("id", user_id)
        .single()
        .execute()
    )
    return result.data

# ============================================================
# Cookies
# ============================================================
COOKIES_FILE = "cookies.txt"
cookies_content = os.environ.get("YOUTUBE_COOKIES")
cookie_file = (
    COOKIES_FILE
    if cookies_content or os.path.isfile(COOKIES_FILE)
    else None
)

if cookies_content:
    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        f.write(cookies_content)
    print("Cookies file saved from environment variable.")
elif cookie_file:
    print("Using local cookies.txt for yt-dlp.")
else:
    print("No cookies found in environment variable YOUTUBE_COOKIES.")


# ============================================================
# Background download worker
# ============================================================
# Runs in its own thread, separate from the request that
# triggered it. Must not touch flask.session or flask.request —
# those only exist inside the request that started the thread.
# Everything the job needs is passed in as an argument instead.
# ============================================================
def run_download_job(download_id, user_id, video_url, format_id, has_audio):
    temp_dir = None
    temp_file = None

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

        safe_name = re.sub(
            r"[^a-zA-Z0-9_-]+",
            "_",
            video_title
        ).strip("_") or "video"

        output_template = os.path.join(
            temp_dir,
            f"{safe_name}.%(ext)s"
        )

        format_selector = (
            format_id if has_audio else f"{format_id}+bestaudio/best"
        )

        ydl_opts = {
            "format": format_selector,
            "outtmpl": output_template,

            "cookiefile": cookie_file,

            "quiet": False,
            "no_warnings": False,
            "noplaylist": True,
            "merge_output_format": "mp4",

            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/139.0.0.0 Safari/537.36"
                ),
                "Referer": video_url,
            },

            "retries": 10,
            "fragment_retries": 10,
            "file_access_retries": 5,
            "socket_timeout": 30,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            dl_info = ydl.extract_info(video_url, download=True)
            downloaded_file = ydl.prepare_filename(dl_info)

        temp_file = downloaded_file

        base_name = os.path.splitext(downloaded_file)[0]
        possible_files = [
            f"{base_name}.mp4",
            f"{base_name}.webm",
            f"{base_name}.mkv",
            downloaded_file,
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

        extension = os.path.splitext(temp_file)[1].lower() or ".mp4"
        file_name = f"{uuid.uuid4().hex}{extension}"

        storage_path = f"{user_id}/{download_id}/{file_name}"

        content_type = (
            "video/mp4" if extension == ".mp4"
            else "video/webm" if extension == ".webm"
            else "application/octet-stream"
        )

        with open(temp_file, "rb") as file:
            supabase.storage.from_("videos").upload(
                storage_path,
                file,
                {
                    "content-type": content_type,
                    "upsert": "true",
                }
            )

        supabase.table("downloads").update({
            "storage_path": storage_path,
            "status": "completed",
            # No longer needed once our own copy is ready — the
            # source's temporary link may also have expired by now.
            "preview_url": None,
        }).eq("id", download_id).eq("user_id", user_id).execute()

    except Exception as e:
        print(f"Background download {download_id} failed:", e)
        try:
            supabase.table("downloads").update({
                "status": "failed",
                "error_message": str(e)[:500],
                "preview_url": None,
            }).eq("id", download_id).eq("user_id", user_id).execute()
        except Exception as update_error:
            print("Could not mark download as failed:", update_error)

    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


# ============================================================
# Stuck-download sweep
# ============================================================
# Runs forever in a background thread, started once at process
# startup. Every STUCK_SWEEP_INTERVAL_SECONDS it looks for rows
# that have been "processing" for longer than
# STUCK_PROCESSING_TIMEOUT_MINUTES and marks them "failed" so they
# don't sit there forever (e.g. after a server crash mid-download)
# and so the user gets a Retry button on the dashboard instead.
# ============================================================
def sweep_stuck_downloads():
    while True:
        try:
            cutoff = (
                datetime.now(timezone.utc)
                - timedelta(minutes=STUCK_PROCESSING_TIMEOUT_MINUTES)
            ).isoformat()

            result = (
                supabase
                .table("downloads")
                .update({
                    "status": "failed",
                    "error_message": (
                        f"Timed out after "
                        f"{STUCK_PROCESSING_TIMEOUT_MINUTES} minutes "
                        f"stuck in processing."
                    ),
                    "preview_url": None,
                })
                .eq("status", "processing")
                .lt("created_at", cutoff)
                .execute()
            )

            if result.data:
                print(
                    f"Stuck-download sweep: marked "
                    f"{len(result.data)} row(s) as failed."
                )

        except Exception as e:
            print("Stuck-download sweep failed:", e)

        time.sleep(STUCK_SWEEP_INTERVAL_SECONDS)


# Start the sweep loop once, when the module is loaded (works both
# for `python app.py` and when imported by a WSGI server).
threading.Thread(target=sweep_stuck_downloads, daemon=True).start()



# ============================================================
# HTML templates now live in templates/ (see templates/home.html,
# templates/login.html, templates/register.html, templates/dashboard.html,
# templates/watch.html, templates/retry.html)
# ============================================================


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            return render_template(
                "login.html",
                error="Email and password are required."
            )

        try:
            # Find user by email
            result = (
                supabase
                .table("users")
                .select("id, name, email, password_hash")
                .eq("email", email)
                .execute()
            )

            if not result.data:
                return render_template(
                    "login.html",
                    error="Invalid email or password."
                )

            user = result.data[0]

            # Verify hashed password
            if not check_password_hash(
                user["password_hash"],
                password
            ):
                return render_template(
                    "login.html",
                    error="Invalid email or password."
                )

            # Store only required information in session
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_email"] = user["email"]

            # Update last login
            supabase.table("users").update({
                "last_login": "now()"
            }).eq("id", user["id"]).execute()

            return redirect("/")

        except Exception as e:
            return render_template(
                "login.html",
                error=f"Login failed: {str(e)}"
            )

    return render_template("login.html")

  
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


  
@app.route("/dashboard")
def dashboard():
    # Login required
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

    # Escape LIKE wildcards in the user's search text so a query like
    # "100%" is treated literally, not as a pattern.
    escaped_search = search_query.replace("%", r"\%").replace("_", r"\_")

    def build_query():
        query = (
            supabase
            .table("downloads")
            .select("*", count="exact")
            .eq("user_id", user_id)
        )
        if search_query:
            query = query.ilike("video_title", f"%{escaped_search}%")
        if status_filter in {"processing", "completed", "failed"}:
            query = query.eq("status", status_filter)
        return query.order("created_at", desc=(sort_order != "oldest"))

    try:
        # Ask the database for exactly one page of rows (and the total
        # matching count) instead of fetching every download and
        # slicing it in Python — this used to pull a user's entire
        # history on every dashboard visit regardless of how many
        # rows they had.
        start = (page - 1) * page_size
        end = start + page_size - 1
        result = build_query().range(start, end).execute()

        total_downloads = result.count or 0
        total_pages = max(1, (total_downloads + page_size - 1) // page_size)

        if page > total_pages:
            # Requested page is past the end (e.g. a stale bookmark
            # or a hand-edited URL) — clamp and re-fetch that page.
            page = total_pages
            start = (page - 1) * page_size
            end = start + page_size - 1
            result = build_query().range(start, end).execute()

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
            total_downloads=total_downloads
        )

    except Exception as e:
        return render_template(
            "dashboard.html",
            user_name=session.get("user_name", "User"),
            downloads=[],
            error=f"Unable to load downloads: {str(e)}"
        )


# ============================================================
# Download status API
# ============================================================
@app.route("/api/download-status/<download_id>")
def download_status(download_id):
    if "user_id" not in session:
        return {"error": "Login required"}, 401

    user_id = session["user_id"]

    try:
        result = (
            supabase
            .table("downloads")
            .select("id, status, storage_path")
            .eq("id", download_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )

        video = result.data

        if not video:
            return {"error": "Video not found"}, 404

        return {
            "id": video["id"],
            "status": video.get("status", "processing"),
            "ready": (
                video.get("status") == "completed"
                and bool(video.get("storage_path"))
            )
        }

    except Exception as e:
        return {"error": str(e)}, 500


# ============================================================
# Delete saved video
# ============================================================
@app.route("/delete/<download_id>", methods=["GET"])
def delete_video(download_id):
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]

    try:
        result = (
            supabase
            .table("downloads")
            .select("id, storage_path")
            .eq("id", download_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )

        video = result.data

        if not video:
            return "Video not found.", 404

        storage_path = video.get("storage_path")

        if storage_path:
            try:
                supabase.storage.from_("videos").remove([storage_path])
            except Exception as storage_error:
                print("Storage delete warning:", storage_error)

        (
            supabase
            .table("downloads")
            .delete()
            .eq("id", download_id)
            .eq("user_id", user_id)
            .execute()
        )

        return redirect("/dashboard")

    except Exception as e:
        return render_template(
            "dashboard.html",
            user_name=session.get("user_name", "User"),
            downloads=[],
            error=f"Unable to delete video: {str(e)}"
        )


# ============================================================
# Bulk-delete selected videos
# ============================================================
@app.route("/delete-bulk", methods=["POST"])
def delete_bulk():
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]
    selected_ids = [
        item.strip()
        for item in request.form.getlist("selected_ids")
        if item.strip()
    ]

    if not selected_ids:
        return redirect("/dashboard")

    try:
        result = (
            supabase
            .table("downloads")
            .select("id, storage_path")
            .in_("id", selected_ids)
            .eq("user_id", user_id)
            .execute()
        )

        owned_rows = result.data or []
        owned_ids = [row["id"] for row in owned_rows]
        storage_paths = [
            row["storage_path"] for row in owned_rows if row.get("storage_path")
        ]

        if not owned_ids:
            return redirect("/dashboard")

        if storage_paths:
            try:
                supabase.storage.from_("videos").remove(storage_paths)
            except Exception as storage_error:
                print("Bulk storage delete warning:", storage_error)

        (
            supabase
            .table("downloads")
            .delete()
            .in_("id", owned_ids)
            .eq("user_id", user_id)
            .execute()
        )

        return redirect("/dashboard")

    except Exception as e:
        return render_template(
            "dashboard.html",
            user_name=session.get("user_name", "User"),
            downloads=[],
            error=f"Unable to delete selected videos: {str(e)}"
        )


 # ============================================================
# Retry failed download
# ============================================================
@app.route("/retry/<download_id>")
def retry_download(download_id):
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]

    try:
        result = (
            supabase
            .table("downloads")
            .select("*")
            .eq("id", download_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )

        video = result.data

        if not video:
            return "Download not found.", 404

        if video.get("status") != "failed":
            return redirect("/dashboard")

        original_url = (video.get("original_url") or "").strip()

        if not original_url:
            return "Original video URL not found.", 400

        format_id = (video.get("format_id") or "").strip()

        if not format_id:
            return "Saved format is not available for retry.", 400

        return render_template(
            "retry.html",
            original_url=original_url,
            format_id=format_id,
            download_id=download_id
        )

    except Exception as e:
        return f"Retry failed: {e}", 500


@app.route("/download-selected", methods=["POST"])
def download_selected():
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]
    video_url = request.form.get("video_url", "").strip()
    format_id = request.form.get("format_id", "").strip()
    retry_id = request.form.get("retry_id", "").strip()

    if not video_url or not format_id:
        return redirect("/")

    download_id = None

    try:
        # Quick metadata-only check, so we can give the user
        # immediate feedback if the URL/format is no longer valid,
        # before we ever hand off to the background worker.
        ydl_info_opts = {
            "cookiefile": cookie_file,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

        with yt_dlp.YoutubeDL(ydl_info_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)

        video_title = info.get("title", "Video")
        selected_format = next(
            (
                item for item in info.get("formats", [])
                if str(item.get("format_id", "")) == format_id
                and item.get("vcodec") != "none"
            ),
            None,
        )

        if not selected_format:
            return render_template(
                "home.html",
                error="Selected quality is no longer available."
            )

        has_audio = selected_format.get("acodec") != "none"

        # Best-effort "watch while it uploads" link: yt-dlp already
        # resolved a direct, temporary URL for each format. If the
        # quality the user picked is video-only (no audio track), grab
        # the best combined video+audio format instead so the preview
        # actually has sound. This link comes straight from the source
        # site and typically expires within a few hours — plenty of
        # time to cover the background upload, but it isn't meant to
        # be a permanent link and some sites may restrict playback
        # away from their own player.
        if has_audio:
            preview_url = selected_format.get("url")
        else:
            combined_formats = [
                item for item in info.get("formats", [])
                if item.get("vcodec") != "none"
                and item.get("acodec") != "none"
                and item.get("url")
            ]
            best_combined = max(
                combined_formats,
                key=lambda item: item.get("height") or 0,
                default=None,
            )
            preview_url = (
                best_combined.get("url") if best_combined
                else selected_format.get("url")
            )

        if retry_id:
            retry_record = (
                supabase
                .table("downloads")
                .select("id, status, original_url")
                .eq("id", retry_id)
                .eq("user_id", user_id)
                .single()
                .execute()
            )
            if not retry_record.data or retry_record.data.get("status") != "failed":
                return "Download is not available for retry.", 400
            download_id = retry_id
            supabase.table("downloads").update({
                "status": "processing",
                "error_message": None,
                "preview_url": preview_url,
                # Reset the "clock" so the stuck-download sweep times
                # out from this retry, not from the original attempt.
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", download_id).eq("user_id", user_id).execute()
        else:
            download_record = (
                supabase
                .table("downloads")
                .insert({
                    "user_id": user_id,
                    "video_title": video_title,
                    "original_url": video_url,
                    "thumbnail_url": info.get("thumbnail"),
                    "format_id": format_id,
                    "status": "processing",
                    "preview_url": preview_url,
                })
                .execute()
            )

            if not download_record.data:
                return render_template(
                    "home.html",
                    error="Could not create download record."
                )

            download_id = download_record.data[0]["id"]

        # Hand the actual download+merge+upload off to a background
        # thread and return to the user right away — they'll see it
        # as "Processing..." on the dashboard and it'll flip to
        # completed/failed once the worker finishes.
        threading.Thread(
            target=run_download_job,
            args=(download_id, user_id, video_url, format_id, has_audio),
            daemon=True,
        ).start()

        return redirect("/dashboard")

    except Exception as e:

        if download_id:
            try:
                supabase.table("downloads").update({
                    "status": "failed",
                    "error_message": str(e)[:500]
                }).eq("id", download_id).eq(
                    "user_id", user_id
                ).execute()
            except Exception:
                pass

        return render_template(
            "home.html",
            error=f"Download failed: {str(e)}"
        )


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        # Basic validation
        if not name or not email or not password:
            return render_template(
                "register.html",
                error="All fields are required."
            )

        if password != confirm_password:
            return render_template(
                "register.html",
                error="Passwords do not match."
            )

        if len(password) < 6:
            return render_template(
                "register.html",
                error="Password must be at least 6 characters."
            )

        try:
            # Check whether email already exists
            existing = (
                supabase
                .table("users")
                .select("id")
                .eq("email", email)
                .execute()
            )

            if existing.data:
                return render_template(
                    "register.html",
                    error="An account with this email already exists."
                )

            # Hash password before storing it
            password_hash = generate_password_hash(password)

            verification_token = secrets.token_urlsafe(32)
            verification_expires = (
                datetime.now(timezone.utc)
                + timedelta(hours=VERIFY_TOKEN_EXPIRY_HOURS)
            ).isoformat()

            # Create user
            result = (
                supabase
                .table("users")
                .insert({
                    "name": name,
                    "email": email,
                    "password_hash": password_hash,
                    "email_verified": False,
                    "verification_token": verification_token,
                    "verification_token_expires": verification_expires,
                })
                .execute()
            )

            if not result.data:
                return render_template(
                    "register.html",
                    error="Unable to create account."
                )

            verify_link = (
                f"{request.url_root.rstrip('/')}"
                f"/verify-email/{verification_token}"
            )
            send_email(
                email,
                "Verify your Clipgrab email",
                f"""
                <p>Hi {name},</p>
                <p>Thanks for signing up for Clipgrab. Please verify
                your email address to finish setting up your account:</p>
                <p><a href="{verify_link}">{verify_link}</a></p>
                <p>This link expires in {VERIFY_TOKEN_EXPIRY_HOURS} hours.</p>
                """
            )

            return render_template(
                "register.html",
                success=(
                    "Account created! We've sent a verification link "
                    "to your email — you can log in right away."
                )
            )

        except Exception as e:
            return render_template(
                "register.html",
                error=f"Registration failed: {str(e)}"
            )

    return render_template("register.html")


# ============================================================
# Email verification
# ============================================================
@app.route("/verify-email/<token>")
def verify_email(token):
    try:
        result = (
            supabase
            .table("users")
            .select("id, verification_token_expires")
            .eq("verification_token", token)
            .execute()
        )

        if not result.data:
            return render_template(
                "login.html",
                error="That verification link is invalid or has already been used."
            )

        user = result.data[0]
        expires = user.get("verification_token_expires")

        if expires and datetime.fromisoformat(
            expires.replace("Z", "+00:00")
        ) < datetime.now(timezone.utc):
            return render_template(
                "login.html",
                error=(
                    "That verification link has expired. Log in and "
                    "resend a new one from your account page."
                )
            )

        supabase.table("users").update({
            "email_verified": True,
            "verification_token": None,
            "verification_token_expires": None,
        }).eq("id", user["id"]).execute()

        return render_template(
            "login.html",
            success="Your email is verified. You can log in now."
        )

    except Exception as e:
        return render_template(
            "login.html",
            error=f"Verification failed: {str(e)}"
        )


# ============================================================
# Forgot password
# ============================================================
@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        if not email:
            return render_template(
                "forgot_password.html",
                error="Enter the email on your account."
            )

        try:
            result = (
                supabase
                .table("users")
                .select("id, name, email")
                .eq("email", email)
                .execute()
            )

            if result.data:
                user = result.data[0]
                reset_token = secrets.token_urlsafe(32)
                reset_expires = (
                    datetime.now(timezone.utc)
                    + timedelta(minutes=RESET_TOKEN_EXPIRY_MINUTES)
                ).isoformat()

                supabase.table("users").update({
                    "reset_token": reset_token,
                    "reset_token_expires": reset_expires,
                }).eq("id", user["id"]).execute()

                reset_link = (
                    f"{request.url_root.rstrip('/')}"
                    f"/reset-password/{reset_token}"
                )
                send_email(
                    email,
                    "Reset your Clipgrab password",
                    f"""
                    <p>Hi {user.get('name', '')},</p>
                    <p>We received a request to reset your Clipgrab
                    password. This link expires in
                    {RESET_TOKEN_EXPIRY_MINUTES} minutes:</p>
                    <p><a href="{reset_link}">{reset_link}</a></p>
                    <p>If you didn't request this, you can safely
                    ignore this email — your password won't change.</p>
                    """
                )

            # Same message whether or not the email is registered, so
            # this can't be used to check which emails have accounts.
            return render_template(
                "forgot_password.html",
                success=(
                    "If an account exists for that email, a reset "
                    "link is on its way."
                )
            )

        except Exception as e:
            return render_template(
                "forgot_password.html",
                error=f"Something went wrong: {str(e)}"
            )

    return render_template("forgot_password.html")


# ============================================================
# Reset password
# ============================================================
@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        result = (
            supabase
            .table("users")
            .select("id, reset_token_expires")
            .eq("reset_token", token)
            .execute()
        )

        if not result.data:
            return render_template(
                "login.html",
                error="That reset link is invalid or has already been used."
            )

        user = result.data[0]
        expires = user.get("reset_token_expires")

        if expires and datetime.fromisoformat(
            expires.replace("Z", "+00:00")
        ) < datetime.now(timezone.utc):
            return render_template(
                "login.html",
                error="That reset link has expired. Request a new one."
            )

    except Exception as e:
        return render_template(
            "login.html",
            error=f"Something went wrong: {str(e)}"
        )

    if request.method == "POST":
        new_password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not new_password or not confirm_password:
            return render_template(
                "reset_password.html",
                token=token,
                error="Both fields are required."
            )

        if new_password != confirm_password:
            return render_template(
                "reset_password.html",
                token=token,
                error="Passwords do not match."
            )

        if len(new_password) < 6:
            return render_template(
                "reset_password.html",
                token=token,
                error="Password must be at least 6 characters."
            )

        try:
            supabase.table("users").update({
                "password_hash": generate_password_hash(new_password),
                "reset_token": None,
                "reset_token_expires": None,
            }).eq("id", user["id"]).execute()

            return render_template(
                "login.html",
                success="Password updated. You can log in now."
            )

        except Exception as e:
            return render_template(
                "reset_password.html",
                token=token,
                error=f"Something went wrong: {str(e)}"
            )

    return render_template("reset_password.html", token=token)


# ============================================================
# Account settings
# ============================================================
@app.route("/account")
def account():
    if "user_id" not in session:
        return redirect("/login")

    try:
        user = get_account_view(session["user_id"])
        if not user:
            session.clear()
            return redirect("/login")

        return render_template("account.html", user=user)

    except Exception as e:
        return render_template(
            "account.html",
            user=None,
            error=f"Unable to load account: {str(e)}"
        )


@app.route("/account/profile", methods=["POST"])
def update_profile():
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()

    if not name or not email:
        return render_template(
            "account.html",
            user=get_account_view(user_id),
            error="Name and email are both required."
        )

    try:
        current = get_account_view(user_id)
        email_changed = current and current.get("email") != email

        if email_changed:
            existing = (
                supabase
                .table("users")
                .select("id")
                .eq("email", email)
                .neq("id", user_id)
                .execute()
            )
            if existing.data:
                return render_template(
                    "account.html",
                    user=current,
                    error="That email is already in use by another account."
                )

        update_payload = {"name": name, "email": email}
        verify_link = None

        if email_changed:
            # Changing the email means it has to be verified again.
            verification_token = secrets.token_urlsafe(32)
            verification_expires = (
                datetime.now(timezone.utc)
                + timedelta(hours=VERIFY_TOKEN_EXPIRY_HOURS)
            ).isoformat()
            update_payload.update({
                "email_verified": False,
                "verification_token": verification_token,
                "verification_token_expires": verification_expires,
            })
            verify_link = (
                f"{request.url_root.rstrip('/')}"
                f"/verify-email/{verification_token}"
            )

        supabase.table("users").update(update_payload).eq(
            "id", user_id
        ).execute()

        session["user_name"] = name
        session["user_email"] = email

        success_msg = "Profile updated."

        if email_changed and verify_link:
            send_email(
                email,
                "Verify your new Clipgrab email",
                f"""
                <p>Hi {name},</p>
                <p>Confirm your new email address for Clipgrab:</p>
                <p><a href="{verify_link}">{verify_link}</a></p>
                """
            )
            success_msg += (
                " We've sent a verification link to your new email address."
            )

        return render_template(
            "account.html",
            user=get_account_view(user_id),
            success=success_msg
        )

    except Exception as e:
        return render_template(
            "account.html",
            user=get_account_view(user_id),
            error=f"Unable to update profile: {str(e)}"
        )


@app.route("/account/password", methods=["POST"])
def change_password():
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    try:
        result = (
            supabase
            .table("users")
            .select("id, password_hash")
            .eq("id", user_id)
            .single()
            .execute()
        )
        user = result.data

        if not user or not check_password_hash(
            user["password_hash"], current_password
        ):
            return render_template(
                "account.html",
                user=get_account_view(user_id),
                error="Current password is incorrect."
            )

        if not new_password or new_password != confirm_password:
            return render_template(
                "account.html",
                user=get_account_view(user_id),
                error="New passwords do not match."
            )

        if len(new_password) < 6:
            return render_template(
                "account.html",
                user=get_account_view(user_id),
                error="New password must be at least 6 characters."
            )

        supabase.table("users").update({
            "password_hash": generate_password_hash(new_password)
        }).eq("id", user_id).execute()

        return render_template(
            "account.html",
            user=get_account_view(user_id),
            success="Password changed successfully."
        )

    except Exception as e:
        return render_template(
            "account.html",
            user=get_account_view(user_id),
            error=f"Unable to change password: {str(e)}"
        )


@app.route("/account/resend-verification", methods=["POST"])
def resend_verification():
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]

    try:
        user = get_account_view(user_id)
        if not user:
            return redirect("/login")

        if user.get("email_verified"):
            return render_template(
                "account.html",
                user=user,
                success="Your email is already verified."
            )

        verification_token = secrets.token_urlsafe(32)
        verification_expires = (
            datetime.now(timezone.utc)
            + timedelta(hours=VERIFY_TOKEN_EXPIRY_HOURS)
        ).isoformat()

        supabase.table("users").update({
            "verification_token": verification_token,
            "verification_token_expires": verification_expires,
        }).eq("id", user_id).execute()

        verify_link = (
            f"{request.url_root.rstrip('/')}"
            f"/verify-email/{verification_token}"
        )
        send_email(
            user["email"],
            "Verify your Clipgrab email",
            f"""
            <p>Hi {user.get('name', '')},</p>
            <p>Verify your Clipgrab email address:</p>
            <p><a href="{verify_link}">{verify_link}</a></p>
            """
        )

        return render_template(
            "account.html",
            user=user,
            success="Verification email sent — check your inbox."
        )

    except Exception as e:
        return render_template(
            "account.html",
            user=get_account_view(user_id),
            error=f"Unable to resend verification email: {str(e)}"
        )


@app.route("/account/delete", methods=["POST"])
def delete_account():
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]
    password = request.form.get("password", "")

    try:
        result = (
            supabase
            .table("users")
            .select("id, password_hash")
            .eq("id", user_id)
            .single()
            .execute()
        )
        user = result.data

        if not user or not check_password_hash(
            user["password_hash"], password
        ):
            return render_template(
                "account.html",
                user=get_account_view(user_id),
                error="Password is incorrect. Your account was not deleted."
            )

        downloads = (
            supabase
            .table("downloads")
            .select("storage_path")
            .eq("user_id", user_id)
            .execute()
        ).data or []

        storage_paths = [
            item["storage_path"] for item in downloads
            if item.get("storage_path")
        ]

        if storage_paths:
            try:
                supabase.storage.from_("videos").remove(storage_paths)
            except Exception as storage_error:
                print("Account deletion storage cleanup warning:", storage_error)

        supabase.table("downloads").delete().eq(
            "user_id", user_id
        ).execute()
        supabase.table("users").delete().eq("id", user_id).execute()

        session.clear()

        return render_template(
            "login.html",
            success="Your account and all your videos have been deleted."
        )

    except Exception as e:
        return render_template(
            "account.html",
            user=get_account_view(user_id),
            error=f"Unable to delete account: {str(e)}"
        )


# ============================================================
# Watch saved video
# ============================================================
@app.route("/video/<download_id>")
def watch_video(download_id):
    if "user_id" not in session:
        return redirect("/login")

    try:
        result = (
            supabase
            .table("downloads")
            .select("*")
            .eq("id", download_id)
            .eq("user_id", session["user_id"])
            .single()
            .execute()
        )

        video = result.data
        if not video:
            return "Video not found", 404

        title = video.get("video_title", "Video")
        status = video.get("status")

        if status == "completed":
            storage_path = video.get("storage_path")
            if not storage_path:
                return "Video file not found.", 404

            signed = (
                supabase
                .storage
                .from_("videos")
                .create_signed_url(storage_path, 3600)
            )
            video_url = signed.get("signedURL") or signed.get("signedUrl")
            if not video_url:
                return "Could not create video URL.", 500

            return render_template(
                "watch.html",
                title=title,
                video_url=video_url,
                download_id=download_id,
                is_preview=False
            )

        if status == "processing" and video.get("preview_url"):
            # The final copy is still being saved to our own storage.
            # In the meantime, stream directly from the source's own
            # temporary link (the one yt-dlp resolved) so the user
            # doesn't have to wait. This link is short-lived and some
            # sources may restrict playback outside their own site,
            # so it's best-effort, not guaranteed.
            return render_template(
                "watch.html",
                title=title,
                video_url=video.get("preview_url"),
                download_id=download_id,
                is_preview=True
            )

        if status == "processing":
            return "Video is still processing. Please check back shortly.", 202

        return "Video is not available.", 400

    except Exception as e:
        return f"Unable to watch video: {e}", 500


# ============================================================
# Download saved video
# ============================================================
@app.route("/video/<download_id>/download")
def download_saved_video(download_id):
    if "user_id" not in session:
        return redirect("/login")

    try:
        result = (
            supabase
            .table("downloads")
            .select("*")
            .eq("id", download_id)
            .eq("user_id", session["user_id"])
            .single()
            .execute()
        )

        video = result.data
        if not video:
            return "Video not found", 404

        if video.get("status") != "completed":
            return "Video is not ready yet.", 400

        storage_path = video.get("storage_path")
        if not storage_path:
            return "Video file not found.", 404

        signed = (
            supabase
            .storage
            .from_("videos")
            .create_signed_url(storage_path, 3600)
        )
        video_url = signed.get("signedURL") or signed.get("signedUrl")
        if not video_url:
            return "Could not create download URL.", 500

        return redirect(video_url)

    except Exception as e:
        return f"Unable to download video: {e}", 500


# ============================================================
# Routes
# ============================================================
@app.route("/", methods=["GET"])
def home():
    return render_template("home.html")


@app.route("/download", methods=["POST"])
def download():
    if "user_id" not in session:
        return redirect("/login")

    url = request.form.get("url", "").strip()
    if not url:
        return render_template(
            "home.html",
            error="Please enter a video URL."
        )

    try:
        ydl_opts = {
            "cookiefile": cookie_file,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = []

        for f in info.get("formats", []):
            if f.get("vcodec") == "none":
                continue

            format_id = str(f.get("format_id", ""))
            ext = f.get("ext", "")
            height = f.get("height")

            if not format_id or not height:
                continue

            filesize = f.get("filesize") or f.get("filesize_approx") or 0
            filesize_mb = (
                round(filesize / (1024 * 1024), 2)
                if filesize else "N/A"
            )

            formats.append({
                "format_id": format_id,
                "ext": ext,
                "quality": f"{height}p",
                "filesize": (
                    f"{filesize_mb} MB"
                    if filesize_mb != "N/A"
                    else "N/A"
                ),
                "has_audio": f.get("acodec") != "none",
            })

        # Highest quality first.
        formats.sort(
            key=lambda x: int(x["quality"][:-1])
            if x["quality"] != "N/A" else 0,
            reverse=True
        )

        # Remove duplicate quality/ext combinations from the UI.
        unique_formats = []
        seen = set()

        for item in formats:
            key = (item["quality"], item["ext"])
            if key not in seen:
                seen.add(key)
                unique_formats.append(item)

        if not unique_formats:
            return render_template(
                "home.html",
                error="No downloadable video formats were found."
            )

        return render_template(
            "home.html",
            formats=unique_formats,
            video_title=info.get("title", "Video"),
            video_url=url
        )

    except Exception as e:
        return render_template(
            "home.html",
            error=f"Unable to fetch video: {str(e)}"
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
