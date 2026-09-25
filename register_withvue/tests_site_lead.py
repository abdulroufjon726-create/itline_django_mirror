"""Landing sayt shakllari uchun testlar (/api/site-lead/).

Saytdagi "Ro'yxatdan o'tish" va "Need Support" shakllari shu endpointga
yoziladi. Token talab qilinmaydi, lekin rate-limit va validatsiya bor.
"""

import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase

from .models import Lead


def solve_captcha(client):
    """Haqiqiy captcha oladi va to'g'ri javobini qaytaradi (test uchun)."""
    import re

    res = client.get("/api/captcha/new/")
    data = json.loads(res.content)
    a, b = re.findall(r"\d+", data["question"])
    # captcha moduli juda tez yuborilganini rad etadi — 2s kutmaslik
    # uchun cache'dagi created ni biroz orqaga suramiz (captcha
    # alohida "captcha" cache'ida turadi — settings.CACHES)
    from django.core.cache import caches

    _c = caches["captcha"]
    key = f"captcha:{data['id']}"
    entry = _c.get(key)
    entry["created"] -= 10
    _c.set(key, entry, timeout=600)
    return {
        "captcha_id": data["id"],
        "captcha_answer": int(a) + int(b) if "+" in data["question"] else int(a) - int(b),
    }


class SiteLeadTests(TestCase):
    def setUp(self):
        super().setUp()
        # Rate-limit locmem cache'da saqlanadi va testlar orasida yashaydi —
        # har bir test toza hisob bilan boshlansin
        cache.clear()
        self.client = Client()
        self.url = "/api/site-lead/"
        self.payload = {
            "name": "Aziza Rahimova",
            "phone": "+998 90 123 45 67",
            "interest": "Ingliz tili - IELTS",
            "note": "Sinov darsiga yozilmoqchiman",
            "source": "website",
        }

    def _post(self, payload=None, with_captcha=True):
        body = self.payload if payload is None else payload
        if with_captcha:
            body = {**body, **self._solve_captcha()}
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
        )

    def _solve_captcha(self):
        return solve_captcha(self.client)

    @patch("register_withvue.telegram.notify_managers_lead")
    def test_creates_lead_and_notifies(self, notify):
        res = self._post()
        self.assertEqual(res.status_code, 201)

        lead = Lead.objects.get()
        self.assertEqual(lead.name, "Aziza Rahimova")
        self.assertEqual(lead.interest, "Ingliz tili - IELTS")
        self.assertEqual(lead.source, "website")

        # Menejerga Telegram xabari boradi (lead obyekti bilan)
        notify.assert_called_once()
        self.assertEqual(notify.call_args[0][0], lead)

    @patch("register_withvue.telegram.notify_managers_lead")
    def test_invalid_phone_rejected(self, notify):
        res = self._post({**self.payload, "phone": "abc"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Lead.objects.count(), 0)
        notify.assert_not_called()

    @patch("register_withvue.telegram.notify_managers_lead")
    def test_missing_phone_rejected(self, notify):
        res = self._post({"name": "Ali", "phone": ""})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Lead.objects.count(), 0)

    @patch("register_withvue.telegram.notify_managers_lead")
    def test_empty_name_gets_placeholder(self, notify):
        res = self._post({"name": "", "phone": "+998901234567"})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Lead.objects.get().name, "Ism ko'rsatilmagan")

    @patch("register_withvue.telegram.notify_managers_lead")
    def test_source_defaults_to_website(self, notify):
        payload = {k: v for k, v in self.payload.items() if k != "source"}
        res = self._post(payload)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Lead.objects.get().source, "website")

    def test_get_method_not_allowed(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 405)

    @patch("register_withvue.telegram.notify_managers_lead")
    def test_rate_limited_after_ten(self, notify):
        for _ in range(10):
            res = self._post()
            self.assertEqual(res.status_code, 201)
        res = self._post()
        self.assertEqual(res.status_code, 429)
        self.assertEqual(Lead.objects.count(), 10)

    # ── Captcha (men robot emasman) ──

    @patch("register_withvue.telegram.notify_managers_lead")
    def test_captcha_required(self, notify):
        """Captcha'siz murojaat qabul qilinmaydi."""
        res = self._post(with_captcha=False)
        self.assertEqual(res.status_code, 400)
        self.assertTrue(json.loads(res.content)["captcha_failed"])
        self.assertEqual(Lead.objects.count(), 0)

    @patch("register_withvue.telegram.notify_managers_lead")
    def test_captcha_wrong_answer_rejected(self, notify):
        res = self.client.get("/api/captcha/new/")
        data = json.loads(res.content)
        res = self._post(
            {**self.payload, "captcha_id": data["id"], "captcha_answer": 9999},
            with_captcha=False,
        )
        self.assertEqual(res.status_code, 400)
        self.assertTrue(json.loads(res.content)["captcha_failed"])
        self.assertEqual(Lead.objects.count(), 0)

    @patch("register_withvue.telegram.notify_managers_lead")
    def test_captcha_single_use(self, notify):
        """Bir captcha faqat bir marta ishlaydi — ikkinchi marta rad etiladi."""
        solved = self._solve_captcha()
        res = self._post({**self.payload, **solved}, with_captcha=False)
        self.assertEqual(res.status_code, 201)
        res = self._post({**self.payload, **solved}, with_captcha=False)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Lead.objects.count(), 1)

    @patch("register_withvue.telegram.notify_managers_lead")
    def test_lead_records_ip_and_geo(self, notify):
        """IP va joylashuv ma'lumoti lead bilan saqlanadi."""
        res = self._post()
        self.assertEqual(res.status_code, 201)
        lead = Lead.objects.get()
        self.assertEqual(lead.ip_address, "127.0.0.1")  # test client IP
        # geo tashqi xizmat testda ishlamasligi mumkin — IP esa doim yoziladi


