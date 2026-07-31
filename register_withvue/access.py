"""Menejer vakolatlari, supermenejer tekshiruvi va qurilma hisobi.

Loyihada sessiya/token autentifikatsiyasi yo'q — chaqiruvchi
'X-User-Phone' sarlavhasi orqali aniqlanadi (views.py'dagi
`_require_staff` bilan bir xil yondashuv). Qurilma esa brauzerda bir
marta yaratilib localStorage'da saqlanadigan 'X-Device-Id' bilan.

⚠️ Bu sarlavhalarni soxtalashtirish mumkin. Maqsad — vakolatlarni
ajratish va supermenejerga kirishlarni ko'rsatish, kriptografik himoya
emas. Haqiqiy himoya uchun token/sessiya alohida qo'shilishi kerak.
"""

import re

from django.http import JsonResponse
from django.utils import timezone

from .models import LoginDevice, Manager

MIN_PHONE_KEY_LEN = 7


def phone_key(phone):
    """Telefonni solishtirish uchun normal ko'rinishga keltiradi."""
    d = re.sub(r"\D", "", str(phone or ""))
    if len(d) > 9 and d.startswith("998"):
        d = d[3:]
    return d[-9:] if len(d) >= 9 else d


def find_manager_by_phone(phone, active_only=True):
    """Menejerni telefon bo'yicha topadi — format farqiga qaramasdan."""
    qs = Manager.objects.filter(is_active=True) if active_only else Manager.objects.all()
    exact = qs.filter(phone=phone).first()
    if exact:
        return exact
    target = phone_key(phone)
    if len(target) < MIN_PHONE_KEY_LEN:
        return None
    for m in qs:
        if phone_key(m.phone) == target:
            return m
    return None


# ─────────────────────────────────────────
# VAKOLATLAR KATALOGI
# ─────────────────────────────────────────
#
# Har bir yozuv: (kalit, sarlavha, bo'lim). Supermenejer menejer
# qo'shayotganda shu ro'yxatni ko'radi va kerakligini belgilaydi.
# Yangi funksiya qo'shilsa — shu yerga ham qo'shiladi, panelda
# avtomatik chiqadi.

PERMISSIONS = [
    # To'lovlar
    ("payments.view", "To'lovlarni ko'rish", "To'lovlar"),
    ("payments.edit", "To'lov summasi va holatini o'zgartirish", "To'lovlar"),
    ("payments.generate", "Oylik to'lovlarni yaratish", "To'lovlar"),
    ("payments.discount", "Chegirma berish", "To'lovlar"),
    ("payments.requests", "To'lov so'rovlarini qabul/rad qilish", "To'lovlar"),
    ("payments.settings", "To'lov kartasini o'zgartirish", "To'lovlar"),
    # O'quvchilar
    ("students.view", "O'quvchilar ro'yxatini ko'rish", "O'quvchilar"),
    ("students.add", "O'quvchi qo'shish", "O'quvchilar"),
    ("students.edit", "O'quvchi ma'lumotini tahrirlash", "O'quvchilar"),
    ("students.delete", "O'quvchini o'chirish", "O'quvchilar"),
    ("students.transfer", "O'quvchini boshqa ustozga ko'chirish", "O'quvchilar"),
    # Ustozlar
    ("teachers.view", "Ustozlar ro'yxatini ko'rish", "Ustozlar"),
    ("teachers.add", "Ustoz qo'shish", "Ustozlar"),
    ("teachers.edit", "Ustoz ma'lumotini tahrirlash", "Ustozlar"),
    ("teachers.delete", "Ustozni o'chirish", "Ustozlar"),
    # Menejerlar — yangi menejer yaratish faqat supermenejerda,
    # bu yerdagilari mavjud yozuvlarni ko'rish/tahrirlash uchun
    ("managers.view", "Menejerlar ro'yxatini ko'rish", "Menejerlar"),
    ("managers.edit", "Menejer raqamini tahrirlash va o'chirish", "Menejerlar"),
    # Guruhlar va darslar
    ("groups.view", "Guruhlarni ko'rish", "Guruhlar"),
    ("groups.edit", "Guruh yaratish va tahrirlash", "Guruhlar"),
    ("groups.delete", "Guruhni o'chirish", "Guruhlar"),
    ("attendance.view", "Davomatni ko'rish", "Guruhlar"),
    ("attendance.edit", "Davomat belgilash", "Guruhlar"),
    # Kurslar
    ("courses.view", "Kurslarni ko'rish", "Kurslar"),
    ("courses.edit", "Kurs va narxlarni o'zgartirish", "Kurslar"),
    # Do'kon va coinlar
    ("shop.products", "Mahsulotlarni boshqarish", "Do'kon"),
    ("shop.orders", "Buyurtmalarni tasdiqlash", "Do'kon"),
    ("coins.settings", "Coin sozlamalarini o'zgartirish", "Do'kon"),
    ("coins.give", "Qo'lda coin berish", "Do'kon"),
    # Aloqa
    ("messages.send", "Telegram orqali xabar yuborish", "Aloqa"),
    ("news.manage", "Yangiliklarni boshqarish", "Aloqa"),
    # Hisobot
    ("history.view", "To'lovlar tarixini ko'rish", "Hisobot"),
    ("database.view", "Baza (leadlar, bitiruvchilar) ko'rish", "Hisobot"),
]

