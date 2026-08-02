"""Telegram bot oqimi uchun testlar (tuzatilgan xatolarni qo'riqlaydi)."""

from unittest.mock import patch

from django.test import TestCase

from . import telegram as tg
from .models import Student, TelegramSubscriber


class HandleUpdateTests(TestCase):
    def setUp(self):
        self.student = Student.objects.create(
            name="Ali", surname="Valiyev", phone="+998901234567"
        )

    def _contact_update(self, chat_id, phone, user_id=777):
        return {
            "message": {
                "chat": {"id": chat_id, "first_name": "Ali"},
                "from": {"id": user_id},
                "contact": {"phone_number": phone, "user_id": user_id},
            }
        }

    @patch.object(tg, "send_text")
    def test_contact_links_student(self, send_text):
        tg.handle_update(self._contact_update(555, "+998901234567"))
        sub = TelegramSubscriber.objects.get(chat_id=555)
        self.assertEqual(sub.student_id, self.student.id)
        # o'z raqamini tugma orqali yuborgan — login ma'lumoti ko'rsatiladi
        self.assertIn("Login", send_text.call_args[0][1])

    @patch.object(tg, "send_text")
    def test_unknown_number_keeps_existing_link(self, send_text):
        """Regressiya: bazada yo'q raqam ulanishni buzmasligi kerak."""
        tg.handle_update(self._contact_update(555, "+998901234567"))
        self.assertEqual(TelegramSubscriber.objects.get(chat_id=555).student_id,
                         self.student.id)

        # endi o'sha chat bazada yo'q raqam yuboradi
        tg.handle_update(self._contact_update(555, "+998900000000"))
        sub = TelegramSubscriber.objects.get(chat_id=555)
        self.assertEqual(sub.student_id, self.student.id, "ulanish o'chib ketdi")

    @patch.object(tg, "send_text")
    def test_foreign_number_does_not_leak_password(self, send_text):
        """Begonaning raqamini qo'lda yozish parolni ochmasligi kerak."""
        tg.handle_update(
            {
                "message": {
                    "chat": {"id": 999, "first_name": "Xaker"},
                    "from": {"id": 999},
                    "text": "998901234567",
                }
            }
        )
        body = send_text.call_args[0][1]
        self.assertNotIn("Parol", body)


class SendToStudentsTests(TestCase):
    @patch.object(tg, "send_text")
    def test_same_phone_and_phone2_sends_once(self, send_text):
        """Regressiya: phone == phone2 bo'lganda xabar 2 marta ketardi."""
        student = Student.objects.create(
            name="Ali", surname="Valiyev",
            phone="+998901234567", phone2="998901234567",
        )
        # student'ga bog'lanmagan, faqat telefon bo'yicha topiladigan obunachi
        TelegramSubscriber.objects.create(chat_id=321, phone="901234567")

        sent, failed, no_chat = tg.send_to_students([student], "Salom", "single")

        self.assertEqual(send_text.call_count, 1, "xabar takrorlandi")
        self.assertEqual((sent, failed, no_chat), (1, 0, 0))


class SendTextHtmlTests(TestCase):
    """Regressiya: xabarlar <b> teglari bilan, matn ko'rinishida ketardi."""

    @patch.object(tg, "tg_call")
    def test_tags_are_sent_as_html(self, tg_call):
        tg.send_text(1, "🧾 <b>Chek</b>")
        payload = tg_call.call_args[0][1]
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertEqual(payload["text"], "🧾 <b>Chek</b>")

    @patch.object(tg, "tg_call")
    def test_stray_angle_brackets_are_escaped(self, tg_call):
        """Menejer yozgan "5 < 6" yoki <div> xabarni buzmasligi kerak."""
        tg.send_text(1, "5 < 6 & <div>salom</div>")
        payload = tg_call.call_args[0][1]
        self.assertEqual(
            payload["text"], "5 &lt; 6 &amp; &lt;div&gt;salom&lt;/div&gt;"
        )

    @patch.object(tg, "tg_call")
    def test_falls_back_to_plain_text_on_parse_error(self, tg_call):
        tg_call.side_effect = [
            RuntimeError("Bad Request: can't parse entities"),
            {"ok": True},
        ]
        tg.send_text(1, "<b>Chek</b>")
        self.assertEqual(tg_call.call_count, 2)
        second = tg_call.call_args[0][1]
        self.assertNotIn("parse_mode", second)
        self.assertEqual(second["text"], "<b>Chek</b>")

    @patch.object(tg, "tg_call")
    def test_other_errors_are_not_retried(self, tg_call):
        tg_call.side_effect = RuntimeError("Forbidden: bot was blocked")
        with self.assertRaises(RuntimeError):
            tg.send_text(1, "Salom")
        self.assertEqual(tg_call.call_count, 1)


class TgCallTests(TestCase):
    def test_missing_token_raises_clear_error(self):
        with self.settings(TG_BOT_TOKEN=""):
            with self.assertRaises(RuntimeError) as ctx:
                tg.tg_call("sendMessage", {})
        self.assertIn("TG_BOT_TOKEN", str(ctx.exception))
