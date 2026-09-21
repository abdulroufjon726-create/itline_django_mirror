"""IP bo'yicha joylashuv va qurilma ma'lumotini aniqlash.

Landing'dan kelgan har bir murojaat qayerdan (mamlakat/shahar), qaysi
IP va qanday qurilma orqali yuborilgani aniqlanadi — spam tahlili va
menejerga to'liq ma'lumot uchun.

Bepul xizmat: ip-api.com (kalit talab qilmaydi, 45 so'rov/daqiqa).
Natija 24 soat cache'da — bir IP uchun faqat bir marta tashqi so'rov.
Xizmat javob bermasa ham ish davom etadi (faqat IP yoziladi).
"""
import json
import logging
import urllib.request

from django.core.cache import cache

from .ratelimit import _client_ip

logger = logging.getLogger(__name__)

_GEO_CACHE_PREFIX = "geo:"
_GEO_TTL = 60 * 60 * 24  # 24 soat
_GEO_TIMEOUT = 3  # sekund — sayt sekinlashmasin


def _fetch_geo(ip):
    """ip-api.com dan joylashuv + hosting/proxy belgisini oladi.

    Xato bo'lsa None. `proxy, hosting, mobile` maydonlari ip-api
    security-extension'idan: proxy=True yoki hosting=True bo'lgan IP
    VPN/Datacenter (AWS, Google Cloud, NordVPN va h.k.) bo'ladi.
    """
    url = (
        f"http://ip-api.com/json/{ip}"
        "?fields=country,city,regionName,isp,query,proxy,hosting,mobile"
    )
    try:
        with urllib.request.urlopen(url, timeout=_GEO_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("query"):  # xizmat o'zi "status":"fail" qaytarishi mumkin
                return {
                    "country": str(data.get("country") or "")[:50],
                    "city": str(data.get("city") or "")[:50],
                    "region": str(data.get("regionName") or "")[:50],
                    "isp": str(data.get("isp") or "")[:100],
                    "proxy": bool(data.get("proxy")),
                    "hosting": bool(data.get("hosting")),
                    "mobile": bool(data.get("mobile")),
                }
    except Exception:  # noqa: BLE001 — tashqi xizmat muhim emas
        logger.debug("Geo lookup failed for %s", ip, exc_info=True)
    return None


def is_vpn_or_hosting(geo):
    """Geo ma'lumoti VPN/proxy/datacenter belgisini o'z ichiga oladimi."""
    if not geo:
        return False
    return bool(geo.get("proxy") or geo.get("hosting"))


def geo_for_ip(ip):
    """IP uchun joylashuv (cache bilan). Topilmasa None."""
    if not ip:
        return None
    key = f"{_GEO_CACHE_PREFIX}{ip}"
    hit = cache.get(key)
    if hit is not None:
        return hit or None  # "yo'q" degan ma'no ham cache qilinadi
    geo = _fetch_geo(ip)
    cache.set(key, geo or "", timeout=_GEO_TTL)
    return geo


def format_geo(geo):
    """Geo lug'atidan odam o'qiydigan satr: "O'zbekiston, Farg'ona (Uztelecom)"."""
    if not geo:
        return ""
    parts = [p for p in (geo.get("city"), geo.get("region"), geo.get("country")) if p]
    line = ", ".join(parts)
    if geo.get("isp"):
        line += f" ({geo['isp']})"
    return line[:200]


def client_meta(request):
    """So'rovdan IP, qurilma va joylashuv ma'lumotlarini yig'adi.

    Lead yaratishda ishlatiladi: spam tahlili + menejerga to'liq kontekst.
    """
    ip = _client_ip(request)
    user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:300]
    return {
        "ip": ip,
        "user_agent": user_agent,
        "geo": geo_for_ip(ip),
    }