PERMISSION_KEYS = {key for key, _label, _section in PERMISSIONS}

# Yangi menejer uchun standart to'plam — kundalik ish uchun yetarli,
# o'chirish/ko'chirish kabi qaytarib bo'lmaydigan amallar kirmaydi.
DEFAULT_PERMISSIONS = [
    "payments.view",
    "payments.edit",
    "payments.requests",
    "students.view",
    "teachers.view",
    "groups.view",
    "attendance.view",
    "attendance.edit",
    "courses.view",
    "history.view",
]


def permission_catalog():
    """Frontend uchun bo'limlarga ajratilgan vakolatlar ro'yxati."""
    sections = {}
    for key, label, section in PERMISSIONS:
        sections.setdefault(section, []).append({"key": key, "label": label})
    return [
        {"section": name, "items": items} for name, items in sections.items()
    ]


def clean_permissions(value):
    """Kelgan ro'yxatdan faqat mavjud kalitlarni qoldiradi."""
    if not isinstance(value, list):
        return []
    seen, result = set(), []
    for key in value:
        if key in PERMISSION_KEYS and key not in seen:
            seen.add(key)
            result.append(key)
    return result


# ─────────────────────────────────────────
# TEKSHIRUVLAR
# ─────────────────────────────────────────


def caller_phone(request):
    return (request.headers.get("X-User-Phone") or "").strip()


def caller_manager(request):
    """Chaqiruvchi menejer bo'lsa — o'sha obyekt, aks holda None."""
    phone = caller_phone(request)
    if not phone:
        return None
    return find_manager_by_phone(phone)


def require_super(request):
    """Faqat supermenejerga ruxsat. Mos kelsa None, aks holda 403."""
    manager = caller_manager(request)
    if manager and manager.is_super:
        return None
    return JsonResponse(
        {"error": "Bu bo'lim faqat supermenejer uchun"}, status=403
    )


def require_permission(request, key):
    """Menejerda shu vakolat bormi. Supermenejerda hammasi bor.

    Menejer bo'lmagan chaqiruvchilar (ustoz/admin o'quvchi) bu
    tekshiruvdan o'tadi — ularning cheklovi alohida `_require_staff`
    bilan hal qilinadi, vakolatlar tizimi menejerlarga tegishli.
    """
    manager = caller_manager(request)
    if manager is None:
        return None
    if manager.has_perm(key):
        return None
    return JsonResponse(
        {"error": "Bu amal uchun vakolatingiz yo'q"}, status=403
    )


# ─────────────────────────────────────────
# QURILMALAR
# ─────────────────────────────────────────


def device_id(request):
    return (request.headers.get("X-Device-Id") or "").strip()[:64]


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def is_device_blocked(request, phone):
    """Shu qurilma+foydalanuvchi juftligi bloklanganmi."""
    did = device_id(request)
    if not did:
        return False
    return LoginDevice.objects.filter(
        device_id=did, phone=phone, is_blocked=True
    ).exists()


def record_login(request, *, phone, role, user_name="", manager=None):
    """Muvaffaqiyatli loginni yozib qo'yadi.

    Qurilma ID bo'lmasa (eski frontend yoki bot) hech narsa yozilmaydi —
    aks holda barcha kirishlar bitta bo'sh ID ostida qo'shilib ketardi.
    """
    did = device_id(request)
    if not did or not phone:
        return None

    device, _created = LoginDevice.objects.get_or_create(
        device_id=did,
        phone=phone,
        defaults={"role": role, "manager": manager},
    )
    device.role = role
    device.manager = manager
    device.user_name = (user_name or "")[:200]
    device.user_agent = (request.headers.get("User-Agent") or "")[:400]
    device.ip = client_ip(request)
    device.login_count = (device.login_count or 0) + 1
    device.last_seen = timezone.now()
    device.save()
    return device
