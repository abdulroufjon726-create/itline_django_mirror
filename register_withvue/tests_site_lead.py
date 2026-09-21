"""Landing sayt shakllari uchun testlar (/api/site-lead/).

Saytdagi "Ro'yxatdan o'tish" va "Need Support" shakllari shu endpointga
yoziladi. Token talab qilinmaydi, lekin rate-limit va validatsiya bor.
"""

import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase

from .models import Lead


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
        """Haqiqiy captcha oladi va to'g'ri javobini qaytaradi."""
        import re

        res = self.client.get("/api/captcha/new/")
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
