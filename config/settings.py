from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Lokal ishlab chiqishda BASE_DIR/.env dan o'qiladi (Render'da env dashboard'dan keladi)
load_dotenv(BASE_DIR / ".env")


# SECURITY
# Read DEBUG from environment for deploy flexibility
DEBUG = os.environ.get("DEBUG", "False").lower() in ("1", "true", "yes")

SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        # Faqat local development uchun — production'da bu ishlamaydi
        SECRET_KEY = "django-insecure-local-dev-only-change-in-render-env"
    else:
        raise RuntimeError(
            "SECRET_KEY environment o'zgaruvchisi o'rnatilmagan! "
            "Render dashboard > Environment bo'limida SECRET_KEY qo'shing. "
            "Ishlab chiqarishda standart kalit bilan ishga tushirish taqiqlanadi."
        )

# Allow hosts configurable via env var; keep render domain by default
ALLOWED_HOSTS = os.environ.get(
    "ALLOWED_HOSTS",
    "127.0.0.1,localhost,.onrender.com",
).split(",")


# APPLICATIONS
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "register_withvue",
]


# MIDDLEWARE
MIDDLEWARE = [
    # JSON javoblarni siqish — katta ro'yxatlar (~10x kichikroq) tezroq yuklanadi
    "django.middleware.gzip.GZipMiddleware",
    "config.middleware.JsonExceptionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # API gate — /api/ ostidagi HAR BIR endpoint JWT talab qiladi
    # (ommaviy ro'yxatdagi: login, register, webhook'lar). Deny-by-default:
    # yangi qo'shilgan endpoint tasodifan ochiq qolmaydi.
    "config.middleware.ApiAuthGateMiddleware",
    # WhiteNoise static uchun
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


ROOT_URLCONF = "config.urls"


# TEMPLATES
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


WSGI_APPLICATION = "config.wsgi.application"


# DATABASE
# Fallback to a local sqlite DB when DATABASE_URL is not provided
sqlite_path = str(BASE_DIR / "db.sqlite3").replace("\\", "/")
DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get("DATABASE_URL"),
        conn_max_age=600,
    )
}


DATABASE_URL = os.environ.get("DATABASE_URL", "")

# If DATABASE_URL is set use dj_database_url to parse it, otherwise fall
# back to a local sqlite database as the comment above intended.
if isinstance(DATABASE_URL, bytes):
    DATABASE_URL = DATABASE_URL.decode()

if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=600)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": sqlite_path,
        }
    }
# When behind a proxy (Render), honor X-Forwarded-Proto for secure URLs
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ─────────────────────────────
# XAVFSIZLIK HEADER'LARI — HAR DOIM (DEBUG=True'da ham)
# Bular xavfsizlik mudofaasi, faqat "chiroylik" uchun emas: brauzer
# darajasida XSS va boshqa hujumlar ortini qoplaydi.DEBUG'ga bog'liq
# emas — lokal HTTP'da ham ishlaydi, shuning uchun shartsiz o'rnatiladi.
# ─────────────────────────────
SECURE_CONTENT_TYPE_NOSNIFF = True  # brauzer fayl turini "taxmin qilishi"ni to'xtatadi
X_FRAME_OPTIONS = "DENY"  # saytni boshqa saytga iframe orqali joylashtirishni bloklaydi
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"  # tashqi saytga to'liq URL oqmaydi

# ─────────────────────────────
# PRODUCTION XAVFSIZLIK HEADER'LARI
# DEBUG=False bo'lganda (Render'da) ishga tushadi. Local development
# (DEBUG=True, HTTP orqali) buzilmasligi uchun shart qo'yilgan.
# ─────────────────────────────
if not DEBUG:
    SECURE_SSL_REDIRECT = True  # HTTP so'rovlarni avtomatik HTTPS'ga yo'naltiradi
    SESSION_COOKIE_SECURE = True  # cookie faqat HTTPS orqali yuboriladi
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000  # brauzerga 1 yil "faqat HTTPS" deb aytadi
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# PASSWORD VALIDATION
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# LANGUAGE
LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True
USE_TZ = True


