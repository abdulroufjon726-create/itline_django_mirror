"""IP bo'yicha joylashuv va qurilma ma'lumotini aniqlash.

Landing'dan kelgan har bir murojaat qayerdan (mamlakat/shahar), qaysi
IP va qanday qurilma orqali yuborilgani aniqlanadi — spam tahlili va
menejerga to'liq ma'lumot uchun.

Bepul xizmatlar: ip-api.com (asosiy) → ipwho.is (zaxira). Kalit
talab qilmaydi. Natija 24 soat cache'da — bir IP uchun faqat bir marta
tashqi so'rov. Birinchisi javob bermasa ikkinchisi urinadi; ikkalasi ham
javob bermasa faqat IP yoziladi (ish davom etadi).
"""
import json
import logging
import urllib.request

from django.core.cache import cache

from .device import parse_device
from .ratelimit import _client_ip

logger = logging.getLogger(__name__)

_GEO_CACHE_PREFIX = "geo:"
_GEO_TTL = 60 * 60 * 24  # 24 soat
_GEO_TIMEOUT = 3  # sekund — sayt sekinlashmasin


def _is_private_ip(ip):
    """Lokal/ichki tarmoq IP'mi (127.x, 10.x, 192.168.x, 172.16-31.x)."""
    import ipaddress

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_reserved


def _parse_ipapi(data):
    """ip-api.com javobini standart geo lug'atga aylantiradi."""
    if not data.get("query") and not data.get("country"):
        return None
    lat, lon = data.get("lat"), data.get("lon")
    try:
        lat = round(float(lat), 4) if lat is not None else None
        lon = round(float(lon), 4) if lon is not None else None
    except (TypeError, ValueError):
        lat = lon = None
    return {
        "country": str(data.get("country") or "")[:50],
        "city": str(data.get("city") or "")[:50],
        "region": str(data.get("regionName") or "")[:50],
        "isp": str(data.get("isp") or "")[:100],
        "lat": lat,
        "lon": lon,
        "proxy": bool(data.get("proxy")),
        "hosting": bool(data.get("hosting")),
        "mobile": bool(data.get("mobile")),
    }


def _fetch_geo_ipapi(ip):
    """Asosiy provider: ip-api.com (joylashuv + hosting/proxy belgisi)."""
    url = (
        f"http://ip-api.com/json/{ip}"
        "?fields=status,country,city,regionName,isp,query,proxy,hosting,mobile,lat,lon"
    )
    try:
        with urllib.request.urlopen(url, timeout=_GEO_TIMEOUT) as resp:
            return _parse_ipapi(json.loads(resp.read().decode("utf-8")))
    except Exception:  # noqa: BLE001 — tashqi xizmat muhim emas
        logger.debug("ip-api lookup failed for %s", ip, exc_info=True)
    return None


def _fetch_geo_ipwho(ip):
    """Zaxira provider: ipwho.is (ip-api o'chsa ishlaydi, 10k/oy bepul)."""
    url = f"https://ipwho.is/{ip}"
    try:
        with urllib.request.urlopen(url, timeout=_GEO_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("success"):
            return None
        lat, lon = data.get("latitude"), data.get("longitude")
        try:
            lat = round(float(lat), 4) if lat is not None else None
            lon = round(float(lon), 4) if lon is not None else None
        except (TypeError, ValueError):
            lat = lon = None
        conn = data.get("connection") or {}
        flag = data.get("flag") or {}
        return {
            "country": str(flag.get("country") or "")[:50],
            "city": str(data.get("city") or "")[:50],
            "region": str(data.get("region") or "")[:50],
            "isp": str(conn.get("isp") or conn.get("org") or "")[:100],
            "lat": lat,
            "lon": lon,
            # ipwho.is da proxy/hosting belgisi yo'q — pessimist emas,
            # False qoldiramiz (blok faqat ip-api verdictiga qaraydi)
            "proxy": False,
            "hosting": False,
            "mobile": bool(data.get("type") == "mobile"),
        }
    except Exception:  # noqa: BLE001
        logger.debug("ipwho.is lookup failed for %s", ip, exc_info=True)
    return None


def _local_network_geo(ip):
    """Lokal/ichki IP uchun barqaror natija — "Joylashuv chiqmadi" bo'lmasin.

    Lokal IP tashqi geo xizmatlar tomonidan topilmaydi (45x NoCity) —
    shuning uchun uni aniq belgilaymiz. Bu odatda dasturchi/dev muhit.
    """
    return {
        "country": "Lokal tarmoq",
        "city": "",
        "region": "",
        "isp": "",
        "lat": None,
        "lon": None,
        "proxy": False,
        "hosting": False,
        "mobile": False,
    }


def _fetch_geo(ip):
    """Joylashuvni oladi: asosiy provider → zaxira provider.

    Lokal IP uchun tashqi so'rov umuman qilinmaydi (mubolag'li natija).
    """
    if _is_private_ip(ip):
        return _local_network_geo(ip)
    return _fetch_geo_ipapi(ip) or _fetch_geo_ipwho(ip)


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
    if geo.get("gps"):
        # Brauzer GPS'dan kelgani ko'rinib tursin (IP taxminidan farqli).
        # "Lokal tarmoq" joylagichi bu yerda ma'nosiz — o'rniga aniq
        # koordinata ko'rsatiladi.
        parts = [p for p in parts if p != "Lokal tarmoq"]
        line = ", ".join(parts)
        if not line:
            line = f"{geo.get('lat')}, {geo.get('lon')}"
        return ("📍 GPS: " + line)[:200]
    line = ", ".join(parts)
    if geo.get("isp"):
        line += f" ({geo['isp']})"
    return line[:200] or "Joylashuv aniqlanmadi"


def maps_urls(geo):
    """Geo koordinatalaridan xarita havolalari (Google + Yandex).

    Koordinata yo'q bo'lsa shahar/mamlakat nomi bo'yicha qidiruv havolasi
    qaytariladi — menejur bir bosishda mijozi xaritada ko'radi.
    """
    if not geo:
        return {}
    lat, lon = geo.get("lat"), geo.get("lon")
    place = ", ".join(
        p for p in (geo.get("city"), geo.get("country")) if p
    )
    if lat is not None and lon is not None:
        return {
            "google": f"https://www.google.com/maps?q={lat},{lon}",
            "yandex": f"https://yandex.com/maps/?ll={lon}%2C{lat}&z=15&pt={lon},{lat}",
        }
    if place:
        from urllib.parse import quote

        q = quote(place)
        return {
            "google": f"https://www.google.com/maps/search/?api=1&query={q}",
            "yandex": f"https://yandex.com/maps/?text={q}",
        }
    return {}


def client_meta(request, gps=None):
    """So'rovdan IP, qurilma va joylashuv ma'lumotlarini yig'adi.

    Lead yaratishda ishlatiladi: spam tahlili + menejerga to'liq kontekst.

    gps — landing brauzerining aniq koordinatalari (lat, lon). Brauzer
    GPS ruxsat berganda IP'ga bog'langan shahar o'rniga aniq nuqta va
    uning haqiqiy shahar nomi ishlatiladi (ip-api Uztelecom IP'larini
    ko'pincha noto'g'ri Toshkentga bog'laydi).
    """
    ip = _client_ip(request)
    user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:300]
    geo = geo_for_ip(ip)
    gps = _valid_gps(gps[0], gps[1]) if gps else None
    if gps:
        place = reverse_geocode(gps[0], gps[1])
        base = geo or {}
        if place:
            geo = {
                **base,
                "country": place.get("country") or base.get("country") or "",
                "city": place.get("city") or "",
                "region": place.get("region") or "",
                "isp": base.get("isp") or "",
                "lat": gps[0],
                "lon": gps[1],
                "gps": True,
            }
        else:
            # Shahar nomi olinmadi — aniq koordinata baribir saqlansin
            geo = {**base, "lat": gps[0], "lon": gps[1], "gps": True}
    return {
        "ip": ip,
        "user_agent": user_agent,
        "device": parse_device(user_agent),
        "geo": geo,
    }


