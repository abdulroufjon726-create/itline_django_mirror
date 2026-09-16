"""Serverni (Render free tier) har 60 sekundda so'roq bilan uyg'otib turadi.

Render free plan'dagi servis 15 daqiqa trafik bo'lmasa "uxlaydi" (spin down);
keyingi so'rov 30-50 sekund cold start bilan ochiladi. Bu skript har bir
daqiqada /api/ping/ ga yengil GET yuborib, servisni doim uyg'oq saqlaydi —
shu jumladan Telegram bot webhook'i ham bir xizmatda bo'lgani uchun bot ham
tezkor javob beradi.

Ishlatish:
    py tools/keep_alive.py                      # standart URL, har 60s
    py tools/keep_alive.py --interval 30        # har 30 sekundda
    py tools/keep_alive.py --url https://...    # boshqa server uchun

Windows'da fonda doimiy ishlashi uchun alohida terminalda ochib qo'yish
yoki Task Scheduler'ga qo'shish yetarli.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "https://davomat-django-zbn4.onrender.com/api/ping/"
# Render free spin-down ~15 daqiqa; 45s interval bilan xavfsiz qamrab olinadi.
DEFAULT_INTERVAL = 45.0
TIMEOUT = 30


def ping_once(url: str) -> tuple[bool, int | str]:
    """Bir marta so'rov yuboradi. (ok, status) qaytaradi."""
    req = urllib.request.Request(url, headers={"User-Agent": "keep-alive/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return True, resp.status
    except urllib.error.HTTPError as e:
        # 4xx/5xx ham server UYG'OQ degani (routing javob beradi) — lekin
        # 5xx ni alohida belgilaymiz, chunki suspend'da ham 503 keladi.
        return e.code < 500, e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return False, str(e.__cause__ or e)


def main() -> int:
    ap = argparse.ArgumentParser(description="Render serverini uyg'oq saqlaydi")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"ping URL (default: {DEFAULT_URL})")
    ap.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL,
        help=f"sekundlarda oraliq (default: {DEFAULT_INTERVAL})",
    )
    args = ap.parse_args()

    # Windows konsolida ham o'zbekcha/yozuv belgilari to'g'ri chiqishi uchun.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    print(f"keep-alive boshlandi: {args.url} har {args.interval:g}s da. To'xtatish: Ctrl+C")
    consecutive_failures = 0
    while True:
        now = _dt.datetime.now().strftime("%H:%M:%S")
        ok, status = ping_once(args.url)
        if ok:
            consecutive_failures = 0
            print(f"[{now}] OK  ({status})", flush=True)
        else:
            consecutive_failures += 1
            print(f"[{now}] XATO ({status}) — ketma-ket {consecutive_failures}", file=sys.stderr, flush=True)
        time.sleep(max(5.0, args.interval))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nto'xtatildi")
        sys.exit(0)