# STATIC FILES
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"


# DEFAULT PRIMARY KEY
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ─────────────────────────────
# JWT AUTENTIFIKATSIYA
#
# Loyihada Django'ning standart User modeli ishlatilmaydi (Manager,
# Teacher, Student o'z jadvallarida), shuning uchun SimpleJWT'ning
# "for_user()" emas, qo'lda claim qo'shiladigan usuli ishlatiladi —
# tokenga faqat 'phone' va 'role' yoziladi (register_withvue/jwt_auth.py).
#
# Token SECRET_KEY bilan imzolanadi — uni bilmagan hech kim o'zi uchun
# soxta token yasay olmaydi. Shu bilan avvalgi 'X-User-Phone'
# muammosi (istalgan qiymatni yuborish mumkin edi) butunlay yopiladi.
# ─────────────────────────────
from datetime import timedelta  # noqa: E402

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=12),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
}


# CACHE
# Login urinishlarini sanash (rate limiting) uchun ishlatiladi
# (register_withvue/ratelimit.py). Render'dagi bitta worker uchun
# yetarli — ko'p worker/instance ishlatilsa Redis'ga o'tkazish kerak
# bo'ladi, chunki bu xotira har bir worker'da alohida bo'ladi.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}


# CORS
# ⚠️ MUHIM: Ilgari CORS_ALLOW_ALL_ORIGINS=True edi — bu istalgan saytdan
# (jumladan zararli saytdan) API'ga so'rov yuborishga ruxsat berardi.
# Endi faqat quyidagi domenlardan so'rov qabul qilinadi. Yangi frontend
# domen (masalan Vercel) qo'shsangiz, Render'da CORS_EXTRA_ORIGINS env
# o'zgaruvchisiga vergul bilan ajratib qo'shing:
#   CORS_EXTRA_ORIGINS=https://itline.vercel.app,https://itline.uz
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    # Windows'da localhost ko'pincha IPv6 (::1) ga hal qilinadi — ikkalasini ham qabul qilamiz
    "http://[::1]:5173",
    "http://[::1]:8080",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    # Menejer paneli (Vercel'da deploy qilingan)
    "https://crmfr.vercel.app",
] + [
    origin.strip()
    for origin in os.environ.get("CORS_EXTRA_ORIGINS", "").split(",")
    if origin.strip()
]

CORS_ALLOW_CREDENTIALS = True

# Panel har bir so'rovda JWT tokenni 'Authorization' sarlavhasida,
# qurilma ID'sini esa 'X-Device-Id' da yuboradi (supermenejer qaysi
# qurilmadan kirilganini shu orqali ko'radi). Standart bo'lmagan
# sarlavha CORS preflight'ni ishga tushiradi — ro'yxatga qo'shilmasa
# brauzer so'rovni bloklaydi va sahifada "Internet aloqasi yo'q"
# ko'rinadi. Yangi sarlavha qo'shsangiz, shu ro'yxatga ham qo'shing.
#
# Eslatma: eski 'X-User-Phone' sarlavhasi endi ishlatilmaydi (u
# istalgan qiymatga o'zgartirilishi mumkin edi) — ro'yxatdan olib
# tashlandi, token o'rniga JWT 'Authorization' ishlatiladi.
from corsheaders.defaults import default_headers  # noqa: E402

CORS_ALLOW_HEADERS = (*default_headers, "x-device-id")

# ─────────────────────────────
# TELEGRAM BOT (o'quvchilarga xabar yuborish)
# Tavsiya: tokenni Render'da TG_BOT_TOKEN env o'zgaruvchisiga ko'chiring
# ─────────────────────────────
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")

# Bot username (@siz) — frontend'ga "botga kiring" havolasini ko'rsatish uchun.
# Bo'sh qoldirilsa `set_webhook` buyrug'i chiqargan nomdan foydalaning.
TG_BOT_USERNAME = os.environ.get("TG_BOT_USERNAME", "excellence_school_kokand_bot")

