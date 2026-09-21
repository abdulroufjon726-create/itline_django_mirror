"""User-Agent satridan qurilma ma'lumotini aniqlash.

Tashqi kutubxonasiz (user-agents paketisiz) yengil regex tahlili:
menejur Telegram xabarida "iPhone · Safari · iOS 17" ko'rinishida ko'radi.
Aniqlanmasa "Noma'lum qurilma" qaytariladi — hech qachon yiqilmaydi.
"""
import re

# ── Qurilma turi ──
_DEVICE_PATTERNS = (
    (re.compile(r"iPad|Tablet|PlayBook|Silk", re.I), "Tablet"),
    (re.compile(r"Mobi|iPhone|Android.*Mobile|Windows Phone", re.I), "Telefon"),
    (re.compile(r"TV|SmartTV|AppleTV", re.I), "TV"),
    (re.compile(r"bot|crawler|spider|curl|wget|python-requests|okhttp", re.I), "Bot"),
)

# ── Operating tizimi ──
_OS_PATTERNS = (
    (re.compile(r"Windows NT 10\.0|Windows NT 11\.0", re.I), "Windows 10/11"),
    (re.compile(r"Windows NT 6\.3", re.I), "Windows 8.1"),
    (re.compile(r"Windows NT 6\.1", re.I), "Windows 7"),
    (re.compile(r"Windows", re.I), "Windows"),
    (re.compile(r"iPhone", re.I), "iOS (iPhone)"),
    (re.compile(r"iPad", re.I), "iPadOS"),
    (re.compile(r"Android (\d+[\d.]*)", re.I), None),  # versiya bilan — maxsus
    (re.compile(r"Android", re.I), "Android"),
    (re.compile(r"Mac OS X (\d+[_\d]*)", re.I), None),  # versiya bilan — maxsus
    (re.compile(r"Macintosh|Mac OS", re.I), "macOS"),
    (re.compile(r"CrOS", re.I), "ChromeOS"),
    (re.compile(r"Linux", re.I), "Linux"),
)

# ── Brauzer ──
# Tartib muhim: eng maxsus birinchi (masalan Edge ichida Chrome ham yozilgan)
_BROWSER_PATTERNS = (
    ("Edge", re.compile(r"Edg(?:e|A|iOS)?/(\d+)")),
    ("Opera", re.compile(r"OPR/(\d+)|Opera[/ ](\d+)")),
    ("Samsung Internet", re.compile(r"SamsungBrowser/(\d+)")),
    ("Yandex Browser", re.compile(r"YaBrowser/(\d+)")),
    ("Mi Browser", re.compile(r"MiuiBrowser/(\d+)")),
    ("Firefox", re.compile(r"Firefox/(\d+)")),
    ("Chrome", re.compile(r"Chrome/(\d+)")),
    ("Safari", re.compile(r"Version/(\d+).*Safari")),
    ("Safari", re.compile(r"Safari")),
    ("Telegram", re.compile(r"Telegram(?:Bot)?")),
)


def parse_device(user_agent):
    """User-Agent dan (tur, os, brauzer) lug'atini qaytaradi.

    Qaytaradigan: {"type": "Telefon", "os": "iOS (iPhone)", "browser": "Safari 17"}
    Hech narsa topilmasa "Noma'lum" qiymatlar — hech qachon xato bermaydi.
    """
    ua = (user_agent or "").strip()
    if not ua:
        return {"type": "Noma'lum", "os": "Noma'lum", "browser": "Noma'lum"}

    device_type = "Kompyuter"
    for pattern, label in _DEVICE_PATTERNS:
        if pattern.search(ua):
            device_type = label
            break

    os_name = "Noma'lum"
    for pattern, label in _OS_PATTERNS:
        m = pattern.search(ua)
        if m:
            if label is None and pattern.pattern.startswith("Android"):
                os_name = f"Android {m.group(1).replace('_', '.')}"
            elif label is None and pattern.pattern.startswith("Mac OS X"):
                os_name = "macOS " + m.group(1).replace("_", ".")[:4]
            else:
                os_name = label or pattern.pattern.split("\\")[0]
            break

    browser = "Noma'lum"
    for name, pattern in _BROWSER_PATTERNS:
        m = pattern.search(ua)
        if m:
            # birinchi raqamli guruh — versiya (bo'lsa)
            version = next((g for g in m.groups() if g), None)
            browser = f"{name} {version}" if version else name
            break

    return {"type": device_type, "os": os_name, "browser": browser}


def format_device(device):
    """Qurilma lug'atidan odam o'qiydigan satr: \"Telefon · Android 13 · Chrome 120\"."""
    if not device:
        return ""
    parts = [
        device.get("type") or "",
        device.get("os") or "",
        device.get("browser") or "",
    ]
    return " · ".join(p for p in parts if p and p != "Noma'lum")
