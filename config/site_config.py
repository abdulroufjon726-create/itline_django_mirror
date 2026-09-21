"""Markazga xos barcha sozlamalar BITTA JOYDA.

Boshqa o'quv markazga loyihani sotganda FAQAT SHU FAYL va .env
o'zgartiriladi — qolgan barcha modullar shu yerdan import qiladi:

    from config.site_config import SITE_NAME, TG_BOT_USERNAME

Maxfiylar (token, parol) BU YERDA SAQLANMAYDI — ular .env /
Render Environment'da turadi. Shu tarzda qiymatni bitta joydan
almashtirsangiz, u ishlatilgan barcha joylarda avtomatik yangilanadi.
"""
import os

# ── Markaz identifikatori (ommaviy — kodda ko'rinishi mumkin) ──

# Markaz nomi — bot xabarlari, SMS matnlari, panel sarlavhalari
SITE_NAME = os.environ.get("SITE_NAME", "ITLINE")

# To'liq nom — landing, ogohlantirish matnlarida
SITE_NAME_FULL = os.environ.get("SITE_NAME_FULL", "ITLINE o'quv markazi")

# Bot username (@siz) — "botga kiring" havolasi uchun
TG_BOT_USERNAME = os.environ.get("TG_BOT_USERNAME", "excellence_school_kokand_bot")

# ── Manzillar (ommaviy) ──

# Backend (webhook shu bo'yicha o'rnatiladi)
PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL", "https://davomat-django-zbn4.onrender.com"
).rstrip("/")

# Menejer paneli (frontend)
PANEL_BASE_URL = os.environ.get("PANEL_BASE_URL", "https://crmfr.vercel.app").rstrip("/")

# Landing sahifa
LANDING_BASE_URL = os.environ.get("LANDING_BASE_URL", "https://excellences-school.vercel.app").rstrip("/")

# ── Brend materiallari (ommaviy) ──

# Qo'llab-quvvatlash aloqalari — landing, bot xabarlari, rasmiylashtirish
SUPPORT_PHONE = os.environ.get("SUPPORT_PHONE", "+998 90 562 33 45")
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "info@itline.uz")
SUPPORT_TG_USERNAME = os.environ.get("SUPPORT_TG_USERNAME", "itline_support")

# Telegram guruh QR kodi (rasm URL) — to'lov guruhiga qo'shilish uchun
# Lokalda: panel "Veb-sayt" QR kartochkasi shu fayldan ishlatadi
# (crm_fr/src/icon/telegram_QR.png).
TELEGRAM_QR_URL = os.environ.get("TELEGRAM_QR_URL", "")

# Logotip (rasm URL) — Telegram xabarlariga attach qilinadi (bo'sh bo'lsa
# yuborilmaydi). Lokalda fayl: crm_fr/src/icon/itline.png
LOGO_URL = os.environ.get("LOGO_URL", "")

# ── Telegram SUPERADMIN — barcha muhim xabarlar shunga boradi ──

# Botga birinchi /start qilganda avtomatik menejer sifatida ulanadigan
# raqam (faqat oxirgi 9 xona — masalan 905623345). Ushbu raqamning
# Telegram akkaunti menejer sifatida taniladi.
SUPERADMIN_PHONE_LAST9 = os.environ.get("SUPERADMIN_PHONE_LAST9", "")

# Muhabbatga oid xabarlarni qo'shimcha yuborish uchun chat_id.
# Bo'sh qoldirilsa — faqat SUPERADMIN_PHONE_LAST9 orqali aniqlanadi.
SUPERADMIN_CHAT_ID = os.environ.get("SUPERADMIN_CHAT_ID", "")

# ── Maxfiylar (.env / Render Environment'da) ──

# Telegram bot tokeni — BU YERDA EMAS, .env'da:
#   TG_BOT_TOKEN=...
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")

# Webhook maxfiy kaliti — bo'sh qoldirilsa SECRET_KEY'dan hosil qilinadi
TG_WEBHOOK_SECRET = os.environ.get("TG_WEBHOOK_SECRET", "")

# ── Rate limit / xavfsizlik (kuchaytirish uchun sozlanadi) ──

# Bir IP'dan kuniga qancha lead yuborish mumkin
LEAD_PER_IP_DAILY_LIMIT = int(os.environ.get("LEAD_PER_IP_DAILY_LIMIT", "20"))

# Bir IP'dan bir daqiqada qancha lead (burst limit)
LEAD_PER_IP_MINUTE_LIMIT = int(os.environ.get("LEAD_PER_IP_MINUTE_LIMIT", "5"))

# Parol eng kam uzunligi (yangi parol o'rnatayotganda)
MIN_PASSWORD_LENGTH = int(os.environ.get("MIN_PASSWORD_LENGTH", "6"))

# Parolda kamida bitta harf VA bitta raqam bo'lishi kerakmi
REQUIRE_ALNUM_PASSWORD = os.environ.get("REQUIRE_ALNUM_PASSWORD", "1") == "1"