class SiteLeadGpsTests(TestCase):
    """Brauzer GPS koordinatalari bilan joylashuv aniqligi.

    Landing navigator.geolocation ruxsat berganda aniq nuqta keladi;
    backend teskari geokodlash (BigDataCloud -> Nominatim) bilan haqiqiy
    shahar nomini oladi. ip-api Uztelecom IP'larini noto'g'ri Toshkentga
    bog'lashi muammosi shu bilan yechiladi.
    """

    QOQON = {"city": "Qo'qon", "region": "Farg'ona viloyati", "country": "O'zbekiston"}

    def setUp(self):
        super().setUp()
        cache.clear()
        self.client = Client()
        self.url = "/api/site-lead/"
        self.payload = {
            "name": "Test GPS Mijoz",
            "phone": "+998 90 555 55 55",
            "source": "website",
        }

    def _post(self, **extra):
        body = {**self.payload, **extra, **solve_captcha(self.client)}
        return self.client.post(
            self.url, data=json.dumps(body), content_type="application/json"
        )

    @patch("register_withvue.telegram.notify_managers_lead")
    @patch("register_withvue.geo._reverse_geocode_bdc", return_value=QOQON)
    def test_gps_lead_gets_real_city(self, bdc, notify):
        """GPS koordinata -> haqiqiy shahar nomi (Qo'qon test nuqtasi)."""
        res = self._post(gps_lat=40.5286, gps_lon=70.9425)
        self.assertEqual(res.status_code, 201)
        lead = Lead.objects.get()
        self.assertIn("Qo'qon", lead.geo_info)
        self.assertIn("GPS", lead.geo_info)
        self.assertEqual(lead.geo_lat, 40.5286)
        self.assertEqual(lead.geo_lon, 70.9425)

    @patch("register_withvue.telegram.notify_managers_lead")
    def test_gps_out_of_range_falls_back_to_ip(self, notify):
        """Diapazondan tashqari GPS (999) rad etiladi — IP taxmini qoladi."""
        res = self._post(gps_lat=999.0, gps_lon=70.9425)
        self.assertEqual(res.status_code, 201)
        lead = Lead.objects.get()
        self.assertNotIn("GPS", lead.geo_info)  # 127.0.0.1 -> "Lokal tarmoq"
        self.assertIsNone(lead.geo_lat)

    @patch("register_withvue.telegram.notify_managers_lead")
    def test_gps_non_numeric_falls_back_to_ip(self, notify):
        """Son bo'lmagan GPS qiymati jim rad etiladi, xato qaytmaydi."""
        res = self._post(gps_lat="abc", gps_lon=None)
        self.assertEqual(res.status_code, 201)
        lead = Lead.objects.get()
        self.assertNotIn("GPS", lead.geo_info)
        self.assertIsNone(lead.geo_lat)

    @patch("register_withvue.telegram.notify_managers_lead")
    @patch("register_withvue.geo._reverse_geocode_nominatim", return_value=QOQON)
    @patch("register_withvue.geo._reverse_geocode_bdc", return_value=None)
    def test_reverse_geocode_falls_back_to_nominatim(self, bdc, nom, notify):
        """BigDataCloud javob bermasa Nominatim ishlaydi."""
        res = self._post(gps_lat=40.5286, gps_lon=70.9425)
        self.assertEqual(res.status_code, 201)
        lead = Lead.objects.get()
        self.assertIn("Qo'qon", lead.geo_info)
        self.assertEqual(lead.geo_lat, 40.5286)

    @patch("register_withvue.telegram.notify_managers_lead")
    @patch("register_withvue.geo._reverse_geocode_nominatim", return_value=None)
    @patch("register_withvue.geo._reverse_geocode_bdc", return_value=None)
    def test_reverse_geocode_all_fail_keeps_exact_coords(self, bdc, nom, notify):
        """Providerlar javob bermasa ham aniq koordinata saqlanadi."""
        res = self._post(gps_lat=40.5286, gps_lon=70.9425)
        self.assertEqual(res.status_code, 201)
        lead = Lead.objects.get()
        self.assertIn("40.5286", lead.geo_info)
        self.assertIn("70.9425", lead.geo_info)
        self.assertIn("GPS", lead.geo_info)
        self.assertEqual(lead.geo_lat, 40.5286)

    @patch("register_withvue.telegram.notify_managers_lead")
    def test_no_gps_keeps_ip_geo_behavior(self, notify):
        """GPS yuborilmasa eski IP-geo xulqi o'zgarilmagan."""
        res = self._post()
        self.assertEqual(res.status_code, 201)
        lead = Lead.objects.get()
        self.assertEqual(lead.ip_address, "127.0.0.1")
        self.assertNotIn("GPS", lead.geo_info)
        self.assertIsNone(lead.geo_lat)
