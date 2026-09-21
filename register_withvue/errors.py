"""Xato xabarlarini foydalanuvchiga XAVFSIZ qaytarish.

Muammo: `JsonResponse({"error": str(e)})` istisno to'liq matnini
foydalanuvchiga yuboradi — SQL xatolari bazadagi jadval/ustun nomlarini,
kutubxona xatlari fayl yo'llarini, ba'zi xatolar esa maxfiy qiymatlarni
oshkor qilishi mumkin. Hujumchi bu ma'lumotlardan tizim tuzilishini
o'rganish uchun foydalanadi.

Yechim: barcha view'lar `safe_error(e)` ishlatadi:
  - Bizning tekshirilgan xatolarimiz (ValueError, app ichidagi
    istisnolar — masalan RangeError) matni o'zicha qaytaradi;
  - Boshqa har qanday istisno (DatabaseError, TypeError, ...) logga
    to'liq yozilib, foydalanuvchiga umumiy matn qaytariladi.
"""
import json
import logging

log = logging.getLogger("register_withvue.errors")

# Xabari foydalanuvchiga o'zicha qaytariladigan standart istisnolar.
# Bu xatolar bizning tekshiruvlarimizdan chiqadi (masalan "Telefon raqam
# noto'g'ri") va maxfiy ma'lumot o'z ichiga olmaydi.
SAFE_EXCEPTION_TYPES = (ValueError, json.JSONDecodeError)

DEFAULT_ERROR_TEXT = "Xatolik yuz berdi. Qayta urinib ko'ring."


def safe_error(e: BaseException, fallback: str = DEFAULT_ERROR_TEXT) -> str:
    """Istisno xabarini foydalanuvchiga berish uchun xavfsiz matnga aylantiradi.

    - ValueError / JSONDecodeError va register_withvue ichidagi
      istisnolar (masalan RangeError) — matni qaytariladi.
    - Qolgan hamma istisno — to'liq xabar logga yozilib, umumiy matn
      qaytariladi (bazaviy/fayl/maxfiy ma'lumot sizilmaydi).
    """
    if isinstance(e, SAFE_EXCEPTION_TYPES):
        return str(e).strip() or fallback
    if type(e).__module__.startswith("register_withvue"):
        return str(e).strip() or fallback
    log.warning(
        "istisno tafsilotlari foydalanuvchiga yashirildi: %s: %s",
        type(e).__name__,
        e,
        exc_info=True,
    )
    return fallback
