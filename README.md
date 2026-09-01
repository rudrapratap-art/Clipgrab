# Clipgrab — Video Downloader

A fast, private, and secure web application for downloading videos from YouTube, Instagram, Facebook, TikTok, and more. Built with Flask, yt-dlp, and Supabase.

## Features

✨ **Core Features**
- Download videos from multiple platforms (YouTube, Instagram, Facebook, TikTok)
- Choose video quality (360p to 4K when available)
- Automatic video + audio merge into MP4 format
- Watch downloaded videos directly in browser
- Save and manage your download history
- User accounts with secure authentication
- Dashboard with search and status filtering

🔧 **Advanced Features**
- Retry failed downloads
- Multi-format selection including video-only streams
- Secure session cookies (HTTPONLY, SAMESITE)
- YouTube cookie-based authentication support
- Responsive dark UI design
- Real-time download status tracking

🔒 **Security**
- User-scoped download records
- Secure password hashing
- No local file storage (temporary cleanup)
- Environment-based secrets management
- CSRF and injection protection

## Technology Stack

- **Backend:** Flask (Python)
- **Video Download:** yt-dlp with FFmpeg
- **Database:** Supabase (PostgreSQL)
- **Storage:** Supabase Storage (S3-compatible)
- **Authentication:** Flask Sessions + Supabase Auth
- **Hosting:** Render (recommended for production)

## Installation

### Prerequisites

- Python 3.8+
- FFmpeg
- Git (for version control)
- pip (Python package manager)

### Local Setup

1. **Clone the repository:**

```bash
git clone https://github.com/YOUR_USERNAME/clipgrab.git
cd clipgrab
```

2. **Rename .gitignore:**

```bash
# On Windows PowerShell
Rename-Item file.gitignore .gitignore

# On macOS/Linux
mv file.gitignore .gitignore
```

3. **Create environment configuration:**

Create `file.env` (this will NOT be pushed to GitHub):

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your_service_key_here
FLASK_SECRET_KEY=your_random_secret_key
YOUTUBE_COOKIES=optional_cookie_content
```

4. **Install dependencies:**

```bash
python -m pip install -r requirements.txt
```

5. **Verify FFmpeg:**

```bash
ffmpeg -version
```

If FFmpeg is not installed:
- **Windows:** Download from https://ffmpeg.org/download.html
- **macOS:** `brew install ffmpeg`
- **Linux:** `sudo apt-get install ffmpeg`

6. **Run locally:**

```bash
python app.py
```

Visit `http://127.0.0.1:5000` in your browser.

## Environment Variables

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `SUPABASE_URL` | Your Supabase project URL | `https://xxx.supabase.co` |
| `SUPABASE_SERVICE_KEY` | Supabase service role key (keep secret!) | Your 40-char key |
| `FLASK_SECRET_KEY` | Flask session secret (auto-generated on Render) | Random 32+ char string |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `YOUTUBE_COOKIES` | Netscape-format YouTube cookies | None (uses local `cookies.txt`) |
| `FLASK_COOKIE_SECURE` | Enable HTTPS-only cookies (1 for Render, 0 for local) | `0` |

### Local vs. Production

**Local Development (file.env):**
```env
FLASK_COOKIE_SECURE=0
YOUTUBE_COOKIES=
```

**Production (Render Environment):**
```env
FLASK_COOKIE_SECURE=1
YOUTUBE_COOKIES=your_cookies_here
```

## Local Workflow

### Running the App

```bash
python app.py
```

The Flask development server runs on `http://127.0.0.1:5000` by default.

### Testing Features

1. **Register & Login:**
   - Create test account at `/register`
   - Login with credentials at `/login`

2. **Download Video:**
   - Enter YouTube/Instagram URL
   - Select quality from available formats
   - Watch download progress in dashboard

3. **View Downloads:**
   - Visit `/dashboard` to see all your downloads
   - Click "▶ Watch" to play in browser
   - Click "↓ Download" to get file
   - Click "🗑 Delete" to remove

4. **Retry Failed Downloads:**
   - Failed downloads show "🔄 Retry" button
   - Retries reuse same format and original URL
   - Useful for temporary connection issues

