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

    def _post(self, payload=None):
        body = self.payload if payload is None else payload
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
        )

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
