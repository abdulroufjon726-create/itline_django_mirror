"""VPN blokirovka va xavfsiz xato qaytarish testlari.

VpnBlockMiddleware: VPN/proxy/datacenter IP BUTUN API'ga kirishi
bloklanadi (faqat webhook/ping mustasno, CORS preflight ham o'tadi).
safe_error: ichki istisno tafsilotlari (bazaviy xabar, fayl yo'llari)
foydalanuvchiga sizilmaydi — faqat logga yoziladi.
"""
import json
from unittest.mock import patch

from django.core.cache import caches
from django.test import Client, TestCase

GEO_CLEAN = {
    "country": "Uzbekistan",
    "city": "Tashkent",
    "region": "Tashkent",
    "isp": "Uzbektelekom",
    "proxy": False,
    "hosting": False,
    "mobile": False,
}
GEO_VPN = {
    "country": "Netherlands",
    "city": "Amsterdam",
    "region": "North Holland",
    "isp": "NordVPN",
    "proxy": True,
    "hosting": True,
    "mobile": False,
}


def _clear_caches():
    caches["captcha"].clear()
    caches["default"].clear()


class VpnBlockMiddlewareTest(TestCase):
    """VPN IP butun API'ga bloklangan, infratuzilma endpointlari ochiq."""

    def setUp(self):
        _clear_caches()
        self.vpn_client = Client(HTTP_X_FORWARDED_FOR="45.134.20.100")
        self.clean_client = Client(HTTP_X_FORWARDED_FOR="203.0.113.10")

    def _with_geo(self, geo):
        return patch("register_withvue.geo.geo_for_ip", return_value=geo)

    def test_vpn_blocked_on_get(self):
        with self._with_geo(GEO_VPN):
            r = self.vpn_client.get("/api/captcha/new/")
        self.assertEqual(r.status_code, 403)
        self.assertTrue(json.loads(r.content).get("vpn_blocked"))

    def test_vpn_blocked_on_post(self):
        with self._with_geo(GEO_VPN):
            r = self.vpn_client.post(
                "/api/site-lead/",
                data=json.dumps({"name": "X", "phone": "901234567"}),
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 403)

    def test_vpn_blocked_on_manager_login_path(self):
        with self._with_geo(GEO_VPN):
            r = self.vpn_client.get("/api/manager/login/")
        self.assertEqual(r.status_code, 403)

    def test_ping_exempt(self):
        with self._with_geo(GEO_VPN):
            r = self.vpn_client.get("/api/ping/")
        self.assertEqual(r.status_code, 200)

    def test_webhook_exempt_from_vpn_block(self):
        # Webhook VPN blokida emas — Telegram serveri datacenter IP'dan keladi.
        # Secret tekshiruvi ham 403 qaytaradi, shuning uchun VPN blok belgisi
        # (vpn_blocked=True) YO'QLIGINI tekshiramiz.
        with self._with_geo(GEO_VPN):
            r = self.vpn_client.post(
                "/api/tg/webhook/",
                data="{}",
                content_type="application/json",
            )
        try:
            body = json.loads(r.content)
        except (ValueError, TypeError):
            body = {}
        self.assertFalse(body.get("vpn_blocked", False))

    def test_cors_preflight_exempt(self):
        with self._with_geo(GEO_VPN):
            r = self.vpn_client.options(
                "/api/site-lead/",
                HTTP_ORIGIN="https://excellences-school.vercel.app",
                HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            )
        self.assertEqual(r.status_code, 200)

    def test_clean_ip_passes(self):
        with self._with_geo(GEO_CLEAN):
            r = self.clean_client.get("/api/captcha/new/")
        self.assertEqual(r.status_code, 200)

    def test_geo_service_down_fails_open(self):
        # Geo xizmat javob bermasa — so'rov o'tadi (sayt o'chib qolmasin).
        with patch("register_withvue.geo.geo_for_ip", return_value=None):
            r = self.clean_client.get("/api/captcha/new/")
        self.assertEqual(r.status_code, 200)

    def test_verdict_cached(self):
        with self._with_geo(GEO_VPN) as mocked:
            self.vpn_client.get("/api/captcha/new/")
            self.vpn_client.get("/api/captcha/new/")
        # 2 so'rovda geo 1 marta chaqiriladi (2-chisi cache'dan)
        self.assertEqual(mocked.call_count, 1)