### Code Structure

```
app.py                 # Main Flask application
├── Routes           # /download, /download-selected, /retry, /watch, etc.
├── Database         # Supabase client & users/downloads tables
├── Storage          # Supabase videos bucket
├── UI               # HTML_PAGE, DASHBOARD_PAGE, LOGIN_PAGE, etc.
└── Helpers          # yt-dlp options, format filtering, cleanup

requirements.txt     # Python dependencies
Procfile            # Gunicorn command for production
render.yaml         # Render blueprint configuration
apt.txt             # System packages (FFmpeg)
.gitignore          # Git ignore patterns
file.env            # Local secrets (NOT in git)
```

## Deployment to Render

### Step 1: Prepare Supabase

1. Go to https://supabase.com
2. Create project or use existing
3. In **Project Settings → API**, copy:
   - `Project URL` → `SUPABASE_URL`
   - `Service Role Secret` → `SUPABASE_SERVICE_KEY`

### Step 2: Create Database Tables

In Supabase **SQL Editor**, run:

```sql
-- Users table (usually auto-created by auth)
create table if not exists users (
  id uuid primary key default auth.uid(),
  name text,
  email text unique,
  password_hash text,
  last_login timestamptz,
  created_at timestamptz default now()
);

-- Downloads table
create table if not exists downloads (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  video_title text,
  original_url text,
  thumbnail_url text,
  format_id text,
  quality text,
  format text,
  storage_path text,
  status text default 'processing',
  error_message text,
  created_at timestamptz default now()
);

-- Storage bucket
create bucket videos;
```

### Step 3: Create Storage Bucket Policy

In Supabase **Storage → Policies**, add:

```sql
-- Allow users to upload their own videos
create policy "Users can upload their own videos" on storage.objects
  for insert with check (auth.uid()::text = (storage.foldername(name))[1]);

-- Allow users to download their own videos
create policy "Users can download their own videos" on storage.objects
  for select using (auth.uid()::text = (storage.foldername(name))[1]);

-- Allow users to delete their own videos
create policy "Users can delete their own videos" on storage.objects
  for delete using (auth.uid()::text = (storage.foldername(name))[1]);
```

### Step 4: GitHub Repository Setup

1. Push your code to GitHub:

```bash
git add .
git commit -m "Prepare for Render deployment"
git push origin main
```

2. Verify `.gitignore` includes:
```
.env
file.env
cookies.txt
__pycache__/
*.pyc
```

3. Check that secrets are NOT tracked:
```bash
git status
git ls-files file.env cookies.txt
```

### Step 5: Deploy on Render

1. Go to https://render.com
2. Sign in with GitHub
3. Click **New +** → **Blueprint**
4. Select your repository
5. Render will detect `render.yaml`
6. Click **Create New Blueprint Instance**
7. Wait for deployment (2-5 minutes)

### Step 6: Set Environment Variables

In Render **Dashboard → Service → Environment**:

```
SUPABASE_URL = https://xxx.supabase.co
SUPABASE_SERVICE_KEY = your_service_key
YOUTUBE_COOKIES = optional_cookies
FLASK_COOKIE_SECURE = 1
```

**Do NOT** include `FLASK_SECRET_KEY` — Render will auto-generate it.

### Step 7: Verify Deployment

1. Check **Logs** for successful startup:
```
Listening on port ...
```

2. Visit your service URL (e.g., `https://clipgrab-xxx.onrender.com`)

3. Test full workflow:
   - Register account
   - Login
   - Download video
   - View dashboard
   - Retry failed download
   - Watch & delete

## YouTube Cookies Setup

### Why Cookies?

Some YouTube videos require authentication cookies to access playable formats.

### How to Export Cookies

#### Using Browser Extension (Recommended)

1. Install **EditThisCookie** or **Get cookies.txt** extension
2. Go to youtube.com and login
3. Click extension icon
4. Export as Netscape format
5. Copy full cookie content

#### Using DevTools

1. Open YouTube
2. Press `F12` → **Application** → **Cookies**
3. Copy all cookies manually in Netscape format:
```
.youtube.com	TRUE	/	TRUE	1704067200	cookie_name	cookie_value
```

