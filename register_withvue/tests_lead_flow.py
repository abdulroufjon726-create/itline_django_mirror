"""Sayt leadlari tugma oqimi (Bog'lanish/Qabul/Bekor) + create_student testlari."""

import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase

from . import telegram as tg
from .models import Lead, Manager, Student, TelegramSubscriber


class LeadCallbackTests(TestCase):
    def setUp(self):
        cache.clear()
        self.lead = Lead.objects.create(
            name="Aziza Rahimova",
            phone="+998 90 123 45 67",
            interest="Ingliz tili",
            note="Sinov darsi",
        )
        self.manager = Manager.objects.create(
            name="Direktor", phone="90 111 22 33", password="x"
        )
        self.sub = TelegramSubscriber.objects.create(
            chat_id=8778538958, role="manager", manager=self.manager, phone="901112233"
        )

    def _callback(self, action, sender=8778538958):
        return {
            "id": "cb1",
            "from": {"id": sender},
            "data": f"lead:{self.lead.id}:{action}",
            "message": {"chat": {"id": 8778538958}, "message_id": 42},
        }

    @patch.object(tg, "tg_call")
    def test_contact_then_accept_then_reject_flow(self, api):
        # javob: answerCallbackQuery OK, editMessageText OK, sendContact OK
        api.return_value = {"ok": True}

        tg.handle_update({"callback_query": self._callback("contact")})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "contacted")
        # editMessageText chaqirilgan — markupda Qabul/Bekor tugmalari bor
        edit = [c for c in api.call_args_list if c[0][0] == "editMessageText"]
        self.assertTrue(edit)
        kb = edit[-1][0][1]["reply_markup"]
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("✅ Qabul qilish", texts)
        self.assertIn("❌ Bekor qilish", texts)
        # Bir bosishda qo'ng'iroq tugmasi (PUBLIC_BASE_URL sozlangan)
        self.assertIn("📞 Qo'ng'iroq qilish", texts)
        url_btns = [
            b for row in kb["inline_keyboard"] for b in row if "url" in b
        ]
        self.assertTrue(url_btns, "tel: redirect tugmasi yo'q")
        self.assertIn("/api/call/", url_btns[-1]["url"])

        tg.handle_update({"callback_query": self._callback("accept")})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "accepted")

        tg.handle_update({"callback_query": self._callback("reject")})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "rejected")

    @patch.object(tg, "tg_call")
    def test_non_manager_cannot_press_buttons(self, api):
        TelegramSubscriber.objects.filter(chat_id=8778538958).update(role="student")
        tg.handle_update({"callback_query": self._callback("contact")})
        self.lead.refresh_from_db()
        self.assertNotEqual(self.lead.status, "contacted", "begona odam holatni o'zgartirdi")

    @patch.object(tg, "tg_call")
    def test_keyboard_per_status(self, api):
        kb = tg.lead_keyboard(self.lead)
        self.assertEqual(kb["inline_keyboard"][0][0]["text"], "📞 Bog'lanish")

        self.lead.status = "contacted"
        kb = tg.lead_keyboard(self.lead)
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("✅ Qabul qilish", texts)

        # PANEL_BASE_URL bo'sh — accepted holatda tugma bo'lmaydi
        self.lead.status = "accepted"
        with patch.object(tg.settings, "PANEL_BASE_URL", ""):
            self.assertIsNone(tg.lead_keyboard(self.lead))

        # PANEL_BASE_URL bor — "Bazaga qo'shish" URL tugmasi
        with patch.object(tg.settings, "PANEL_BASE_URL", "https://panel.example.com"):
            kb = tg.lead_keyboard(self.lead)
        btn = kb["inline_keyboard"][0][0]
        self.assertEqual(btn["text"], "➕ Bazaga qo'shish")
        self.assertIn("add-student?", btn["url"])
        self.assertIn("name=Aziza", btn["url"])

        self.lead.status = "rejected"
        self.assertIsNone(tg.lead_keyboard(self.lead))


class CreateStudentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.manager = Manager.objects.create(
            name="Direktor", phone="90 111 22 33", password="x"
        )
        self.client = Client()

    def _login(self):
        # _perm_any_or_admin JWT token kutadi — to'g'ridan-to'g'ri chaqiruv
        # uchun request factory bilan emas, panelda ishlatilgan login oqimi
        # orqali token olinadi. Bu testda faqat validatsiya yo'llari
        # tekshiriladi (token yo'q — 401/403), aks holda login mexanizmini
        # takrorlash kerak bo'lardi.
        pass

    def test_requires_auth(self):
        res = self.client.post(
            "/api/students/create/",
            data=json.dumps({"name": "Ali", "phone": "901234567"}),
            content_type="application/json",
        )
        self.assertIn(res.status_code, (401, 403))