# --- Teskari geokodlash: GPS koordinata -> odam o'qiydigan joy nomi ---
# Bepul providerlar: BigDataCloud (asosiy, kalit talab qilmaydi) ->
# Nominatim/OpenStreetMap (zaxira). Natija 24 soat cache'da.

_REV_TTL = 60 * 60 * 24
_REV_UA = {"User-Agent": "Excellence-CRM-Lead/1.0"}


def _reverse_geocode_bdc(lat, lon):
    """BigDataCloud reverse-geocode-client: shahar/viloyat (kalitsiz)."""
    url = (
        "https://api.bigdatacloud.net/data/reverse-geocode-client"
        f"?latitude={lat}&longitude={lon}&localityLanguage=uz"
    )
    try:
        req = urllib.request.Request(url, headers=_REV_UA)
        with urllib.request.urlopen(req, timeout=_GEO_TIMEOUT) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        city = str(d.get("city") or d.get("locality") or "")[:50]
        region = str(d.get("principalSubdivision") or "")[:50]
        country = str(d.get("countryName") or "")[:50]
        if not (city or region or country):
            return None
        return {"city": city, "region": region, "country": country}
    except Exception:  # noqa: BLE001 — tashqi xizmat muhim emas
        logger.debug("BigDataCloud reverse failed (%s, %s)", lat, lon, exc_info=True)
    return None


def _reverse_geocode_nominatim(lat, lon):
    """Nominatim (OpenStreetMap): zaxira provider — shahar/tuman nomi."""
    url = (
        "https://nominatim.openstreetmap.org/reverse"
        f"?lat={lat}&lon={lon}&format=json&zoom=10&accept-language=uz"
    )
    try:
        req = urllib.request.Request(url, headers=_REV_UA)
        with urllib.request.urlopen(req, timeout=_GEO_TIMEOUT) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        addr = d.get("address") or {}
        city = str(addr.get("city") or addr.get("town") or addr.get("district") or "")[:50]
        region = str(addr.get("state") or addr.get("region") or "")[:50]
        country = str(addr.get("country") or "")[:50]
        if not (city or region or country):
            return None
        return {"city": city, "region": region, "country": country}
    except Exception:  # noqa: BLE001
        logger.debug("Nominatim reverse failed (%s, %s)", lat, lon, exc_info=True)
    return None


def _valid_gps(lat, lon):
    """GPS qiymatlarini tekshiradi: son va diapazonda — (lat, lon), aks holda None."""
    try:
        lat = round(float(lat), 4)
        lon = round(float(lon), 4)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return (lat, lon)


def reverse_geocode(lat, lon):
    """Koordinatadan joy nomi (cache bilan). Yo'q bo'lsa None."""
    gps = _valid_gps(lat, lon)
    if gps is None:
        return None
    lat, lon = gps
    key = f"{_GEO_CACHE_PREFIX}rev:{lat},{lon}"
    hit = cache.get(key)
    if hit is not None:
        return hit or None
    place = _reverse_geocode_bdc(lat, lon) or _reverse_geocode_nominatim(lat, lon)
    cache.set(key, place or "", timeout=_GEO_TTL)
    return place
