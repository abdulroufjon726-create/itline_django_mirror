"""\"Men robot emasman\" tekshiruvi — uchinchi tomarsiz (hCaptcha/Google'siz).

Qanday ishlaydi:
  1. Frontend GET /api/captcha/new/ qiladi → {id, question} oladi
     (masalan "7 + 5 = ?").
  2. Foydalanuvchi javobini yozadi; forma yuborilganda captcha_id +
     captcha_answer birga ketadi.
  3. Backend `verify_captcha()` bilan tekshiradi:
     - javob mosmi (bir martalik — tekshirilgach o'chadi),
     - muddati o'tmaganmi (10 daqiqa TTL),
     - juda tez yuborilganmi (odam yozishi uchun kamida 2 sekund).

Botlar javobni o'qiy olmaydi — har urinishda yangi savol kerak bo'ladi
va bir javob bir martaga ishlaydi.
"""
import secrets
import time

from django.core.cache import caches

from django.http import JsonResponse

# Alohida cache: captcha endpointini to'lib-toshqin so'rovlar bilan
# bosib, asosiy cache'dagi rate-limit hisoblagichlarini evict
# qilinishining oldini oladi (locmem LRU).
cache = caches["captcha"]

CAPTCHA_TTL_SECONDS = 600  # 10 daqiqa — keyin eskiradi
CAPTCHA_MIN_FILL_SECONDS = 2  # odam savolni o'qib javob yozishi kerak
_CAPTCHA_PREFIX = "captcha:"


def _captcha_key(captcha_id):
    return f"{_CAPTCHA_PREFIX}{captcha_id}"


def new_captcha():
    """Yangi savol yaratadi: {id, question} — frontendga JSON qaytadi."""
    a = secrets.randbelow(9) + 2  # 2..10
    b = secrets.randbelow(9) + 2  # 2..10
    if secrets.randbelow(2):  # 50% ayirish
        if b > a:
            a, b = b, a
        question = f"{a} - {b} = ?"
        answer = a - b
    else:
        question = f"{a} + {b} = ?"
        answer = a + b

    captcha_id = secrets.token_urlsafe(16)
    cache.set(
        _captcha_key(captcha_id),
        {"answer": answer, "created": time.time()},
        timeout=CAPTCHA_TTL_SECONDS,
    )
    return JsonResponse({"id": captcha_id, "question": question})


def verify_captcha(captcha_id, answer_text, created_hint=None):
    """Captcha javobini tekshiradi.

    captcha_id / answer — frontend yuborgan satrlar.
    created_hint — captcha berilgan vaqt (epoch). Berilmasa cache'dan
    olinadi. Muvaffaqiyatli tekshiruvdan keyin captcha o'chadi (bir martalik).

    Qaytaradi: (ok: bool, error: str|None)
    """
    if not captcha_id or answer_text is None:
        return False, "Captcha to'ldirilmagan"

    key = _captcha_key(str(captcha_id))
    data = cache.get(key)
    if data is None:
        if created_hint and (time.time() - float(created_hint)) < CAPTCHA_TTL_SECONDS:
            # Cache'da yo'q lekin yangi — ehtimol allaqachon ishlatilgan
            return False, "Captcha allaqachon ishlatilgan — yangisini oling"
        return False, "Captcha muddati tugadi — sahifani yangilang"

    # juda tez yuborish — bot belgisi
    elapsed = time.time() - data["created"]
    if elapsed < CAPTCHA_MIN_FILL_SECONDS:
        cache.delete(key)
        return False, "Juda tez yuborildi — yana urinib ko'ring"

    try:
        typed = int(str(answer_text).strip())
    except (ValueError, TypeError):
        cache.delete(key)
        return False, "Captcha javobi noto'g'ri"

    if typed != data["answer"]:
        cache.delete(key)
        return False, "Captcha javobi noto'g'ri"

    cache.delete(key)  # bir martalik
    return True, None
