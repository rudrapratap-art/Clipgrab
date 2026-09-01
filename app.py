from flask import Flask, request, render_template_string, redirect, session
import os
import tempfile
import re
import uuid
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

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise RuntimeError("Supabase environment variables are missing.")

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY
)

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
# Modern Dark UI
# ============================================================
HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#050b18">
    <title>Clipgrab — Video Downloader</title>

    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">

    <style>
        :root {
            --bg: #030712;
            --bg-soft: #07111f;
            --panel: rgba(10, 20, 38, 0.72);
            --panel-strong: rgba(13, 25, 46, 0.92);
            --border: rgba(148, 163, 184, 0.16);
            --text: #f8fafc;
            --muted: #94a3b8;
            --cyan: #22d3ee;
            --blue: #3b82f6;
            --purple: #a855f7;
            --green: #22c55e;
            --red: #fb3b5a;
            --shadow: 0 25px 80px rgba(0, 0, 0, 0.42);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        html {
            scroll-behavior: smooth;
        }

        body {
            min-height: 100vh;
            color: var(--text);
            font-family: "Inter", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background:
                radial-gradient(circle at 12% 8%, rgba(34, 211, 238, 0.14), transparent 27%),
                radial-gradient(circle at 88% 12%, rgba(168, 85, 247, 0.13), transparent 28%),
                radial-gradient(circle at 50% 75%, rgba(59, 130, 246, 0.08), transparent 32%),
                var(--bg);
            overflow-x: hidden;
        }

        body::before {
            content: "";
            position: fixed;
            inset: 0;
            pointer-events: none;
            background-image:
                linear-gradient(rgba(255,255,255,0.018) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255,255,255,0.018) 1px, transparent 1px);
            background-size: 42px 42px;
            mask-image: linear-gradient(to bottom, black, transparent 85%);
            z-index: -1;
        }

        a {
            color: inherit;
            text-decoration: none;
        }

        button,
        input {
            font: inherit;
        }

        .page {
            width: min(1180px, calc(100% - 28px));
            margin: 18px auto;
            border: 1px solid var(--border);
            border-radius: 26px;
            background: rgba(3, 7, 18, 0.64);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            box-shadow: var(--shadow);
            overflow: hidden;
        }

        /* =====================================================
           Header
           ===================================================== */
        .navbar {
            min-height: 82px;
            padding: 0 34px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid var(--border);
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 25px;
            font-weight: 800;
            letter-spacing: -0.8px;
        }

        .brand-icon {
            width: 42px;
            height: 42px;
            border-radius: 12px;
            display: grid;
            place-items: center;
            border: 1px solid rgba(34, 211, 238, 0.55);
            background: linear-gradient(145deg, rgba(34,211,238,0.18), rgba(59,130,246,0.10));
            box-shadow: 0 0 25px rgba(34, 211, 238, 0.12);
        }

        .brand-icon svg {
            width: 23px;
            height: 23px;
        }

        .nav-links {
            display: flex;
            align-items: center;
            gap: 30px;
            color: #cbd5e1;
            font-size: 14px;
        }

        .nav-links a {
            transition: color .2s ease;
        }

        .nav-links a:hover {
            color: white;
        }

        .nav-cta {
            padding: 11px 18px;
            border-radius: 11px;
            background: linear-gradient(100deg, var(--cyan), var(--blue) 55%, var(--purple));
            color: white !important;
            font-weight: 700;
            box-shadow: 0 10px 30px rgba(59, 130, 246, 0.25);
        }

        .mobile-menu {
            display: none;
            width: 42px;
            height: 42px;
            border: 1px solid var(--border);
            border-radius: 12px;
            background: var(--panel);
            color: white;
        }

        /* =====================================================
           Hero
           ===================================================== */
        .hero {
            text-align: center;
            padding: 82px 32px 62px;
            position: relative;
        }

        .hero::after {
            content: "";
            position: absolute;
            width: 480px;
            height: 260px;
            left: 50%;
            top: 100px;
            transform: translateX(-50%);
            background: radial-gradient(circle, rgba(34,211,238,0.10), transparent 68%);
            filter: blur(20px);
            pointer-events: none;
            z-index: -1;
        }

        .eyebrow {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 7px 12px;
            margin-bottom: 22px;
            border: 1px solid rgba(34, 211, 238, 0.20);
            border-radius: 999px;
            background: rgba(34, 211, 238, 0.06);
            color: #a5f3fc;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: .4px;
        }

        .eyebrow-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: var(--cyan);
            box-shadow: 0 0 12px var(--cyan);
        }

        .hero h1 {
            max-width: 850px;
            margin: 0 auto 18px;
            font-size: clamp(42px, 6vw, 72px);
            line-height: 1.02;
            letter-spacing: -3.5px;
            font-weight: 800;
        }

        .gradient-text {
            background: linear-gradient(90deg, #f8fafc 10%, #67e8f9 50%, #c084fc 92%);
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
        }

        .hero p {
            max-width: 760px;
            margin: 0 auto;
            color: #a8b4c7;
            font-size: 16px;
            line-height: 1.7;
        }

        /* =====================================================
           Downloader
           ===================================================== */
        .download-form {
            width: min(820px, 100%);
            margin: 34px auto 22px;
        }

        .url-box {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 7px;
            border-radius: 17px;
            border: 1px solid rgba(34, 211, 238, 0.34);
            background: rgba(5, 14, 29, 0.82);
            box-shadow:
                0 0 0 1px rgba(168,85,247,0.08),
                0 18px 55px rgba(0,0,0,0.28),
                0 0 35px rgba(34,211,238,0.06);
            transition: border-color .25s ease, box-shadow .25s ease;
        }

        .url-box:focus-within {
            border-color: rgba(34, 211, 238, 0.75);
            box-shadow:
                0 0 0 4px rgba(34,211,238,0.07),
                0 18px 55px rgba(0,0,0,0.3),
                0 0 40px rgba(34,211,238,0.10);
        }

        .url-icon {
            flex: 0 0 42px;
            display: grid;
            place-items: center;
            color: var(--cyan);
        }

        .url-icon svg {
            width: 23px;
            height: 23px;
        }

        .url-input {
            min-width: 0;
            flex: 1;
            border: 0;
            outline: 0;
            background: transparent;
            color: white;
            padding: 15px 4px;
            font-size: 16px;
        }

        .url-input::placeholder {
            color: #64748b;
        }

        .download-main {
            flex: 0 0 auto;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 9px;
            min-width: 170px;
            padding: 14px 21px;
            border: 0;
            border-radius: 12px;
            color: white;
            font-weight: 800;
            cursor: pointer;
            background: linear-gradient(100deg, #06b6d4, #3b82f6 55%, #9333ea);
            box-shadow: 0 12px 30px rgba(59,130,246,0.25);
            transition: transform .2s ease, filter .2s ease, box-shadow .2s ease;
        }

        .download-main:hover {
            transform: translateY(-2px);
            filter: brightness(1.08);
            box-shadow: 0 16px 35px rgba(59,130,246,0.34);
        }

        .download-main:active {
            transform: translateY(0);
        }

        .download-main.loading {
            pointer-events: none;
            opacity: .82;
        }

        .spinner {
            width: 17px;
            height: 17px;
            border: 2px solid rgba(255,255,255,.35);
            border-top-color: white;
            border-radius: 50%;
            animation: spin .7s linear infinite;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        /* =====================================================
           Platforms
           ===================================================== */
        .platform-title {
            margin: 30px 0 16px;
            color: #cbd5e1;
            font-size: 13px;
            font-weight: 600;
        }

        .platforms {
            display: flex;
            justify-content: center;
            flex-wrap: wrap;
            gap: 11px;
        }

        .platform {
            min-width: 145px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 9px;
            padding: 12px 17px;
            border-radius: 13px;
            border: 1px solid var(--border);
            background: rgba(9, 18, 34, 0.70);
            color: #dbeafe;
            font-size: 13px;
            font-weight: 700;
            transition: transform .2s ease, border-color .2s ease, background .2s ease;
        }

        .platform:hover {
            transform: translateY(-2px);
            border-color: rgba(34,211,238,0.32);
            background: rgba(14, 30, 52, .86);
        }

        .platform-icon {
            width: 27px;
            height: 27px;
            display: grid;
            place-items: center;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 900;
        }

        .yt { background: #ff0033; }
        .ig { background: linear-gradient(135deg, #f97316, #ec4899, #8b5cf6); }
        .fb { background: #1877f2; }
        .tt { background: #050505; border: 1px solid #334155; }

        /* =====================================================
           Results
           ===================================================== */
        .result-area {
            width: min(900px, calc(100% - 40px));
            margin: 0 auto 65px;
        }

        .result-card,
        .error-card {
            border: 1px solid var(--border);
            border-radius: 20px;
            background: linear-gradient(145deg, rgba(12, 28, 50, .86), rgba(5, 12, 25, .86));
            backdrop-filter: blur(16px);
            box-shadow: 0 20px 55px rgba(0,0,0,.25);
            padding: 23px;
            animation: appear .4s ease both;
        }

        @keyframes appear {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .result-head {
            display: flex;
            align-items: flex-start;
            gap: 13px;
            margin-bottom: 18px;
        }

        .success-icon,
        .error-icon {
            flex: 0 0 38px;
            width: 38px;
            height: 38px;
            border-radius: 11px;
            display: grid;
            place-items: center;
        }

        .success-icon {
            background: rgba(34,197,94,.11);
            border: 1px solid rgba(34,197,94,.28);
            color: #4ade80;
        }

        .error-icon {
            background: rgba(251,59,90,.11);
            border: 1px solid rgba(251,59,90,.28);
            color: #fb7185;
        }

        .result-title {
            color: #e2e8f0;
            font-size: 13px;
            line-height: 1.5;
        }

        .video-title {
            margin-top: 3px;
            color: white;
            font-weight: 700;
            overflow-wrap: anywhere;
        }

        .quality-list {
            display: grid;
            gap: 10px;
        }

        .quality-option {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            padding: 14px;
            border: 1px solid rgba(148,163,184,.12);
            border-radius: 14px;
            background: rgba(255,255,255,.025);
            transition: transform .18s ease, border-color .18s ease, background .18s ease;
        }

        .quality-option:hover {
            transform: translateY(-1px);
            border-color: rgba(34,211,238,.28);
            background: rgba(34,211,238,.035);
        }

        .quality-left {
            display: flex;
            align-items: center;
            gap: 12px;
            min-width: 0;
        }

        .quality-badge {
            min-width: 57px;
            padding: 7px 8px;
            border-radius: 9px;
            text-align: center;
            background: rgba(59,130,246,.12);
            border: 1px solid rgba(59,130,246,.22);
            color: #93c5fd;
            font-size: 12px;
            font-weight: 800;
        }

        .quality-name {
            color: #f8fafc;
            font-weight: 700;
        }

        .quality-size {
            margin-top: 3px;
            color: #64748b;
            font-size: 12px;
        }

        .quality-download {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 7px;
            flex: 0 0 auto;
            padding: 10px 14px;
            border-radius: 10px;
            background: linear-gradient(100deg, #06b6d4, #2563eb);
            color: white;
            font-size: 12px;
            font-weight: 800;
            box-shadow: 0 7px 20px rgba(37,99,235,.20);
            transition: transform .2s ease, filter .2s ease;
        }

        .quality-download:hover {
            transform: translateY(-1px);
            filter: brightness(1.08);
        }

        .error-card {
            display: flex;
            align-items: flex-start;
            gap: 12px;
            color: #fecdd3;
            line-height: 1.6;
        }

        /* =====================================================
           Features
           ===================================================== */
        .features-section {
            padding: 70px 32px 64px;
            border-top: 1px solid var(--border);
            background: rgba(4, 11, 23, .48);
        }

        .section-heading {
            text-align: center;
            margin-bottom: 38px;
        }

        .section-heading h2 {
            font-size: clamp(26px, 4vw, 38px);
            letter-spacing: -1.5px;
            margin-bottom: 9px;
        }

        .section-heading p {
            color: var(--muted);
            font-size: 14px;
        }

        .features {
            max-width: 1050px;
            margin: 0 auto;
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 18px;
        }

        .feature {
            min-height: 230px;
            padding: 28px 24px;
            text-align: center;
            border: 1px solid var(--border);
            border-radius: 20px;
            background: linear-gradient(145deg, rgba(11,28,47,.72), rgba(7,15,29,.68));
            position: relative;
            overflow: hidden;
            transition: transform .25s ease, border-color .25s ease;
        }

        .feature::before {
            content: "";
            position: absolute;
            width: 150px;
            height: 150px;
            top: -75px;
            left: 50%;
            transform: translateX(-50%);
            background: radial-gradient(circle, rgba(34,211,238,.12), transparent 70%);
            pointer-events: none;
        }

        .feature:nth-child(2)::before {
            background: radial-gradient(circle, rgba(168,85,247,.14), transparent 70%);
        }

        .feature:nth-child(3)::before {
            background: radial-gradient(circle, rgba(59,130,246,.14), transparent 70%);
        }

        .feature:hover {
            transform: translateY(-5px);
            border-color: rgba(148,163,184,.28);
        }

        .feature-icon {
            width: 60px;
            height: 60px;
            margin: 0 auto 19px;
            border-radius: 16px;
            display: grid;
            place-items: center;
            border: 1px solid rgba(34,211,238,.40);
            background: rgba(34,211,238,.08);
            color: var(--cyan);
            box-shadow: 0 0 28px rgba(34,211,238,.08);
        }

        .feature:nth-child(2) .feature-icon {
            color: #c084fc;
            border-color: rgba(192,132,252,.38);
            background: rgba(168,85,247,.08);
        }

        .feature:nth-child(3) .feature-icon {
            color: #60a5fa;
            border-color: rgba(96,165,250,.38);
            background: rgba(59,130,246,.08);
        }

        .feature h3 {
            font-size: 19px;
            margin-bottom: 10px;
        }

        .feature p {
            max-width: 280px;
            margin: auto;
            color: #94a3b8;
            font-size: 13px;
            line-height: 1.65;
        }

        .stats {
            max-width: 1050px;
            margin: 28px auto 0;
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            border: 1px solid var(--border);
            border-radius: 17px;
            background: rgba(255,255,255,.018);
            overflow: hidden;
        }

        .stat {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            padding: 20px;
            border-right: 1px solid var(--border);
        }

        .stat:last-child {
            border-right: 0;
        }

        .stat-icon {
            color: var(--cyan);
            font-size: 22px;
        }

        .stat strong {
            display: block;
            color: #e2e8f0;
            font-size: 14px;
        }

        .stat span {
            color: #64748b;
            font-size: 11px;
        }

        /* =====================================================
           Footer
           ===================================================== */
        footer {
            padding: 45px 38px 25px;
            border-top: 1px solid var(--border);
            background: rgba(2, 6, 16, .65);
        }

        .footer-grid {
            display: grid;
            grid-template-columns: 1.5fr 1fr 1fr 1fr;
            gap: 40px;
            max-width: 1050px;
            margin: 0 auto;
        }

        .footer-brand p {
            max-width: 280px;
            margin-top: 13px;
            color: #64748b;
            font-size: 12px;
            line-height: 1.7;
        }

        .footer h4 {
            margin-bottom: 14px;
            font-size: 13px;
        }

        .footer a {
            display: block;
            width: fit-content;
            margin-bottom: 9px;
            color: #64748b;
            font-size: 12px;
            transition: color .2s ease;
        }

        .footer a:hover {
            color: #cbd5e1;
        }

        .copyright {
            max-width: 1050px;
            margin: 34px auto 0;
            padding-top: 20px;
            border-top: 1px solid var(--border);
            text-align: center;
            color: #475569;
            font-size: 11px;
        }

        /* =====================================================
           Responsive
           ===================================================== */
        @media (max-width: 820px) {
            .navbar {
                padding: 0 20px;
            }

            .nav-links {
                display: none;
            }

            .nav-links.show-mobile {
                position: absolute;
                top: 82px;
                right: 20px;
                left: 20px;
                z-index: 10;
                display: flex;
                flex-direction: column;
                gap: 16px;
                padding: 18px;
                border: 1px solid var(--border);
                border-radius: 14px;
                background: rgba(7, 17, 31, .96);
                box-shadow: var(--shadow);
            }

            .mobile-menu {
                display: grid;
                place-items: center;
            }

            .hero {
                padding: 65px 20px 50px;
            }

            .hero h1 {
                letter-spacing: -2px;
            }

            .features {
                grid-template-columns: 1fr;
                max-width: 520px;
            }

            .feature {
                min-height: auto;
            }

            .stats {
                grid-template-columns: 1fr;
            }

            .stat {
                border-right: 0;
                border-bottom: 1px solid var(--border);
            }

            .stat:last-child {
                border-bottom: 0;
            }

            .footer-grid {
                grid-template-columns: 1fr 1fr;
            }
        }

        @media (max-width: 620px) {
            .page {
                width: calc(100% - 12px);
                margin: 6px auto;
                border-radius: 20px;
            }

            .brand {
                font-size: 21px;
            }

            .brand-icon {
                width: 37px;
                height: 37px;
            }

            .hero h1 {
                font-size: clamp(36px, 11vw, 52px);
            }

            .hero p {
                font-size: 14px;
            }

            .download-form {
                margin-top: 28px;
            }

            .url-box {
                flex-direction: column;
                align-items: stretch;
                padding: 8px;
            }

            .url-input {
                width: 100%;
                padding: 13px 10px 13px 2px;
            }

            .url-icon {
                display: none;
            }

            .download-main {
                width: 100%;
            }

            .platform {
                flex: 1 1 calc(50% - 10px);
                min-width: 0;
            }

            .result-area {
                width: calc(100% - 22px);
            }

            .quality-option {
                align-items: stretch;
                flex-direction: column;
            }

            .quality-download {
                width: 100%;
            }

            .features-section {
                padding: 55px 17px;
            }

            footer {
                padding: 38px 22px 22px;
            }

            .footer-grid {
                grid-template-columns: 1fr 1fr;
                gap: 30px 20px;
            }

            .footer-brand {
                grid-column: 1 / -1;
            }
        }

        @media (max-width: 390px) {
            .platform {
                flex-basis: 100%;
            }

            .footer-grid {
                grid-template-columns: 1fr;
            }
        }

        @media (prefers-reduced-motion: reduce) {
            html {
                scroll-behavior: auto;
            }

            *,
            *::before,
            *::after {
                animation-duration: .01ms !important;
                transition-duration: .01ms !important;
            }
        }
    </style>
</head>

<body>
<div class="page">

    <!-- Header -->
    <header class="navbar">
        <a href="/" class="brand" aria-label="Clipgrab home">
            <span class="brand-icon">
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M12 3v10m0 0 4-4m-4 4-4-4M5 17v2h14v-2"
                          stroke="currentColor" stroke-width="2"
                          stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </span>
            Clipgrab
        </a>

        <nav class="nav-links">
            <a href="#features">Features</a>
            <a href="#how-it-works">How It Works</a>
            <a href="#faq">FAQ</a>
            <a href="#features">Pricing</a>
            <a href="#download">Log in</a>
            <a href="#download" class="nav-cta">Get Started</a>
        </nav>

        <button class="mobile-menu" type="button" aria-label="Open menu"
                onclick="document.querySelector('.nav-links').classList.toggle('show-mobile')">
            ☰
        </button>
    </header>

    <!-- Hero -->
    <main id="download" class="hero">
        <div class="eyebrow">
            <span class="eyebrow-dot"></span>
            FAST • PRIVATE • NO REGISTRATION
        </div>

        <h1>
            Download Videos in
            <span class="gradient-text">Seconds</span>
        </h1>

        <p>
            Save videos from YouTube, Instagram, Facebook, TikTok and more.
            Fast, simple, and no software required.
        </p>

        <form class="download-form" action="/download" method="post" onsubmit="startLoading(this)">
            <div class="url-box">
                <span class="url-icon">
                    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                        <path d="M10 13a5 5 0 0 0 7.07.07l2-2a5 5 0 0 0-7.07-7.07l-1.15 1.15"
                              stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
                        <path d="M14 11a5 5 0 0 0-7.07-.07l-2 2A5 5 0 0 0 7 20l1.15-1.15"
                              stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
                    </svg>
                </span>

                <input
                    id="video-url"
                    class="url-input"
                    type="text"
                    name="url"
                    autocomplete="url"
                    placeholder="Paste video URL here..."
                    required
                >

                <button id="download-button" class="download-main" type="submit">
                    <span class="button-icon">
                        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                            <path d="M12 3v11m0 0 4-4m-4 4-4-4M5 19h14"
                                  stroke="currentColor" stroke-width="2"
                                  stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                    </span>
                    <span class="button-text">Download</span>
                </button>
            </div>
        </form>

        <div class="platform-title">Supports popular platforms</div>

        <div class="platforms">
            <div class="platform">
                <span class="platform-icon yt">▶</span>
                YouTube
            </div>
            <div class="platform">
                <span class="platform-icon ig">◎</span>
                Instagram
            </div>
            <div class="platform">
                <span class="platform-icon fb">f</span>
                Facebook
            </div>
            <div class="platform">
                <span class="platform-icon tt">♪</span>
                TikTok
            </div>
        </div>
    </main>

    <!-- Results -->
    {% if formats or error %}
    <section class="result-area" id="results">

        {% if formats %}
        <div class="result-card">
            <div class="result-head">
                <div class="success-icon">
                    <svg width="19" height="19" viewBox="0 0 24 24" fill="none">
                        <path d="m5 12 4 4L19 6"
                              stroke="currentColor" stroke-width="2.2"
                              stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                </div>
                <div>
                    <div class="result-title">Video found</div>
                    <div class="video-title">{{ video_title }}</div>
                </div>
            </div>

            <div class="quality-list">
                {% for format in formats %}
                <div class="quality-option">
                    <div class="quality-left">
                        <div class="quality-badge">{{ format.quality }}</div>
                        <div>
                            <div class="quality-name">{{ format.ext|upper }} Video</div>
                            <div class="quality-size">Size: {{ format.filesize }}</div>
                        </div>
                    </div>

                    <form action="/download-selected" method="POST" style="margin:0;" onsubmit="startSelectedLoading(this)">
                        <input
                            type="hidden"
                            name="video_url"
                            value="{{ video_url }}"
                        >

                        <input
                            type="hidden"
                            name="format_id"
                            value="{{ format.format_id }}"
                        >

                        <input
                            type="hidden"
                            name="has_audio"
                            value="{{ format.has_audio }}"
                        >

                        <button type="submit" class="quality-download">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
            <path d="M12 3v12m0 0 4-4m-4 4-4-4M5 20h14"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"/>
        </svg>
        Download
                        </button>
                    </form>
                </div>
                {% endfor %}
            </div>
        </div>
        {% elif error %}
        <div class="error-card">
            <div class="error-icon">
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none">
                    <path d="M12 8v5m0 4h.01M10.3 3.8 2.9 17a2 2 0 0 0 1.75 3h14.7a2 2 0 0 0 1.75-3l-7.4-13.2a2 2 0 0 0-3.5 0Z"
                          stroke="currentColor" stroke-width="1.8"
                          stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </div>
            <div>
                <strong>Something went wrong</strong>
                <div>{{ error }}</div>
            </div>
        </div>
        {% endif %}

    </section>
    {% endif %}

    <!-- Features -->
    <section id="features" class="features-section">
        <div class="section-heading">
            <h2>Everything you need to download easily</h2>
            <p>Simple, fast, and reliable — no account needed.</p>
        </div>

        <div class="features">
            <article class="feature">
                <div class="feature-icon">
                    <svg width="29" height="29" viewBox="0 0 24 24" fill="none">
                        <path d="m13 2-9 12h7l-1 8 9-12h-7l1-8Z"
                              stroke="currentColor" stroke-width="1.8"
                              stroke-linejoin="round"/>
                    </svg>
                </div>
                <h3>Fast &amp; Free</h3>
                <p>Get direct media links quickly with a clean and simple workflow.</p>
            </article>

            <article class="feature">
                <div class="feature-icon">
                    <svg width="29" height="29" viewBox="0 0 24 24" fill="none">
                        <rect x="3" y="5" width="18" height="14" rx="2"
                              stroke="currentColor" stroke-width="1.8"/>
                        <path d="M7 15h10M7 11h4"
                              stroke="currentColor" stroke-width="1.8"
                              stroke-linecap="round"/>
                    </svg>
                </div>
                <h3>HD to 4K Quality</h3>
                <p>Choose from the available video qualities returned by the source.</p>
            </article>

            <article class="feature">
                <div class="feature-icon">
                    <svg width="29" height="29" viewBox="0 0 24 24" fill="none">
                        <path d="M12 3 20 6v5c0 5.1-3.4 8.7-8 10-4.6-1.3-8-4.9-8-10V6l8-3Z"
                              stroke="currentColor" stroke-width="1.8"
                              stroke-linejoin="round"/>
                        <path d="m9 12 2 2 4-4"
                              stroke="currentColor" stroke-width="1.8"
                              stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                </div>
                <h3>Safe &amp; Private</h3>
                <p>The existing server workflow does not save downloaded video files locally.</p>
            </article>
        </div>

        <div class="stats">
            <div class="stat">
                <div class="stat-icon">⚡</div>
                <div>
                    <strong>Fast Processing</strong>
                    <span>Direct media links</span>
                </div>
            </div>

            <div class="stat">
                <div class="stat-icon">◈</div>
                <div>
                    <strong>No Registration</strong>
                    <span>Start immediately</span>
                </div>
            </div>

            <div class="stat">
                <div class="stat-icon">▣</div>
                <div>
                    <strong>Works on Mobile</strong>
                    <span>Responsive design</span>
                </div>
            </div>
        </div>
    </section>

    <!-- Footer -->
    <footer>
        <div class="footer-grid">
            <div class="footer-brand">
                <a href="/" class="brand">
                    <span class="brand-icon">
                        <svg viewBox="0 0 24 24" fill="none">
                            <path d="M12 3v10m0 0 4-4m-4 4-4-4M5 17v2h14v-2"
                                  stroke="currentColor" stroke-width="2"
                                  stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                    </span>
                    Clipgrab
                </a>
                <p>A simple, modern interface for getting direct media links from supported platforms.</p>
            </div>

            <div class="footer">
                <h4>Product</h4>
                <a href="#features">Features</a>
                <a href="#download">How It Works</a>
                <a href="#features">Quality</a>
            </div>

            <div class="footer" id="faq">
                <h4>Support</h4>
                <a href="#faq">FAQ</a>
                <a href="#download">Contact</a>
                <a href="#download">Report an Issue</a>
            </div>

            <div class="footer">
                <h4>Legal</h4>
                <a href="#download">Privacy</a>
                <a href="#download">Terms</a>
                <a href="#download">Cookies</a>
            </div>
        </div>

        <div id="how-it-works" class="copyright">
            Use the service only for content you have permission to download. © 2026 Clipgrab.
        </div>
    </footer>

</div>

<script>
    function startLoading(form) {
        const button = document.getElementById("download-button");
        const text = button.querySelector(".button-text");
        const icon = button.querySelector(".button-icon");

        button.classList.add("loading");
        icon.innerHTML = '<span class="spinner"></span>';
        text.textContent = "Finding video...";
    }

    function startSelectedLoading(form) {
        const button = form.querySelector("button[type='submit']");
        if (!button) return true;

        button.disabled = true;
        button.textContent = "Downloading...";
        return true;
    }

    // Keep the result visible after the server response.
    window.addEventListener("load", function () {
        const results = document.getElementById("results");
        if (results) {
            setTimeout(() => {
                results.scrollIntoView({ behavior: "smooth", block: "center" });
            }, 80);
        }
    });
</script>
</body>
</html>
"""
REGISTER_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#030712">

    <title>Create Account — Clipgrab</title>

    <style>
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;

            font-family: Inter, Arial, sans-serif;
            color: #f8fafc;

            background:
                radial-gradient(
                    circle at 15% 15%,
                    rgba(34, 211, 238, .14),
                    transparent 30%
                ),
                radial-gradient(
                    circle at 85% 15%,
                    rgba(168, 85, 247, .14),
                    transparent 30%
                ),
                #030712;
        }

        .register-card {
            width: 100%;
            max-width: 440px;
            padding: 34px;

            border: 1px solid rgba(148, 163, 184, .16);
            border-radius: 24px;

            background: rgba(8, 18, 34, .78);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);

            box-shadow:
                0 25px 80px rgba(0,0,0,.45),
                0 0 50px rgba(34,211,238,.04);
        }

        .logo {
            width: 52px;
            height: 52px;
            margin: 0 auto 18px;

            display: grid;
            place-items: center;

            border-radius: 15px;
            border: 1px solid rgba(34,211,238,.4);

            color: #22d3ee;
            background: rgba(34,211,238,.08);
        }

        .logo svg {
            width: 27px;
            height: 27px;
        }

        h1 {
            text-align: center;
            font-size: 28px;
            letter-spacing: -1px;
            margin-bottom: 8px;
        }

        .subtitle {
            text-align: center;
            color: #94a3b8;
            font-size: 13px;
            line-height: 1.6;
            margin-bottom: 28px;
        }

        .field {
            margin-bottom: 17px;
        }

        label {
            display: block;
            margin-bottom: 7px;
            color: #cbd5e1;
            font-size: 12px;
            font-weight: 600;
        }

        input {
            width: 100%;
            padding: 14px 15px;

            border: 1px solid rgba(148,163,184,.16);
            border-radius: 12px;
            outline: none;

            color: white;
            background: rgba(3,10,22,.72);

            font-size: 14px;

            transition:
                border-color .2s ease,
                box-shadow .2s ease;
        }

        input::placeholder {
            color: #475569;
        }

        input:focus {
            border-color: rgba(34,211,238,.65);

            box-shadow:
                0 0 0 4px rgba(34,211,238,.06);
        }

        .password-wrap {
            position: relative;
        }

        .password-wrap input {
            padding-right: 65px;
        }

        .show-password {
            position: absolute;
            right: 10px;
            top: 50%;
            transform: translateY(-50%);

            border: 0;
            background: transparent;

            color: #64748b;
            font-size: 11px;
            font-weight: 700;

            cursor: pointer;
        }

        .show-password:hover {
            color: #cbd5e1;
        }

        .submit {
            width: 100%;
            margin-top: 5px;
            padding: 14px;

            border: 0;
            border-radius: 12px;

            color: white;
            font-size: 14px;
            font-weight: 800;

            cursor: pointer;

            background:
                linear-gradient(
                    100deg,
                    #06b6d4,
                    #3b82f6 55%,
                    #9333ea
                );

            box-shadow:
                0 12px 30px rgba(59,130,246,.23);

            transition:
                transform .2s ease,
                filter .2s ease;
        }

        .submit:hover {
            transform: translateY(-2px);
            filter: brightness(1.08);
        }

        .message {
            margin-bottom: 18px;
            padding: 12px 13px;

            border-radius: 11px;

            font-size: 12px;
            line-height: 1.5;
        }

        .error {
            color: #fecdd3;
            border: 1px solid rgba(251,59,90,.25);
            background: rgba(251,59,90,.08);
        }

        .success {
            color: #bbf7d0;
            border: 1px solid rgba(34,197,94,.25);
            background: rgba(34,197,94,.08);
        }

        .login {
            margin-top: 23px;
            text-align: center;

            color: #64748b;
            font-size: 12px;
        }

        .login a {
            color: #67e8f9;
            font-weight: 700;
            text-decoration: none;
        }

        .login a:hover {
            text-decoration: underline;
        }

        .back {
            display: block;
            margin-top: 18px;

            text-align: center;
            color: #475569;

            font-size: 11px;
            text-decoration: none;
        }

        .back:hover {
            color: #94a3b8;
        }

        @media (max-width: 480px) {
            .register-card {
                padding: 27px 20px;
                border-radius: 20px;
            }

            h1 {
                font-size: 25px;
            }
        }
    </style>
</head>

<body>

<div class="register-card">

    <div class="logo">
        <svg viewBox="0 0 24 24" fill="none">
            <path
                d="M12 3v10m0 0 4-4m-4 4-4-4M5 17v2h14v-2"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
            />
        </svg>
    </div>

    <h1>Create your account</h1>

    <p class="subtitle">
        Create an account to save and manage your downloaded videos.
    </p>

    {% if error %}
        <div class="message error">
            {{ error }}
        </div>
    {% endif %}

    {% if success %}
        <div class="message success">
            {{ success }}
        </div>
    {% endif %}

    <form method="POST" action="/register">

        <div class="field">
            <label for="name">Name</label>

            <input
                id="name"
                type="text"
                name="name"
                placeholder="Enter your name"
                autocomplete="name"
                required
            >
        </div>

        <div class="field">
            <label for="email">Email</label>

            <input
                id="email"
                type="email"
                name="email"
                placeholder="you@example.com"
                autocomplete="email"
                required
            >
        </div>

        <div class="field">
            <label for="password">Password</label>

            <div class="password-wrap">
                <input
                    id="password"
                    type="password"
                    name="password"
                    placeholder="Minimum 6 characters"
                    autocomplete="new-password"
                    minlength="6"
                    required
                >

                <button
                    type="button"
                    class="show-password"
                    onclick="togglePassword('password', this)"
                >
                    SHOW
                </button>
            </div>
        </div>

        <div class="field">
            <label for="confirm_password">Confirm Password</label>

            <div class="password-wrap">
                <input
                    id="confirm_password"
                    type="password"
                    name="confirm_password"
                    placeholder="Enter password again"
                    autocomplete="new-password"
                    minlength="6"
                    required
                >

                <button
                    type="button"
                    class="show-password"
                    onclick="togglePassword('confirm_password', this)"
                >
                    SHOW
                </button>
            </div>
        </div>

        <button class="submit" type="submit">
            Create Account
        </button>

    </form>

    <div class="login">
        Already have an account?
        <a href="/login">Log in</a>
    </div>

    <a class="back" href="/">
        ← Back to Clipgrab
    </a>

</div>

<script>
function togglePassword(id, button) {
    const input = document.getElementById(id);

    if (input.type === "password") {
        input.type = "text";
        button.textContent = "HIDE";
    } else {
        input.type = "password";
        button.textContent = "SHOW";
    }
}
</script>

</body>
</html>
"""
REGISTER_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#030712">

    <title>Create Account — Clipgrab</title>

    <style>
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;

            font-family: Inter, Arial, sans-serif;
            color: #f8fafc;

            background:
                radial-gradient(
                    circle at 15% 15%,
                    rgba(34, 211, 238, .14),
                    transparent 30%
                ),
                radial-gradient(
                    circle at 85% 15%,
                    rgba(168, 85, 247, .14),
                    transparent 30%
                ),
                #030712;
        }

        .register-card {
            width: 100%;
            max-width: 440px;
            padding: 34px;

            border: 1px solid rgba(148, 163, 184, .16);
            border-radius: 24px;

            background: rgba(8, 18, 34, .78);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);

            box-shadow:
                0 25px 80px rgba(0,0,0,.45),
                0 0 50px rgba(34,211,238,.04);
        }

        .logo {
            width: 52px;
            height: 52px;
            margin: 0 auto 18px;

            display: grid;
            place-items: center;

            border-radius: 15px;
            border: 1px solid rgba(34,211,238,.4);

            color: #22d3ee;
            background: rgba(34,211,238,.08);
        }

        .logo svg {
            width: 27px;
            height: 27px;
        }

        h1 {
            text-align: center;
            font-size: 28px;
            letter-spacing: -1px;
            margin-bottom: 8px;
        }

        .subtitle {
            text-align: center;
            color: #94a3b8;
            font-size: 13px;
            line-height: 1.6;
            margin-bottom: 28px;
        }

        .field {
            margin-bottom: 17px;
        }

        label {
            display: block;
            margin-bottom: 7px;
            color: #cbd5e1;
            font-size: 12px;
            font-weight: 600;
        }

        input {
            width: 100%;
            padding: 14px 15px;

            border: 1px solid rgba(148,163,184,.16);
            border-radius: 12px;
            outline: none;

            color: white;
            background: rgba(3,10,22,.72);

            font-size: 14px;

            transition:
                border-color .2s ease,
                box-shadow .2s ease;
        }

        input::placeholder {
            color: #475569;
        }

        input:focus {
            border-color: rgba(34,211,238,.65);

            box-shadow:
                0 0 0 4px rgba(34,211,238,.06);
        }

        .password-wrap {
            position: relative;
        }

        .password-wrap input {
            padding-right: 65px;
        }

        .show-password {
            position: absolute;
            right: 10px;
            top: 50%;
            transform: translateY(-50%);

            border: 0;
            background: transparent;

            color: #64748b;
            font-size: 11px;
            font-weight: 700;

            cursor: pointer;
        }

        .show-password:hover {
            color: #cbd5e1;
        }

        .submit {
            width: 100%;
            margin-top: 5px;
            padding: 14px;

            border: 0;
            border-radius: 12px;

            color: white;
            font-size: 14px;
            font-weight: 800;

            cursor: pointer;

            background:
                linear-gradient(
                    100deg,
                    #06b6d4,
                    #3b82f6 55%,
                    #9333ea
                );

            box-shadow:
                0 12px 30px rgba(59,130,246,.23);

            transition:
                transform .2s ease,
                filter .2s ease;
        }

        .submit:hover {
            transform: translateY(-2px);
            filter: brightness(1.08);
        }

        .message {
            margin-bottom: 18px;
            padding: 12px 13px;

            border-radius: 11px;

            font-size: 12px;
            line-height: 1.5;
        }

        .error {
            color: #fecdd3;
            border: 1px solid rgba(251,59,90,.25);
            background: rgba(251,59,90,.08);
        }

        .success {
            color: #bbf7d0;
            border: 1px solid rgba(34,197,94,.25);
            background: rgba(34,197,94,.08);
        }

        .login {
            margin-top: 23px;
            text-align: center;

            color: #64748b;
            font-size: 12px;
        }

        .login a {
            color: #67e8f9;
            font-weight: 700;
            text-decoration: none;
        }

        .login a:hover {
            text-decoration: underline;
        }

        .back {
            display: block;
            margin-top: 18px;

            text-align: center;
            color: #475569;

            font-size: 11px;
            text-decoration: none;
        }

        .back:hover {
            color: #94a3b8;
        }

        @media (max-width: 480px) {
            .register-card {
                padding: 27px 20px;
                border-radius: 20px;
            }

            h1 {
                font-size: 25px;
            }
        }
    </style>
</head>

<body>

<div class="register-card">

    <div class="logo">
        <svg viewBox="0 0 24 24" fill="none">
            <path
                d="M12 3v10m0 0 4-4m-4 4-4-4M5 17v2h14v-2"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
            />
        </svg>
    </div>

    <h1>Create your account</h1>

    <p class="subtitle">
        Create an account to save and manage your downloaded videos.
    </p>

    {% if error %}
        <div class="message error">
            {{ error }}
        </div>
    {% endif %}

    {% if success %}
        <div class="message success">
            {{ success }}
        </div>
    {% endif %}

    <form method="POST" action="/register">

        <div class="field">
            <label for="name">Name</label>

            <input
                id="name"
                type="text"
                name="name"
                placeholder="Enter your name"
                autocomplete="name"
                required
            >
        </div>

        <div class="field">
            <label for="email">Email</label>

            <input
                id="email"
                type="email"
                name="email"
                placeholder="you@example.com"
                autocomplete="email"
                required
            >
        </div>

        <div class="field">
            <label for="password">Password</label>

            <div class="password-wrap">
                <input
                    id="password"
                    type="password"
                    name="password"
                    placeholder="Minimum 6 characters"
                    autocomplete="new-password"
                    minlength="6"
                    required
                >

                <button
                    type="button"
                    class="show-password"
                    onclick="togglePassword('password', this)"
                >
                    SHOW
                </button>
            </div>
        </div>

        <div class="field">
            <label for="confirm_password">Confirm Password</label>

            <div class="password-wrap">
                <input
                    id="confirm_password"
                    type="password"
                    name="confirm_password"
                    placeholder="Enter password again"
                    autocomplete="new-password"
                    minlength="6"
                    required
                >

                <button
                    type="button"
                    class="show-password"
                    onclick="togglePassword('confirm_password', this)"
                >
                    SHOW
                </button>
            </div>
        </div>

        <button class="submit" type="submit">
            Create Account
        </button>

    </form>

    <div class="login">
        Already have an account?
        <a href="/login">Log in</a>
    </div>

    <a class="back" href="/">
        ← Back to Clipgrab
    </a>

</div>

<script>
function togglePassword(id, button) {
    const input = document.getElementById(id);

    if (input.type === "password") {
        input.type = "text";
        button.textContent = "HIDE";
    } else {
        input.type = "password";
        button.textContent = "SHOW";
    }
}
</script>

</body>
</html>
"""

LOGIN_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#030712">

    <title>Login — Clipgrab</title>

    <style>
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;

            font-family: Inter, Arial, sans-serif;
            color: #f8fafc;

            background:
                radial-gradient(
                    circle at 15% 15%,
                    rgba(34, 211, 238, .14),
                    transparent 30%
                ),
                radial-gradient(
                    circle at 85% 15%,
                    rgba(168, 85, 247, .14),
                    transparent 30%
                ),
                #030712;
        }

        .login-card {
            width: 100%;
            max-width: 420px;
            padding: 34px;

            border: 1px solid rgba(148, 163, 184, .16);
            border-radius: 24px;

            background: rgba(8, 18, 34, .78);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);

            box-shadow:
                0 25px 80px rgba(0,0,0,.45),
                0 0 50px rgba(34,211,238,.04);
        }

        .logo {
            width: 52px;
            height: 52px;
            margin: 0 auto 18px;

            display: grid;
            place-items: center;

            border-radius: 15px;
            border: 1px solid rgba(34,211,238,.4);

            color: #22d3ee;
            background: rgba(34,211,238,.08);
        }

        .logo svg {
            width: 27px;
            height: 27px;
        }

        h1 {
            text-align: center;
            font-size: 28px;
            letter-spacing: -1px;
            margin-bottom: 8px;
        }

        .subtitle {
            text-align: center;
            color: #94a3b8;
            font-size: 13px;
            line-height: 1.6;
            margin-bottom: 28px;
        }

        .field {
            margin-bottom: 18px;
        }

        label {
            display: block;
            margin-bottom: 7px;
            color: #cbd5e1;
            font-size: 12px;
            font-weight: 600;
        }

        input {
            width: 100%;
            padding: 14px 15px;

            border: 1px solid rgba(148,163,184,.16);
            border-radius: 12px;
            outline: none;

            color: white;
            background: rgba(3,10,22,.72);

            font-size: 14px;

            transition:
                border-color .2s ease,
                box-shadow .2s ease;
        }

        input::placeholder {
            color: #475569;
        }

        input:focus {
            border-color: rgba(34,211,238,.65);

            box-shadow:
                0 0 0 4px rgba(34,211,238,.06);
        }

        .password-wrap {
            position: relative;
        }

        .password-wrap input {
            padding-right: 65px;
        }

        .show-password {
            position: absolute;
            right: 10px;
            top: 50%;
            transform: translateY(-50%);

            border: 0;
            background: transparent;

            color: #64748b;
            font-size: 11px;
            font-weight: 700;

            cursor: pointer;
        }

        .show-password:hover {
            color: #cbd5e1;
        }

        .submit {
            width: 100%;
            margin-top: 5px;
            padding: 14px;

            border: 0;
            border-radius: 12px;

            color: white;
            font-size: 14px;
            font-weight: 800;

            cursor: pointer;

            background:
                linear-gradient(
                    100deg,
                    #06b6d4,
                    #3b82f6 55%,
                    #9333ea
                );

            box-shadow:
                0 12px 30px rgba(59,130,246,.23);

            transition:
                transform .2s ease,
                filter .2s ease;
        }

        .submit:hover {
            transform: translateY(-2px);
            filter: brightness(1.08);
        }

        .message {
            margin-bottom: 18px;
            padding: 12px 13px;

            border-radius: 11px;

            color: #fecdd3;
            border: 1px solid rgba(251,59,90,.25);
            background: rgba(251,59,90,.08);

            font-size: 12px;
            line-height: 1.5;
        }

        .register {
            margin-top: 23px;
            text-align: center;

            color: #64748b;
            font-size: 12px;
        }

        .register a {
            color: #67e8f9;
            font-weight: 700;
            text-decoration: none;
        }

        .register a:hover {
            text-decoration: underline;
        }

        .back {
            display: block;
            margin-top: 18px;

            text-align: center;
            color: #475569;

            font-size: 11px;
            text-decoration: none;
        }

        .back:hover {
            color: #94a3b8;
        }

        @media (max-width: 480px) {
            .login-card {
                padding: 27px 20px;
                border-radius: 20px;
            }

            h1 {
                font-size: 25px;
            }
        }
    </style>
</head>

<body>

<div class="login-card">

    <div class="logo">
        <svg viewBox="0 0 24 24" fill="none">
            <path
                d="M12 3v10m0 0 4-4m-4 4-4-4M5 17v2h14v-2"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
            />
        </svg>
    </div>

    <h1>Welcome back</h1>

    <p class="subtitle">
        Log in to access your saved videos and downloads.
    </p>

    {% if error %}
        <div class="message">
            {{ error }}
        </div>
    {% endif %}

    <form method="POST" action="/login">

        <div class="field">
            <label for="email">Email</label>

            <input
                id="email"
                type="email"
                name="email"
                placeholder="you@example.com"
                autocomplete="email"
                required
            >
        </div>

        <div class="field">
            <label for="password">Password</label>

            <div class="password-wrap">
                <input
                    id="password"
                    type="password"
                    name="password"
                    placeholder="Enter your password"
                    autocomplete="current-password"
                    required
                >

                <button
                    type="button"
                    class="show-password"
                    onclick="togglePassword()"
                >
                    SHOW
                </button>
            </div>
        </div>

        <button class="submit" type="submit">
            Log In
        </button>

    </form>

    <div class="register">
        Don't have an account?
        <a href="/register">Create account</a>
    </div>

    <a class="back" href="/">
        ← Back to Clipgrab
    </a>

</div>

<script>
function togglePassword() {
    const input = document.getElementById("password");
    const button = document.querySelector(".show-password");

    if (input.type === "password") {
        input.type = "text";
        button.textContent = "HIDE";
    } else {
        input.type = "password";
        button.textContent = "SHOW";
    }
}
</script>

</body>
</html>
"""


DASHBOARD_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#030712">

    <title>My Downloads — Clipgrab</title>

    <style>
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            min-height: 100vh;
            color: #f8fafc;
            font-family: Inter, Arial, sans-serif;

            background:
                radial-gradient(
                    circle at 10% 5%,
                    rgba(34, 211, 238, .12),
                    transparent 28%
                ),
                radial-gradient(
                    circle at 90% 5%,
                    rgba(168, 85, 247, .12),
                    transparent 28%
                ),
                #030712;
        }

        .container {
            width: min(1100px, calc(100% - 28px));
            margin: auto;
        }

        /* Navbar */

        .navbar {
            height: 76px;

            display: flex;
            align-items: center;
            justify-content: space-between;

            border-bottom: 1px solid rgba(148,163,184,.14);
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 10px;

            color: white;
            text-decoration: none;

            font-size: 22px;
            font-weight: 800;
        }

        .brand-icon {
            width: 39px;
            height: 39px;

            display: grid;
            place-items: center;

            border-radius: 11px;

            color: #22d3ee;
            border: 1px solid rgba(34,211,238,.35);
            background: rgba(34,211,238,.08);
        }

        .brand-icon svg {
            width: 21px;
        }

        .nav-right {
            display: flex;
            align-items: center;
            gap: 13px;
        }

        .user {
            padding: 9px 13px;

            border: 1px solid rgba(148,163,184,.14);
            border-radius: 10px;

            color: #cbd5e1;
            background: rgba(255,255,255,.025);

            font-size: 12px;
            font-weight: 600;
        }

        .logout {
            padding: 9px 13px;

            border: 1px solid rgba(251,59,90,.22);
            border-radius: 10px;

            color: #fda4af;
            background: rgba(251,59,90,.06);

            font-size: 12px;
            font-weight: 700;
            text-decoration: none;

            transition: .2s;
        }

        .logout:hover {
            background: rgba(251,59,90,.12);
        }

        /* Header */

        .page-header {
            padding: 55px 0 30px;
        }

        .page-header h1 {
            font-size: clamp(30px, 5vw, 43px);
            letter-spacing: -1.8px;
            margin-bottom: 9px;
        }

        .gradient {
            background:
                linear-gradient(
                    90deg,
                    #f8fafc,
                    #67e8f9,
                    #c084fc
                );

            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
        }

        .page-header p {
            color: #64748b;
            font-size: 14px;
        }

        /* Alert */

        .error {
            margin-bottom: 20px;
            padding: 13px 15px;

            border-radius: 12px;

            color: #fecdd3;
            border: 1px solid rgba(251,59,90,.25);
            background: rgba(251,59,90,.08);

            font-size: 13px;
        }

        /* Empty */

        .empty {
            padding: 65px 25px;

            text-align: center;

            border: 1px solid rgba(148,163,184,.13);
            border-radius: 20px;

            background: rgba(8,18,34,.62);
        }

        .empty-icon {
            width: 65px;
            height: 65px;

            margin: 0 auto 18px;

            display: grid;
            place-items: center;

            border-radius: 18px;

            color: #22d3ee;
            border: 1px solid rgba(34,211,238,.25);
            background: rgba(34,211,238,.07);
        }

        .empty h2 {
            font-size: 20px;
            margin-bottom: 8px;
        }

        .empty p {
            color: #64748b;
            font-size: 13px;
            margin-bottom: 23px;
        }

        .download-btn {
            display: inline-block;

            padding: 12px 18px;

            border-radius: 11px;

            color: white;
            background:
                linear-gradient(
                    100deg,
                    #06b6d4,
                    #3b82f6 55%,
                    #9333ea
                );

            font-size: 13px;
            font-weight: 800;

            text-decoration: none;
        }

        /* Downloads */

        .downloads {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 17px;

            padding-bottom: 60px;
        }

        .dashboard-controls {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }

        .dashboard-controls input,
        .dashboard-controls select,
        .dashboard-controls button {
            min-height: 42px;
            padding: 0 13px;
            border: 1px solid rgba(148,163,184,.18);
            border-radius: 10px;
            color: #f8fafc;
            background: rgba(8,18,34,.72);
            font: inherit;
            font-size: 12px;
        }

        .dashboard-controls input {
            flex: 1;
            min-width: 0;
        }

        .dashboard-controls button {
            color: white;
            background: linear-gradient(100deg, #06b6d4, #2563eb);
            font-weight: 700;
            cursor: pointer;
        }

        .pagination {
            display: flex;
            justify-content: center;
            gap: 10px;
            padding-bottom: 45px;
        }

        .pagination a,
        .pagination span {
            padding: 9px 13px;
            border: 1px solid rgba(148,163,184,.16);
            border-radius: 9px;
            color: #cbd5e1;
            background: rgba(8,18,34,.62);
            font-size: 12px;
            text-decoration: none;
        }

        .pagination .current {
            color: white;
            border-color: rgba(34,211,238,.35);
            background: rgba(34,211,238,.12);
        }

        .card {
            overflow: hidden;

            border: 1px solid rgba(148,163,184,.14);
            border-radius: 18px;

            background:
                linear-gradient(
                    145deg,
                    rgba(12,28,50,.78),
                    rgba(5,12,25,.78)
                );

            transition:
                transform .2s ease,
                border-color .2s ease;
        }

        .card:hover {
            transform: translateY(-3px);
            border-color: rgba(34,211,238,.25);
        }

        .thumbnail {
            height: 175px;

            display: grid;
            place-items: center;

            background:
                linear-gradient(
                    135deg,
                    rgba(34,211,238,.08),
                    rgba(168,85,247,.08)
                );

            overflow: hidden;
        }

        .thumbnail img {
            width: 100%;
            height: 100%;

            object-fit: cover;
        }

        .placeholder {
            color: #475569;
            font-size: 42px;
        }

        .card-body {
            padding: 17px;
        }

        .title {
            color: #f8fafc;

            font-size: 14px;
            font-weight: 700;
            line-height: 1.5;

            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .meta {
            display: flex;
            flex-wrap: wrap;
            gap: 7px;

            margin-top: 11px;
        }

        .tag {
            padding: 5px 8px;

            border-radius: 7px;

            color: #94a3b8;
            background: rgba(255,255,255,.04);

            font-size: 10px;
            font-weight: 700;
        }

        .actions {
            display: flex;
            gap: 8px;

            margin-top: 16px;
        }

        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 5px 10px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: .2px;
        }

        .status-completed {
            color: #86efac;
            background: rgba(34,197,94,.10);
            border: 1px solid rgba(34,197,94,.18);
        }

        .status-processing {
            color: #fcd34d;
            background: rgba(245,158,11,.10);
            border: 1px solid rgba(245,158,11,.18);
        }

        .status-failed {
            color: #fb7185;
            background: rgba(239,68,68,.10);
            border: 1px solid rgba(239,68,68,.18);
        }

        .action {
            flex: 1;

            padding: 10px;

            border-radius: 9px;

            text-align: center;

            font-size: 11px;
            font-weight: 800;

            text-decoration: none;
        }

        .watch {
            color: white;
            background: rgba(59,130,246,.18);
            border: 1px solid rgba(59,130,246,.23);
        }

        .download {
            color: white;
            background: linear-gradient(100deg, #06b6d4, #2563eb);
        }

        .delete {
            flex: 0 0 42px;

            border: 1px solid rgba(251,59,90,.18);
            color: #fb7185;
            background: rgba(251,59,90,.06);

            cursor: pointer;
        }

        /* Mobile */

        @media (max-width: 700px) {
            .container {
                width: min(100% - 20px, 560px);
            }

            .navbar {
                height: 68px;
            }

            .user {
                display: none;
            }

            .downloads {
                grid-template-columns: 1fr;
            }

            .thumbnail {
                height: 190px;
            }

            .page-header {
                padding-top: 40px;
            }

            .dashboard-controls {
                flex-wrap: wrap;
            }

            .dashboard-controls input {
                flex-basis: 100%;
            }

            .dashboard-controls select,
            .dashboard-controls button {
                flex: 1;
            }
        }

        @media (max-width: 420px) {
            .brand {
                font-size: 19px;
            }

            .actions {
                flex-wrap: wrap;
            }

            .watch,
            .download {
                min-width: calc(50% - 4px);
            }

            .delete {
                flex: 1;
            }
        }
    </style>
</head>

<body>

<div class="container">

    <!-- Navbar -->

    <header class="navbar">

        <a href="/" class="brand">

            <span class="brand-icon">
                <svg viewBox="0 0 24 24" fill="none">
                    <path
                        d="M12 3v10m0 0 4-4m-4 4-4-4M5 17v2h14v-2"
                        stroke="currentColor"
                        stroke-width="2"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                    />
                </svg>
            </span>

            Clipgrab

        </a>

        <div class="nav-right">

            <div class="user">
                👤 {{ user_name }}
            </div>

            <a href="/logout" class="logout">
                Logout
            </a>

        </div>

    </header>


    <!-- Header -->

    <section class="page-header">

        <h1>
            My <span class="gradient">Downloads</span>
        </h1>

        <p>
            Your saved videos, all in one place.
        </p>

    </section>

    <form class="dashboard-controls" action="/dashboard" method="get">
        <input
            type="search"
            name="q"
            value="{{ search_query|default('') }}"
            placeholder="Search downloads..."
            aria-label="Search downloads"
        >
        <select name="status" aria-label="Filter by status">
            <option value="all" {% if status_filter|default('all') == 'all' %}selected{% endif %}>All statuses</option>
            <option value="completed" {% if status_filter|default('all') == 'completed' %}selected{% endif %}>Completed</option>
            <option value="processing" {% if status_filter|default('all') == 'processing' %}selected{% endif %}>Processing</option>
            <option value="failed" {% if status_filter|default('all') == 'failed' %}selected{% endif %}>Failed</option>
        </select>
        <button type="submit">Filter</button>
    </form>


    {% if error %}

        <div class="error">
            {{ error }}
        </div>

    {% endif %}


    {% if downloads %}

        <section class="downloads">

            {% for video in downloads %}

            <article class="card">

                <div class="thumbnail">

                    {% if video.thumbnail_url %}

                        <img
                            src="{{ video.thumbnail_url }}"
                            alt="{{ video.video_title }}"
                            loading="lazy"
                        >

                    {% else %}

                        <div class="placeholder">
                            ▶
                        </div>

                    {% endif %}

                </div>


                <div class="card-body">

                    <div class="title">
                        {{ video.video_title or "Untitled Video" }}
                    </div>


                    <div class="meta">

                        {% if video.quality %}
                            <span class="tag">
                                {{ video.quality }}
                            </span>
                        {% endif %}

                        {% if video.format %}
                            <span class="tag">
                                {{ video.format|upper }}
                            </span>
                        {% endif %}

                        {% if video.status == "completed" %}
                            <span class="status-badge status-completed">
                                ● Completed
                            </span>
                        {% elif video.status == "processing" %}
                            <span class="status-badge status-processing">
                                ● Processing
                            </span>
                        {% else %}
                            <span class="status-badge status-failed">
                                ● Failed
                            </span>
                        {% endif %}

                    </div>


                    {% if video.status == "completed" and video.storage_path %}

                    <div class="actions">

                        <a
                            href="/video/{{ video.id }}"
                            class="action watch"
                        >
                            ▶ Watch
                        </a>

                        <a
                            href="/video/{{ video.id }}/download"
                            class="action download"
                        >
                            ↓ Download
                        </a>

                        <a
                            href="/delete/{{ video.id }}"
                            class="action delete"
                            onclick="return confirm('Delete this video?')"
                        >
                            🗑
                        </a>

                    </div>

                    {% elif video.status == "processing" %}

                        <div
                            class="actions"
                            data-download-id="{{ video.id }}"
                        >

                            <div class="action watch">
                                ⏳ Processing...
                            </div>

                        </div>

                    {% else %}

                        <div class="actions">

                            <a
                                href="/retry/{{ video.id }}"
                                class="action watch"
                            >
                                🔄 Retry
                            </a>

                            <a
                                href="/delete/{{ video.id }}"
                                class="action delete"
                                onclick="return confirm('Delete this failed download?')"
                            >
                                🗑 Delete
                            </a>

                        </div>

                    {% endif %}

                </div>

            </article>

            {% endfor %}

        </section>

        {% if total_pages|default(1) > 1 %}
            <nav class="pagination" aria-label="Download pages">
                {% if page > 1 %}
                    <a href="/dashboard?q={{ search_query|urlencode }}&status={{ status_filter|urlencode }}&page={{ page - 1 }}">Previous</a>
                {% endif %}
                <span class="current">Page {{ page }} of {{ total_pages }}</span>
                {% if page < total_pages %}
                    <a href="/dashboard?q={{ search_query|urlencode }}&status={{ status_filter|urlencode }}&page={{ page + 1 }}">Next</a>
                {% endif %}
            </nav>
        {% endif %}

    {% else %}

        <section class="empty">

            <div class="empty-icon">
                <svg width="30" height="30" viewBox="0 0 24 24" fill="none">
                    <path
                        d="M12 3v10m0 0 4-4m-4 4-4-4M5 17v2h14v-2"
                        stroke="currentColor"
                        stroke-width="1.8"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                    />
                </svg>
            </div>

            <h2>No downloads yet</h2>

            <p>
                Videos you save will appear here.
            </p>

            <a href="/" class="download-btn">
                Download a Video
            </a>

        </section>

    {% endif %}

</div>

<script>
(function () {
    const processingCards = document.querySelectorAll(
        '[data-download-id]'
    );

    if (!processingCards.length) {
        return;
    }

    async function checkStatus(card) {
        const downloadId = card.getAttribute("data-download-id");

        if (!downloadId) {
            return;
        }

        try {
            const response = await fetch(
                `/api/download-status/${encodeURIComponent(downloadId)}`,
                { cache: "no-store" }
            );

            if (!response.ok) {
                return;
            }

            const data = await response.json();

            if (data.status === "completed" || data.status === "failed") {
                window.location.reload();
            }
        } catch (error) {
            console.log("Status check failed:", error);
        }
    }

    function checkAll() {
        processingCards.forEach(checkStatus);
    }

    setInterval(checkAll, 5000);
})();
</script>

</body>
</html>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            return render_template_string(
                LOGIN_PAGE,
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
                return render_template_string(
                    LOGIN_PAGE,
                    error="Invalid email or password."
                )

            user = result.data[0]

            # Verify hashed password
            if not check_password_hash(
                user["password_hash"],
                password
            ):
                return render_template_string(
                    LOGIN_PAGE,
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
            return render_template_string(
                LOGIN_PAGE,
                error=f"Login failed: {str(e)}"
            )

    return render_template_string(LOGIN_PAGE)

  
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

    try:
        # Get only this user's downloads
        result = (
            supabase
            .table("downloads")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )

        downloads = result.data or []
        search_query = request.args.get("q", "").strip().lower()
        status_filter = request.args.get("status", "all").strip().lower()

        if search_query:
            downloads = [
                video for video in downloads
                if search_query in (video.get("video_title") or "").lower()
            ]

        if status_filter in {"processing", "completed", "failed"}:
            downloads = [
                video for video in downloads
                if video.get("status") == status_filter
            ]

        page_size = 8
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            page = 1

        total_downloads = len(downloads)
        total_pages = max(1, (total_downloads + page_size - 1) // page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        downloads = downloads[start:start + page_size]

        return render_template_string(
            DASHBOARD_PAGE,
            user_name=session.get("user_name", "User"),
            downloads=downloads,
            search_query=search_query,
            status_filter=status_filter,
            page=page,
            total_pages=total_pages,
            total_downloads=total_downloads
        )

    except Exception as e:
        return render_template_string(
            DASHBOARD_PAGE,
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
        return render_template_string(
            DASHBOARD_PAGE,
            user_name=session.get("user_name", "User"),
            downloads=[],
            error=f"Unable to delete video: {str(e)}"
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

        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head><meta charset="UTF-8"><title>Retrying download</title></head>
        <body>
            <p>Retrying download...</p>
            <form id="retry-form" action="/download-selected" method="POST">
                <input type="hidden" name="video_url" value="{original_url}">
                <input type="hidden" name="format_id" value="{format_id}">
                <input type="hidden" name="retry_id" value="{download_id}">
            </form>
            <script>document.getElementById("retry-form").submit();</script>
        </body>
        </html>
        """

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

    temp_dir = None
    temp_file = None
    download_id = None

    try:
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
            return render_template_string(
                HTML_PAGE,
                error="Selected quality is no longer available."
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
                "error_message": None
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
                    "status": "processing"
                })
                .execute()
            )

            if not download_record.data:
                return render_template_string(
                    HTML_PAGE,
                    error="Could not create download record."
                )

            download_id = download_record.data[0]["id"]

        temp_dir = tempfile.mkdtemp()

        safe_name = re.sub(
            r"[^a-zA-Z0-9_-]+",
            "_",
            video_title
        ).strip("_")

        if not safe_name:
            safe_name = "video"

        output_template = os.path.join(
            temp_dir,
            f"{safe_name}.%(ext)s"
        )

        has_audio = selected_format.get("acodec") != "none"
        format_selector = format_id if has_audio else f"{format_id}+bestaudio/best"

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
            info = ydl.extract_info(
                video_url,
                download=True
            )

            downloaded_file = ydl.prepare_filename(info)

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

        if not temp_file:
            for filename in os.listdir(temp_dir):
                path = os.path.join(temp_dir, filename)
                if os.path.isfile(path):
                    temp_file = path
                    break

        if not temp_file or not os.path.exists(temp_file):
            raise Exception("Downloaded video file was not found.")

        extension = os.path.splitext(temp_file)[1].lower() or ".mp4"
        file_name = f"{uuid.uuid4().hex}{extension}"

        storage_path = (
            f"{user_id}/"
            f"{download_id}/"
            f"{file_name}"
        )

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
                    "content-type": (
                        content_type
                    ),
                    "upsert": "true",
                }
            )

        supabase.table("downloads").update({
            "storage_path": storage_path,
            "status": "completed"
        }).eq(
            "id",
            download_id
        ).eq(
            "user_id",
            user_id
        ).execute()

        return redirect("/dashboard")

    except Exception as e:

        if download_id:
            try:
                supabase.table("downloads").update({
                    "status": "failed"
                }).eq("id", download_id).eq(
                    "user_id", user_id
                ).execute()
            except Exception:
                pass

        return render_template_string(
            HTML_PAGE,
            error=f"Download failed: {str(e)}"
        )

    finally:

        # --------------------------------------------------
        # 9. Delete temporary file
        # --------------------------------------------------
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass

        # Delete temporary directory
        if temp_dir and os.path.exists(temp_dir):
            try:
                os.rmdir(temp_dir)
            except Exception:
                pass


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        # Basic validation
        if not name or not email or not password:
            return render_template_string(
                REGISTER_PAGE,
                error="All fields are required."
            )

        if password != confirm_password:
            return render_template_string(
                REGISTER_PAGE,
                error="Passwords do not match."
            )

        if len(password) < 6:
            return render_template_string(
                REGISTER_PAGE,
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
                return render_template_string(
                    REGISTER_PAGE,
                    error="An account with this email already exists."
                )

            # Hash password before storing it
            password_hash = generate_password_hash(password)

            # Create user
            result = (
                supabase
                .table("users")
                .insert({
                    "name": name,
                    "email": email,
                    "password_hash": password_hash
                })
                .execute()
            )

            if not result.data:
                return render_template_string(
                    REGISTER_PAGE,
                    error="Unable to create account."
                )

            return render_template_string(
                REGISTER_PAGE,
                success="Account created successfully! You can now log in."
            )

        except Exception as e:
            return render_template_string(
                REGISTER_PAGE,
                error=f"Registration failed: {str(e)}"
            )

    return render_template_string(REGISTER_PAGE)

  
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
            return "Could not create video URL.", 500

        from markupsafe import escape

        title = video.get("video_title", "Video")
        safe_title = escape(title)
        extension = os.path.splitext(storage_path)[1].lower()
        content_type = (
            "video/webm" if extension == ".webm"
            else "video/mp4" if extension == ".mp4"
            else "video/mp4"
        )

        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <meta name="theme-color" content="#030712">
            <title>{safe_title} — Clipgrab</title>
            <style>
                * {{ box-sizing: border-box; }}

                body {{
                    margin: 0;
                    min-height: 100vh;
                    padding: 18px;
                    background: #030712;
                    color: white;
                    font-family: Inter, Arial, sans-serif;
                    background:
                        radial-gradient(circle at 15% 10%, rgba(34,211,238,.12), transparent 30%),
                        radial-gradient(circle at 85% 10%, rgba(168,85,247,.12), transparent 30%),
                        #030712;
                }}

                .watch-page {{
                    width: min(1100px, 100%);
                    margin: 0 auto;
                }}

                .topbar {{
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    margin-bottom: 18px;
                }}

                .brand {{
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    color: white;
                    text-decoration: none;
                    font-size: 20px;
                    font-weight: 800;
                }}

                .brand-icon {{
                    width: 38px;
                    height: 38px;
                    display: grid;
                    place-items: center;
                    border: 1px solid rgba(34,211,238,.4);
                    border-radius: 11px;
                    color: #22d3ee;
                    background: rgba(34,211,238,.08);
                }}

                .back,
                .action {{
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    gap: 8px;
                    border-radius: 11px;
                    text-decoration: none;
                    font-weight: 700;
                }}

                .back {{
                    padding: 10px 14px;
                    border: 1px solid rgba(148,163,184,.16);
                    color: #cbd5e1;
                    background: rgba(255,255,255,.035);
                    font-size: 13px;
                }}

                .player-card {{
                    overflow: hidden;
                    border: 1px solid rgba(148,163,184,.16);
                    border-radius: 20px;
                    background: rgba(8,18,34,.78);
                    box-shadow: 0 25px 80px rgba(0,0,0,.42);
                }}

                .video-wrap {{
                    width: 100%;
                    aspect-ratio: 16 / 9;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    background: #000;
                }}

                video {{
                    display: block;
                    width: 100%;
                    height: 100%;
                    object-fit: contain;
                    background: black;
                }}

                .video-info {{ padding: 20px 22px; }}

                .status {{
                    display: inline-flex;
                    align-items: center;
                    gap: 7px;
                    margin-bottom: 10px;
                    padding: 6px 10px;
                    border: 1px solid rgba(34,197,94,.18);
                    border-radius: 999px;
                    color: #86efac;
                    background: rgba(34,197,94,.08);
                    font-size: 11px;
                    font-weight: 700;
                }}

                .status-dot {{
                    width: 6px;
                    height: 6px;
                    border-radius: 50%;
                    background: #22c55e;
                    box-shadow: 0 0 10px rgba(34,197,94,.7);
                }}

                h1 {{
                    margin: 0;
                    color: white;
                    font-size: clamp(18px, 3vw, 25px);
                    line-height: 1.35;
                    overflow-wrap: anywhere;
                }}

                .subtitle {{
                    margin-top: 7px;
                    color: #64748b;
                    font-size: 12px;
                }}

                .actions {{
                    display: flex;
                    flex-wrap: wrap;
                    gap: 10px;
                    margin-top: 18px;
                }}

                .action {{
                    padding: 11px 16px;
                    font-size: 13px;
                }}

                .download {{
                    color: white;
                    background: linear-gradient(100deg, #06b6d4, #2563eb);
                    box-shadow: 0 9px 25px rgba(37,99,235,.22);
                }}

                .dashboard {{
                    border: 1px solid rgba(148,163,184,.16);
                    color: #cbd5e1;
                    background: rgba(255,255,255,.035);
                }}

                @media (max-width: 600px) {{
                    body {{ padding: 8px; }}
                    .brand {{ font-size: 18px; }}
                    .brand-icon {{ width: 35px; height: 35px; }}
                    .back {{ padding: 9px 11px; font-size: 12px; }}
                    .player-card {{ border-radius: 16px; }}
                    .video-wrap {{ aspect-ratio: 16 / 10; }}
                    .video-info {{ padding: 17px; }}
                    .actions {{ flex-direction: column; }}
                    .action {{ width: 100%; }}
                }}
            </style>
        </head>
        <body>
            <main class="watch-page">
                <header class="topbar">
                    <a href="/" class="brand">
                        <span class="brand-icon">↓</span>
                        Clipgrab
                    </a>
                    <a href="/dashboard" class="back">← Dashboard</a>
                </header>

                <section class="player-card">
                    <div class="video-wrap">
                        <video controls playsinline preload="metadata">
                            <source src="{video_url}">
                            Your browser does not support video playback.
                        </video>
                    </div>

                    <div class="video-info">
                        <div class="status">
                            <span class="status-dot"></span>
                            Saved video
                        </div>
                        <h1>{safe_title}</h1>
                        <div class="subtitle">Your video is saved in your Clipgrab library.</div>
                        <div class="actions">
                            <a href="/video/{download_id}/download" class="action download">↓ Download</a>
                            <a href="/dashboard" class="action dashboard">← Back to Dashboard</a>
                        </div>
                    </div>
                </section>
            </main>
        </body>
        </html>
        """

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
    return render_template_string(HTML_PAGE)


@app.route("/download", methods=["POST"])
def download():
    if "user_id" not in session:
        return redirect("/login")

    url = request.form.get("url", "").strip()
    if not url:
        return render_template_string(
            HTML_PAGE,
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
            return render_template_string(
                HTML_PAGE,
                error="No downloadable video formats were found."
            )

        return render_template_string(
            HTML_PAGE,
            formats=unique_formats,
            video_title=info.get("title", "Video"),
            video_url=url
        )

    except Exception as e:
        return render_template_string(
            HTML_PAGE,
            error=f"Unable to fetch video: {str(e)}"
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
