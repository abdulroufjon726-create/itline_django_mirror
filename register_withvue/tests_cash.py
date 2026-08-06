"""Kunlik umumiy kassa + tranzaksiya jurnali testlari.

Kassa bitta va har kunga bitta smena (umumiy account). Asosiy maqsad —
foydalanuvchi tasvirlagan xatoni yopish: to'lovni ertasi kuni qayta
yozish (200k -> 400k) kechagi topshirilgan kunni buzmasligi, balki
ertangi kun kassasiga +200k bo'lib tushishi kerak.
"""

from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.test.client import RequestFactory

from . import views
from .models import (
    Manager,
    Student,
    Payment,
    CashSession,
    CashEntry,
    CashRegisterSettings,
)


def _on(day):
    """views.tashkent_today() ni berilgan kunga qotiradi."""
    return patch("register_withvue.views.tashkent_today", return_value=day)


class CashRegisterTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.mgr = Manager.objects.create(
            name="Kassir",
            surname="Test",
            phone="+998900000001",
            password="x",
            permissions=["cash.view", "cash.close"],
        )
        self.student = Student.objects.create(
            name="Ali", surname="Vali", phone="+998900000002", stage=1
        )
        self.payment = Payment.objects.create(
            student=self.student, month="2026-08", stage=1, amount_due=400000
        )
        CashRegisterSettings.get_settings()  # enabled=True (default)

    def _req(self, body="{}"):
        return self.rf.post(
            "/x",
            data=body,
            content_type="application/json",
            HTTP_X_USER_PHONE=self.mgr.phone,
        )

    def test_cross_day_history_preserved(self):
        """Foydalanuvchi ssenariysi — kechagi kun buzilmaydi."""
        # 1-kun: 0 -> 200 000, topshiriladi
        with _on(date(2026, 8, 1)):
            self.payment.paid_amount = 200000
            self.payment.save()
            views.record_cash_delta(self._req(), self.payment, 0, 200000)
            resp = views.close_cash_session(self._req('{"counted_total": 200000}'))
            self.assertEqual(resp.status_code, 200)

        s1 = CashSession.objects.get(date=date(2026, 8, 1))
        self.assertEqual(s1.status, CashSession.STATUS_CLOSED)
        self.assertEqual(s1.expected_total, 200000)

        # 2-kun: kassir 200 000 ni o'chirib 400 000 yozadi
        with _on(date(2026, 8, 2)):
            self.payment.paid_amount = 400000
            self.payment.save()
            views.record_cash_delta(self._req(), self.payment, 200000, 400000)

        s2 = CashSession.objects.get(date=date(2026, 8, 2))
        self.assertEqual(s2.live_total(), 200000)  # faqat 2-kun +200k

        # 1-kun tarixi buzilmagan
        s1.refresh_from_db()
        self.assertEqual(s1.expected_total, 200000)
        self.assertEqual(CashSession.objects.count(), 2)  # har kunga bitta

        # Jurnal jami = to'lov jami
        total = sum(e.amount for e in CashEntry.objects.all())
        self.assertEqual(total, 400000)

    def test_reopen_same_day_after_close(self):
        """Topshirilgan kunga yana pul tushsa smena qayta ochiladi (bitta qatorda)."""
        with _on(date(2026, 8, 3)):
            views.record_cash_delta(self._req(), self.payment, 0, 100000)
            resp = views.close_cash_session(self._req('{"counted_total": 100000}'))
            self.assertEqual(resp.status_code, 200)
            s = CashSession.objects.get(date=date(2026, 8, 3))
            self.assertEqual(s.status, CashSession.STATUS_CLOSED)

            # O'sha kuni yana pul tushdi
            views.record_cash_delta(self._req(), self.payment, 100000, 150000)
            s.refresh_from_db()
            self.assertEqual(s.status, CashSession.STATUS_OPEN)  # qayta ochildi
            self.assertEqual(s.live_total(), 150000)
            self.assertEqual(CashSession.objects.filter(date=date(2026, 8, 3)).count(), 1)

    def test_shortage_detected(self):
        """Kam sanalgan pul kamomad (manfiy farq) sifatida ko'rinadi."""
        with _on(date(2026, 8, 4)):
            views.record_cash_delta(self._req(), self.payment, 0, 300000)
            resp = views.close_cash_session(self._req('{"counted_total": 250000}'))
            self.assertEqual(resp.status_code, 200)
        s = CashSession.objects.get(date=date(2026, 8, 4))
        self.assertEqual(s.expected_total, 300000)
        self.assertEqual(s.counted_total, 250000)
        self.assertEqual(s.difference, -50000)

    def test_require_counted_blocks_empty_close(self):
        """Sanoq majburiy bo'lganda bo'sh topshiruv rad etiladi."""
        with _on(date(2026, 8, 5)):
            views.record_cash_delta(self._req(), self.payment, 0, 100000)
            resp = views.close_cash_session(self._req("{}"))
            self.assertEqual(resp.status_code, 400)
        s = CashSession.objects.get(date=date(2026, 8, 5))
        self.assertIsNone(s.closed_at)

    def test_disabled_records_ledger_without_session(self):
        """Kassa o'chirilganda smena ochilmaydi, lekin jurnal yoziladi."""
        st = CashRegisterSettings.get_settings()
        st.enabled = False
        st.save()
        with _on(date(2026, 8, 6)):
            views.record_cash_delta(self._req(), self.payment, 0, 100000)
        self.assertEqual(CashSession.objects.count(), 0)
        self.assertEqual(CashEntry.objects.count(), 1)
        self.assertIsNone(CashEntry.objects.first().session_id)

    def test_zero_delta_no_entry(self):
        """Summa o'zgarmasa jurnalga yozuv qo'shilmaydi."""
        with _on(date(2026, 8, 7)):
            views.record_cash_delta(self._req(), self.payment, 200000, 200000)
        self.assertEqual(CashEntry.objects.count(), 0)
