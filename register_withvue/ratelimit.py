"""Oddiy rate limiting — login endpoint'larini parol taxmin qilishdan
(brute-force) himoya qiladi.

Django cache asosida ishlaydi (settings.py'dagi CACHES, hozircha
locmem). Faqat bitta Render worker uchun mo'ljallangan — ko'p
worker/instance ishlatilsa umumiy cache (Redis) kerak bo'ladi, aks
holda har bir worker o'z hisobini alohida yuritadi va limit
kutilganidan ko'proq urinishga ruxsat berishi mumkin.
"""

from django.core.cache import cache
from django.http import JsonResponse


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def check_rate_limit(request, *, key_prefix, extra_key="", limit=5, window_seconds=300):
    """So'rov limitdan oshgan bo'lsa tayyor 429 javobini qaytaradi.

    IP manzil + (agar berilgan bo'lsa) telefon raqami bo'yicha sanaydi,
    shunda bitta odam turli IP orqali chetlab o'ta olmaydi va bitta
    ofis IP'sidagi boshqa foydalanuvchilar bloklanib qolmaydi.

    Muvaffaqiyatli urinishdan keyin `reset_rate_limit` chaqirilishi
    kerak, aks holda haqiqiy foydalanuvchi ham asossiz bloklanadi.
    """
    ip = _client_ip(request)
    cache_key = f"ratelimit:{key_prefix}:{ip}:{extra_key}"
    attempts = cache.get(cache_key, 0)
    if attempts >= limit:
        return JsonResponse(
            {
                "error": (
                    f"Juda ko'p urinish. {window_seconds // 60} daqiqadan "
                    "keyin qayta urinib ko'ring."
                )
            },
            status=429,
        )
    cache.set(cache_key, attempts + 1, timeout=window_seconds)
    return None


def reset_rate_limit(request, *, key_prefix, extra_key=""):
    """Muvaffaqiyatli login/urinishdan keyin hisoblagichni tozalaydi."""
    ip = _client_ip(request)
    cache_key = f"ratelimit:{key_prefix}:{ip}:{extra_key}"
    cache.delete(cache_key)