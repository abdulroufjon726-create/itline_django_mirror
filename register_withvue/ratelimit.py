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


def _is_cloudflare_ip(value):
    """IP Cloudflare edge'lariga tegishlimi (render.com CF ortida turadi)."""
    import ipaddress

    CF_RANGES = [
        "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22",
        "103.31.4.0/22", "141.101.64.0/18", "108.162.192.0/18",
        "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22",
        "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
        "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    ]
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(r) for r in CF_RANGES)


def _client_ip(request):
    """Haqiqiy mijoz IP'sini aniqlaydi.

    Render zanjiri: mijoz → Cloudflare edge (172.64.0.0/13) → Render
    ichki proxy (10.x/127.0.0.1) → ilova. Har bir proxy o'zi ko'rgan
    IP'ni X-Forwarded-For OXHIRIGA qo'shadi, demak XFF ko'pincha:
      [soxta_yozuvlar..., mijoz_ip, 172.71.x.x(CF edge), 10.x(ichki)]
    ko'rinishida keladi.

    Shuning uchun:
      1) So'rov Cloudflare orqali o'tgan bo'lsa (XFF/REMOTE_ADDR da CF
         edge IP ko'rinadi) — CF o'zi yozgan `CF-Connecting-IP`ga
         ishonamiz: u haqiqiy mijoz manzili, soxtalashtirib bo'lmaydi
         (CF uni har so'rovda o'zi yozib qo'yadi). XFF'dagi oxirgi
         ommaviy yozuvni olsak, ba'zan CF edge IP'ni "mijoz" deb olib
         qo'yardik — ular ip-api'da "hosting" chiqqani uchun toza
         foydalanuvchilar 403 olardi va joylashuv CF sifatida chiqardi.
      2) CF yo'q bo'lsa (lokal dev) — oxirgi OMMANDVIY yozuv, hammasi
         xususiy bo'lsa REMOTE_ADDR.
    """
    import ipaddress

    def _is_public(value):
        try:
            addr = ipaddress.ip_address(value)
        except ValueError:
            return False
        return not (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        )

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    candidates = [p.strip() for p in forwarded.split(",") if p.strip()] if forwarded else []
    remote = (request.META.get("REMOTE_ADDR") or "").strip()

    # OXIRGI ommaviy yozuv — ishonchli zanjirning oxirgi ko'rgan mijizi
    # (soxta yozuvlar undan OLDIN turadi, shuning uchun ular bu yerda
    # qatnashmaydi). U Cloudflare edge bo'lsa, haqiqiy mijoz CF o'zi
    # yozgan CF-Connecting-IP'da (soxtalashtirib bo'lmaydi — CF
    # mijoz yuborilganini o'chirib, o'zinikini yozadi).
    last_public = None
    for cand in reversed(candidates):
        if _is_public(cand):
            last_public = cand
            break

    if _is_cloudflare_ip(remote) or (last_public and _is_cloudflare_ip(last_public)):
        cf_ip = (request.META.get("HTTP_CF_CONNECTING_IP") or "").strip()
        if cf_ip and _is_public(cf_ip):
            return cf_ip[:64]
        # CF sarlavhasi yo'q — oxirgi ommaviy (CF edge) qaytadi
        return (last_public or remote)[:64]

    if last_public:
        return last_public[:64]
    # Ommaviy IP topilmadi (lokal ishlab chiqish yoki barcha yozuvlar
    # xususiy) — to'g'ridan-to'g'ri ulanuvchini olamiz: limit baribir
    # ishlaydi va soxta XFF bilan almashtirib bo'lmaydi.
    return remote[:64]


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