class CloudflareClientIpTest(TestCase):
    """Render Cloudflare ortida: XFF'dagi oxirgi ommaviy yozuv ba'zan CF
    edge IP bo'lib qoladi — u "hosting" deb baholanib TOZA mijozlarga
    soxta 403 bergan. Endi CF-Connecting-IP ishlatiladi."""

    def setUp(self):
        _clear_caches()

    def _with_geo(self, geo):
        return patch("register_withvue.geo.geo_for_ip", return_value=geo)

    def test_cf_edge_in_chain_uses_cf_connecting_ip(self):
        # Real prod zanjiri: mijoz -> CF edge -> Render ichki (10.x)
        client = Client(
            HTTP_X_FORWARDED_FOR="144.124.196.104, 172.71.150.24, 10.238.26.158",
            HTTP_CF_CONNECTING_IP="144.124.196.104",
            REMOTE_ADDR="10.238.26.158",
        )
        with patch("register_withvue.geo.geo_for_ip", return_value=GEO_CLEAN) as mocked:
            r = client.get("/api/captcha/new/")
        self.assertEqual(r.status_code, 200)
        # geo TOZA mijoz IP'si bilan so'ralgan (CF edge EMAS)
        self.assertEqual(mocked.call_args[0][0], "144.124.196.104")

    def test_cf_edge_vpn_client_still_blocked(self):
        # CF orqali kelgan VPN foydalanuvchi baribir bloklanadi
        client = Client(
            HTTP_X_FORWARDED_FOR="45.134.20.100, 172.71.150.24, 10.238.26.158",
            HTTP_CF_CONNECTING_IP="45.134.20.100",
            REMOTE_ADDR="10.238.26.158",
        )
        with self._with_geo(GEO_VPN):
            r = client.get("/api/captcha/new/")
        self.assertEqual(r.status_code, 403)

    def test_cf_spoofed_header_without_cf_edge_is_ignored(self):
        # Hujumchi o'zi CF-Connecting-IP yubordi, lekin zanjirda CF edge
        # yo'q — sarlavha e'tiborsiz, oxirgi ommaviy XFF yozuvi olinadi
        client = Client(
            HTTP_X_FORWARDED_FOR="144.124.196.104, 10.0.0.1",
            HTTP_CF_CONNECTING_IP="8.8.8.8",
            REMOTE_ADDR="10.0.0.1",
        )
        with patch("register_withvue.geo.geo_for_ip", return_value=GEO_CLEAN) as mocked:
            client.get("/api/captcha/new/")
        self.assertEqual(mocked.call_args[0][0], "144.124.196.104")

    def test_cf_edge_without_header_falls_back_to_edge(self):
        # CF sarlavhasi yo'q (mumkin bo'lgan proxy zanjiri) — eski xulq:
        # edge IP qayadi (false positive mumkin, lekin himoya saqlanadi)
        from register_withvue.ratelimit import _client_ip

        client = Client(
            HTTP_X_FORWARDED_FOR="144.124.196.104, 172.71.150.24, 10.238.26.158",
            REMOTE_ADDR="10.238.26.158",
        )
        self.assertEqual(_client_ip(client.get("/api/captcha/new/").wsgi_request), "172.71.150.24")

    def test_ipv6_cf_edge_recognized(self):
        # Cloudflare IPv6 edge — ro'yxatga kiritilgan, mijoz IPv6'si olinadi
        from register_withvue.ratelimit import _client_ip

        client = Client(
            HTTP_X_FORWARDED_FOR="2a02:2168:100::1, 2606:4700:3033::1, 10.0.0.1",
            HTTP_CF_CONNECTING_IP="2a02:2168:100::1",
            REMOTE_ADDR="10.0.0.1",
        )
        self.assertEqual(_client_ip(client.get("/api/captcha/new/").wsgi_request), "2a02:2168:100::1")


class SafeErrorTest(TestCase):
    """Ichki istisno matni foydalanuvchiga sizilmaydi."""

    def test_value_error_passes_through(self):
        from register_withvue.errors import safe_error

        e = ValueError("Telefon raqam noto'g'ri")
        self.assertEqual(safe_error(e), "Telefon raqam noto'g'ri")

    def test_internal_exception_masked(self):
        from register_withvue.errors import safe_error

        e = Exception('column "secret_col" of relation "auth_user" does not exist')
        msg = safe_error(e)
        self.assertNotIn("secret_col", msg)
        self.assertNotIn("auth_user", msg)
        self.assertEqual(msg, "Xatolik yuz berdi. Qayta urinib ko'ring.")

    def test_app_exception_passes_through(self):
        from register_withvue.errors import safe_error

        class RangeError(Exception):
            pass

        RangeError.__module__ = "register_withvue.views"
        e = RangeError("Sana oralig'i noto'g'ri")
        self.assertEqual(safe_error(e), "Sana oralig'i noto'g'ri")

    def test_database_error_masked(self):
        from django.db import DatabaseError
        from register_withvue.errors import safe_error

        e = DatabaseError("FATAL: password authentication failed for user")
        msg = safe_error(e)
        self.assertNotIn("password", msg)
