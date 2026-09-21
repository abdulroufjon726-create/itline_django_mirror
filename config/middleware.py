import logging

from django.core.cache import caches
from django.http import JsonResponse

logger = logging.getLogger(__name__)

# VPN/Datacenter blokirovka BUTUN API'GA tatbiq etiladi: VPN yoniq
# foydalanuvchi crm paneliga ham, landing formalariga ham kira olmaydi
# (foydalanuvchi talabi — majburiy). Faqat infratuzilma endpoint'lari
# mustasno:
#   * /api/tg/webhook/  — Telegram serveri datacenter IP'dan keladi,
#   * /api/ping/        — uptime monitorlar ham serverdan so'raydi.
# Blok 405 (Method not allowed) so'rovlariga ham tatbiq etilmaydi.
VPN_BLOCKED_PATHS = None  # eski ro'yxat — ishlatilmaydi (butun API bloklanadi)
VPN_EXEMPT_PATHS = (
    "/api/tg/webhook/",
    "/api/ping/",
)
# Blok qarori 6 soat cache'da turadi — har so'rovda tashqi xizmatga
# murojaat qilmaslik uchun.
VPN_CACHE_TTL = 60 * 60 * 6


class VpnBlockMiddleware:
    """VPN/proxy/datacenter IP'laridan BUTUN API'ga kirishni bloklaydi.

    Foydalanuvchi VPN yoniq holda crm paneliga ham, landing formalariga
    ham so'rov yuborsa 403 oladi — VPN'ni o'chirmasdan hech qanday
    ma'lumot ko'ra olmaydi. Aniqlash: ip-api.com security maydonlari
    (proxy/hosting) — natija IP bo'yicha 6 soat cache'da turadi,
    sekinlashtirmaydi.
    Geo xizmat javob bermasa — SO'ROV O'TADI (false positive oldini
    olish, xizmat o'chsa sayt ham o'chib qolmasin).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if (
            path.startswith("/api/")
            and path not in VPN_EXEMPT_PATHS
            and request.method != "OPTIONS"
        ):
            from register_withvue.geo import is_vpn_or_hosting
            from register_withvue.ratelimit import _client_ip

            ip = _client_ip(request)
            if ip:
                cache = caches["captcha"]  # alohida cache — eviksiyadan himoya
                cache_key = f"vpncheck:{ip}"
                verdict = cache.get(cache_key)
                if verdict is None:
                    from register_withvue.geo import geo_for_ip

                    geo = geo_for_ip(ip)
                    verdict = "vpn" if is_vpn_or_hosting(geo) else "clean"
                    cache.set(cache_key, verdict, timeout=VPN_CACHE_TTL)
                if verdict == "vpn":
                    logger.warning(
                        "VPN/hosting IP bloklandi (IP=%s, path=%s)", ip, path
                    )
                    return JsonResponse(
                        {
                            "error": (
                                "VPN yoki proksi aniqlandi. Iltimos, VPN'ni "
                                "o'chirib qayta urinib ko'ring."
                            ),
                            "vpn_blocked": True,
                        },
                        status=403,
                    )
        return self.get_response(request)


class JsonExceptionMiddleware:
    """Catch unhandled exceptions, log traceback and return a JSON 500.

    This middleware helps in production by ensuring the process logs the
    exception (visible in hosting logs) while returning a simple JSON
    response so frontends don't attempt to parse HTML error pages.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            return self.get_response(request)
        except Exception:
            logging.exception("Unhandled exception during request")
            return JsonResponse({"error": "Internal server error"}, status=500)


# ─────────────────────────────────────────────────────────────────
# API AUTH GATE — "sukut bo'yicha rad etish" (deny by default)
# ─────────────────────────────────────────────────────────────────
#
# Muammo: panel API'sining ko'p qismi (o'quvchilar, ustozlar, leadlar,
# to'lovlar, xabarlar tarixi) umuman autentifikatsiyasiz edi. Bir
# marta JWT qo'shilgan view'lar bor, boshqalari esa ochiq qolgan —
# yangi endpoint qo'shilsa, muallif tekshiruv yozishini unutishi
# mumkin edi. Endi mantiq teskari: HAR BIR /api/ so'rovi JWT token
# bilan ochiladi, faqat quyidagi ommaviy ro'yxatdan tashqari.
#
# Ommaviy (token talab qilmaydigan) endpoint'lar:
#   * login/register/parol o'zgartirish — foydalanuvchi hali tokenga
#     ega emas,
#   * Telegram webhook va Face ID terminali — ular o'z maxfiy
#     kalitlari bilan himoyalangan (tg_webhook, faceid_event),
#   * ping — uptime monitoring uchun.
#
# Bir marta kirmagan (token yo'q) so'rovga 401 qaytadi — frontend
# uni avtomatik login sahifasiga yo'naltiradi.
# ─────────────────────────────────────────────────────────────────

# Ikkala path usuli ham qo'shiladi — trailing slash bor/yo'qligiga
# qaramasdan ishlashi uchun.
PUBLIC_API_PATHS = (
    "/api/ping/",
    "/api/login/",  # o'quvchi/ustoz login (login_student)
    "/api/register/",  # o'quvchi ro'yxatdan o'tishi
    "/api/manager/login/",  # menejer login
    "/api/token/refresh/",  # access token yangilash
    "/api/change-password/",  # parol almashtirish (bot orqali kod bilan)
    "/api/verify/send-code/",  # tasdiqlash kodi yuborish
    "/api/verify/check-code/",  # kodni tekshirish
    "/api/tg/webhook/",  # Telegram bot (X-Telegram-Bot-Api-Secret-Token)
    "/api/payment-requests/create/",  # o'quvchi chek yuboradi (bot chat_id bilan)
    "/api/site-lead/",  # landing saytdan murojaat (rate-limit view ichida)
    "/api/captcha/new/",  # men robot emasman savoli (ommaviy forma uchun)
)

PUBLIC_API_PREFIXES = (
    "/api/faceid/event/",  # terminal (URL ichidagi secret bilan)
    "/api/faceid/sync/",  # terminal agenti (URL ichidagi secret bilan)
    "/api/call/",  # imzolangan tel: redirect (Telegram qo'ng'iroq tugmasi)
)


class ApiAuthGateMiddleware:
    """Har bir /api/ so'rovini JWT token bilan tekshiradi.

    Token bo'lmasa/imzosi yaroqsiz bo'lsa 401 qaytaradi. Ommaviy
    endpoint'lar (PUBLIC_API_PATHS / PUBLIC_API_PREFIXES) undan
    tashqarida — ularning ba'zilari view ichida o'z tekshiruviga ega
    (masalan webhook secret'i), qolganlari tabiatan ommaviy (login).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path.startswith("/api/"):
            allowed = (
                path in PUBLIC_API_PATHS
                or any(path.startswith(p) for p in PUBLIC_API_PREFIXES)
            )
            if not allowed:
                from register_withvue.access import caller_phone

                if not caller_phone(request):
                    logger.warning(
                        "API auth gate: token yuborilmagan (IP=%s, path=%s)",
                        request.META.get("REMOTE_ADDR"),
                        path,
                    )
                    return JsonResponse(
                        {"error": "Avtorizatsiya talab qilinadi (JWT token kerak)"},
                        status=401,
                    )
        return self.get_response(request)