# Backend'ning tashqi manzili — webhook'ni ro'yxatdan o'tkazish uchun.
# Bu maxfiy emas (manzil baribir hammaga ko'rinadi), shuning uchun
# standart qiymat shu yerda turadi va env o'zgaruvchisi shart emas.
# Boshqa domenga ko'chsangiz PUBLIC_BASE_URL orqali almashtirasiz.
PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL", "https://itline-django-9s85.onrender.com"
).rstrip("/")

# Menejer panelining manzili. Telegram lead xabaridagi "Bazaga qo'shish"
# tugmasi shu manzilga /add-student yo'lini ochadi — lead ma'lumotlari
# forma URL orqali uzatiladi. Sozlanmasa tugma chiqmaydi.
PANEL_BASE_URL = os.environ.get("PANEL_BASE_URL", "").rstrip("/")

# Webhook maxfiy kaliti. Telegram har bir so'rovda buni
# 'X-Telegram-Bot-Api-Secret-Token' sarlavhasida qaytaradi — shu orqali
# soxta (begona) so'rovlarni rad etamiz.
#
# Alohida env o'zgaruvchisi shart emas: berilmasa SECRET_KEY'dan
# hosil qilinadi. SECRET_KEY allaqachon maxfiy va Render'da bor, ya'ni
# kalit ham maxfiy bo'ladi-yu, sozlaydigan narsa kamayadi. Telegram
# kalit uzunligini 1..256 belgi va faqat A-Z a-z 0-9 _ - deb cheklaydi,
# shuning uchun hex ishlatamiz.
TG_WEBHOOK_SECRET = os.environ.get("TG_WEBHOOK_SECRET", "")
if not TG_WEBHOOK_SECRET:
    import hashlib

    TG_WEBHOOK_SECRET = hashlib.sha256(
        f"tg-webhook:{SECRET_KEY}".encode()
    ).hexdigest()

# ─────────────────────────────
# ROL KODLARI
#
# Ro'yxatdan o'tishda kiritilgan parol shu kodlardan biriga teng bo'lsa
# oddiy o'quvchi emas, ustoz yoki menejer profili ochiladi. `ADMIN_PASSWORD`
# ayni paytda "Ustozlar" sahifasidan qo'shilgan ustozning boshlang'ich
# paroli hamdir — ustoz shu kod bilan kiradi va profilida o'zinikiga
# almashtiradi.
#
# Standart qiymatlar kodda turadi, chunki frontend ham xuddi shularni
# biladi (`ROLE_PASSWORDS`) va ekranda ochiq ko'rsatadi ("Boshlang'ich
# parol: excel2024") — bu sir emas, ish kodi. Ilgari bu yerda bo'sh satr
# turgani uchun ikki tomon kelishmay qolgan edi: panel orqali qo'shilgan
# ustozga parol sifatida bo'sh satr yozilib, u tizimga kira olmasdi.
#
# Haqiqiy sir kerak bo'lsa Render'da ADMIN_PASSWORD/EXCELLENCE_PASSWORD
# muhit o'zgaruvchisi orqali almashtiriladi (frontend `ROLE_PASSWORDS`
# ham birga yangilanishi shart).
# ─────────────────────────────
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "excel2024")
EXCELLENCE_PASSWORD = os.environ.get("EXCELLENCE_PASSWORD", "excellence2024")

if not DEBUG and (
    os.environ.get("ADMIN_PASSWORD") is None
    or os.environ.get("EXCELLENCE_PASSWORD") is None
):
    import logging

    logging.warning(
        "⚠️ OGOHLANTIRISH: ADMIN_PASSWORD yoki EXCELLENCE_PASSWORD Render "
        "environment'da o'rnatilmagan — standart (kodda ochiq turgan) "
        "parol ishlatilmoqda. Render dashboard > Environment bo'limida "
        "shu ikkalasini o'zgartiring."
    )