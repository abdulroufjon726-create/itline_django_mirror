"""JWT asosidagi autentifikatsiya.

Nima uchun kerak bo'ldi: loyihada ilgari chaqiruvchi kimligini
'X-User-Phone' sarlavhasi orqali aniqlagan — bu sarlavhani brauzer
DevTools yoki Postman orqali istalgan qiymatga o'zgartirish mumkin
edi, ya'ni har qanday odam o'zini menejer qilib ko'rsatib, boshqa
o'quvchilarning ma'lumotini ko'rishi mumkin edi.

Endi login muvaffaqiyatli bo'lganda server SECRET_KEY bilan
imzolangan token beradi. Token ichidagi 'phone' va 'role' claim'lari
imzoni bilmagan hech kim tomonidan o'zgartirib bo'lmaydi — imzo mos
kelmasa, token butunlay rad etiladi. Shu orqali "kim ekanini
soxtalashtirish" imkoniyati yopiladi.

Frontend endi so'rovda 'X-User-Phone' emas, quyidagi sarlavhani
yuboradi:
    Authorization: Bearer <access_token>
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken


def issue_tokens(phone, role):
    """Login muvaffaqiyatli bo'lganda chaqiriladi — access+refresh token beradi.

    `role` frontend uchun emas (u javobning boshqa joyida bor),
    balki token ichida ham saqlanadi — token o'g'irlangan taqdirda
    ham qaysi turdagi foydalanuvchi ekani token ichida qoladi, bu esa
    keyinchalik kerak bo'lsa (masalan faqat 'teacher' role'iga ruxsat
    beradigan endpoint qo'shilsa) qo'shimcha DB so'roviga hojat
    qoldirmaydi.
    """
    refresh = RefreshToken()
    refresh["phone"] = phone
    refresh["role"] = role
    access = refresh.access_token
    access["phone"] = phone
    access["role"] = role
    return {"access": str(access), "refresh": str(refresh)}


def get_authenticated_phone(request):
    """So'rovdagi 'Authorization: Bearer <token>' dan tekshirilgan
    telefon raqamni qaytaradi.

    Token bo'lmasa, imzosi noto'g'ri bo'lsa yoki muddati o'tgan bo'lsa
    — bo'sh qator qaytadi (ya'ni "aniqlanmagan chaqiruvchi", parolsiz
    kirish huquqi emas). Shu funksiya orqali qaytgan telefon raqamga
    ISHONISH MUMKIN, chunki imzoni faqat server (SECRET_KEY orqali)
    yarata oladi.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return ""
    raw_token = auth_header[7:].strip()
    if not raw_token:
        return ""
    try:
        token = AccessToken(raw_token)
    except TokenError:
        return ""
    return str(token.get("phone", "") or "")


def get_authenticated_role(request):
    """Token ichidagi rolni qaytaradi ('manager', 'teacher', 'student').

    Faqat qo'shimcha ma'lumot sifatida — asosiy huquq tekshiruvi
    hamon DB'dagi Manager/Teacher yozuvi orqali bo'ladi
    (access.caller_manager va h.k.), chunki rol o'zgarishi (masalan
    menejer o'chirilishi) DB'da darhol ko'rinadi, tokenda esa muddati
    tugagunga qadar eski holicha qoladi.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return ""
    raw_token = auth_header[7:].strip()
    try:
        token = AccessToken(raw_token)
    except TokenError:
        return ""
    return str(token.get("role", "") or "")


@csrf_exempt
def refresh_view(request):
    """POST /api/token/refresh/ — refresh token bilan yangi access token oladi.

    Frontend access token muddati tugaganda (12 soat) shu endpoint'ga
    murojaat qiladi, foydalanuvchi qayta login qilishga majbur
    bo'lmaydi.

    ⚠️ csrf_exempt bo'lishi shart: boshqa barcha API endpoint'lar kabi
    bu ham cookie-sessiya emas, JWT sarlavhasi bilan ishlaydi — CSRF
    himoyasi brauzer cookie'lariga asoslangan hujumlar uchun kerak,
    bu yerda esa token qo'lda yuboriladi (ilmgari decorator tushib
    qolgan edi: token yangilash har safar 403 berardi).
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    import json

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    raw_refresh = (data.get("refresh") or "").strip()
    if not raw_refresh:
        return JsonResponse({"error": "refresh token kerak"}, status=400)

    try:
        refresh = RefreshToken(raw_refresh)
    except TokenError:
        return JsonResponse(
            {"error": "Refresh token yaroqsiz yoki muddati tugagan"}, status=401
        )

    phone = refresh.get("phone", "")
    role = refresh.get("role", "")

    access = refresh.access_token
    access["phone"] = phone
    access["role"] = role

    response = {"access": str(access)}

    # Yangi refresh token ham qaytariladi (frontend eskisini almashtiradi).
    # Eslatma: 'token_blacklist' ilovasi ulanmagani uchun eski refresh
    # token o'z muddati (30 kun) tugagunga qadar texnik jihatdan hali
    # ham amal qiladi. Bu xavfni pasaytirish uchun kelajakda
    # 'rest_framework_simplejwt.token_blacklist' ilovasini
    # INSTALLED_APPS'ga qo'shib, migratsiya qilish tavsiya etiladi.
    new_refresh = RefreshToken()
    new_refresh["phone"] = phone
    new_refresh["role"] = role
    response["refresh"] = str(new_refresh)

    return JsonResponse(response)