#### Using Environment Variable

Set in Render environment:

```
YOUTUBE_COOKIES=<full_netscape_cookie_content>
```

OR create local `cookies.txt` (not pushed to GitHub).

## Troubleshooting

### "Supabase environment variables are missing"

**Solution:** Check Render **Environment** tab contains `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`.

### "ffmpeg not found"

**Solution:** Render will install via `apt.txt`. Local machines need:
- Windows: Download from ffmpeg.org
- macOS: `brew install ffmpeg`
- Linux: `sudo apt-get install ffmpeg`

### Download fails with "Requested format is not available"

**Possible causes:**
- Format ID changed for that video
- YouTube enforces region/device restrictions
- Video removed or made private
- Cookies expired

**Solutions:**
- Try `🔄 Retry` button
- Update YouTube cookies
- Try different quality
- Check if video is publicly available

### "HTTP Error 403: Forbidden"

**Cause:** YouTube blocked access (signature extraction or stream URL authentication)

**Solutions:**
1. Ensure yt-dlp is up-to-date:
   ```bash
   pip install -U yt-dlp yt-dlp-ejs
   ```

2. Provide fresh YouTube cookies:
   - Log into YouTube in browser
   - Export cookies using EditThisCookie
   - Update `YOUTUBE_COOKIES` in Render

3. Install Deno for JavaScript challenge solving:
   ```bash
   # Local: https://deno.land/manual/getting_started/installation
   # Render: Add `deno` to apt.txt
   ```

### Download stuck in "Processing"

**Cause:** yt-dlp hung, ⏳ polling stopped, or storage upload failed

**Solutions:**
1. Wait 5+ minutes (may still complete)
2. Refresh page (polling updates status)
3. Delete and retry
4. Check Render logs for errors

### Videos won't play in browser

**Cause:** Unsupported codec or container format

**Solutions:**
- App forces MP4 output; all videos should be playable
- Try different browser (Chrome, Firefox)
- Clear browser cache
- Download file and play locally

### Can't login after deploy

**Cause:** `FLASK_SECRET_KEY` mismatch or changed

**Solutions:**
1. Clear browser cookies:
   - DevTools → Application → Cookies → Delete all
2. Or restart service in Render (generates new key)
3. Logout all users and re-login

## Security Notes

### What's Stored

- **Users:** Name, email, hashed password (never plain text)
- **Downloads:** Video title, URL, Supabase storage path
- **Videos:** Encrypted in Supabase storage, only owner can access

### What's NOT Stored

- Video files on server (only in Supabase bucket)
- Raw cookies in database
- User session data (only Flask session cookie)
- Downloaded video files locally

### Best Practices

1. **Rotate Credentials:**
   - If `file.env` was exposed, rotate Supabase keys in dashboard
   - Regenerate `FLASK_SECRET_KEY` on Render

2. **Session Security:**
   - Production sessions are HTTPS-only (`FLASK_COOKIE_SECURE=1`)
   - Cookies are HTTPOnly (can't be accessed by JavaScript)
   - SameSite=Lax prevents cross-site request forgery

3. **Database:**
   - Supabase enforces row-level security (RLS)
   - Users can only see their own downloads
   - Service key is server-side only

4. **YouTube Cookies:**
   - Keep out of version control
   - Rotate periodically
   - Don't share in logs or error messages

## Contributing

1. Fork repository
2. Create feature branch: `git checkout -b feature/my-feature`
3. Commit changes: `git commit -m "Add my feature"`
4. Push: `git push origin feature/my-feature`
5. Open Pull Request

## License

This project is provided as-is for personal use. Respect YouTube Terms of Service.

## Support

### Common Issues

- **Video formats missing:** Update yt-dlp: `pip install -U yt-dlp`
- **Slow downloads:** Check internet speed, Supabase region
- **Missing dependencies:** Verify `requirements.txt` installed

### Debugging

Enable verbose logging by adding to `app.py`:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Check Render logs:
```
Service → Logs (live tail)
```

### Contact

For bugs or feature requests, open an issue on GitHub.

---

**Last Updated:** September 2026  
**Version:** 1.0.0
