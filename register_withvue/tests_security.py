"""Xavfsizlik mustahkamlash testlari.

Ushbu testlar quyidagi himoyalarni qo'riqlaydi:
  * API gate — token yo'q so'rovga 401 (deny-by-default middleware),
  * IDOR — o'quvchi boshqa odamning balansini ko'ra olmasligi,
  * kartani o'zgartirish faqat vakolatli menejerga,
  * ochiq register/login/verify endpoint'lariga rate limit,
  * o'quvchi o'z nomidan boshqaning chekini yubora olmasligi.
"""

import json
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.test import Client, TestCase
from django.test.client import RequestFactory

from . import views
from .models import (
    Manager,
    Payment,
    PaymentSettings,
    PaymentRequest,
    Student,
    Teacher,
)


def _clear_ratelimit_cache():
    """Testlar orasida rate limit hisoblagichini tozalaydi."""
    cache.clear()


class _AuthCase(TestCase):
    """JWT tokenni o'zi yasab beradigan umumiy asos."""

    def setUp(self):
        cache.clear()
        self.rf = RequestFactory()

    def _token(self, phone, role="manager"):
        return views.issue_tokens(phone, role)["access"]

    def _auth(self, phone, role="manager"):
        return {"HTTP_AUTHORIZATION": f"Bearer {self._token(phone, role)}"}


class ApiGateTests(TestCase):
    """/api/ ostidagi himoyasiz endpoint'lar JWT talab qiladi."""

    def setUp(self):
        cache.clear()

    def test_students_list_requires_token(self):
        res = Client().get("/api/students/")
        self.assertEqual(res.status_code, 401, res.content[:200])

    def test_payments_list_requires_token(self):
        res = Client().get("/api/payments/")
        self.assertEqual(res.status_code, 401)

    def test_teachers_list_requires_token(self):
        res = Client().get("/api/teachers/")
        self.assertEqual(res.status_code, 401)

    def test_delete_student_requires_token(self):
        res = Client().delete("/api/students/delete/1/")
        self.assertEqual(res.status_code, 401)

    def test_update_payment_settings_requires_token(self):
        res = Client().patch(
            "/api/payment-settings/update/",
            data=json.dumps({"card_number": "8600"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 401)

    def test_public_endpoints_stay_open(self):
        self.assertEqual(Client().get("/api/ping/").status_code, 200)

    def test_login_still_works_without_token(self):
        """Gate ommaviy endpoint'larni buzmasligi kerak."""
        Manager.objects.create(
            name="M", phone="+998900000001", password="x"
        )
        res = Client().post(
            "/api/manager/login/",
            data=json.dumps({"phone": "+998900000001", "password": None}),
            content_type="application/json",
        )
        # 'exists' javobi keldi — gate so'rovni to'smadi
        self.assertEqual(res.status_code, 200)
        self.assertIn("exists", json.loads(res.content))

    def test_register_still_works_without_token(self):
        res = Client().post(
            "/api/register/",
            data=json.dumps({"name": "Yangi", "phone": "+998901112233"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201, res.content[:200])


class IdorProtectionTests(_AuthCase):
    """O'quvchi faqat o'z ma'lumotini ko'radi (IDOR himoyasi)."""

    def setUp(self):
        super().setUp()
        self.ali = Student.objects.create(
            name="Ali", surname="Valiyev", phone="+998900000001"
        )
        self.vali = Student.objects.create(
            name="Vali", surname="Tosh", phone="+998900000002"
        )

    def _student_auth(self):
        return self._auth(self.ali.phone, role="student")

    def test_student_cannot_see_others_wallet(self):
        req = self.rf.get(
            f"/api/students/{self.vali.id}/wallet/",
            **self._student_auth(),
        )
        res = views.get_student_wallet(req, self.vali.id)
        self.assertEqual(res.status_code, 403)

    def test_student_sees_own_wallet(self):
        req = self.rf.get(
            f"/api/students/{self.ali.id}/wallet/",
            **self._student_auth(),
        )
        res = views.get_student_wallet(req, self.ali.id)
        self.assertEqual(res.status_code, 200)

    def test_student_cannot_see_others_coins(self):
        req = self.rf.get(
            f"/api/coins/student/{self.vali.id}/",
            **self._student_auth(),
        )
        res = views.get_student_coins(req, self.vali.id)
        self.assertEqual(res.status_code, 403)

    def test_student_cannot_see_others_payments(self):
        req = self.rf.get(
            f"/api/payments/{self.vali.id}/",
            **self._student_auth(),
        )
        res = views.get_payments(req, self.vali.id)
        self.assertEqual(res.status_code, 403)

    def test_student_cannot_see_others_orders(self):
        req = self.rf.get(
            f"/api/orders/student/{self.vali.id}/",
            **self._student_auth(),
        )
        res = views.get_student_orders(req, self.vali.id)
        self.assertEqual(res.status_code, 403)

    def test_student_cannot_see_others_coin_history(self):
        req = self.rf.get(
            f"/api/coins/transactions/{self.vali.id}/",
            **self._student_auth(),
        )
        res = views.get_coin_transactions(req, self.vali.id)
        self.assertEqual(res.status_code, 403)

    def test_manager_can_see_any_wallet(self):
        """Menejer/ustoz — barcha o'quvchilarni ko'ra oladi (o'qitish uchun)."""
        req = self.rf.get(
            f"/api/students/{self.vali.id}/wallet/",
            **self._auth("+998900000999"),
        )
        # Menejer yozuvi yo'q bo'lsa ham ustoz bo'lsa ko'radi — shu testda
        # menejer yaratamiz
        Manager.objects.create(
            name="Menejer", phone="+998900000999", password="x", is_super=True
        )
        req = self.rf.get(
            f"/api/students/{self.vali.id}/wallet/",
            **self._auth("+998900000999"),
        )
        res = views.get_student_wallet(req, self.vali.id)
        self.assertEqual(res.status_code, 200)

    def test_teacher_can_see_student_payments(self):
        Teacher.objects.create(
            name="Ustoz Aka", phone="+998900000888", password="x"
        )
        req = self.rf.get(
            f"/api/payments/{self.vali.id}/",
            **self._auth("+998900000888", role="teacher"),
        )
        res = views.get_payments(req, self.vali.id)
        self.assertEqual(res.status_code, 200)


class PaymentCardProtectionTests(_AuthCase):
    """To'lov kartasini faqat vakolatli menejer o'zgartira oladi."""

    def setUp(self):
        super().setUp()
        PaymentSettings.get_settings()

    def test_anonymous_cannot_read_card(self):
        req = self.rf.get("/api/payment-settings/")
        res = views.get_payment_settings(req)
        self.assertEqual(res.status_code, 401)

    def test_anonymous_cannot_change_card(self):
        req = self.rf.patch(
            "/api/payment-settings/update/",
            data=json.dumps({"card_number": "8600123456789012"}),
            content_type="application/json",
        )
        res = views.update_payment_settings(req)
        # View 403 qaytaradi (token'siz 401 middleware qatlamida)
        self.assertEqual(res.status_code, 403)

    def test_student_cannot_change_card(self):
        Student.objects.create(
            name="Ali", surname="V", phone="+998900000001"
        )
        req = self.rf.patch(
            "/api/payment-settings/update/",
            data=json.dumps({"card_number": "8600123456789012"}),
            content_type="application/json",
            **self._auth("+998900000001", role="student"),
        )
        res = views.update_payment_settings(req)
        self.assertEqual(res.status_code, 403)

    def test_manager_without_permission_cannot_change_card(self):
        Manager.objects.create(
            name="Kassir",
            phone="+998900000001",
            password="x",
            permissions=["cash.view"],  # payments.settings yo'q
        )
        req = self.rf.patch(
            "/api/payment-settings/update/",
            data=json.dumps({"card_number": "8600123456789012"}),
            content_type="application/json",
            **self._auth("+998900000001"),
        )
        res = views.update_payment_settings(req)
        self.assertEqual(res.status_code, 403)

    def test_authorized_manager_can_change_card(self):
        Manager.objects.create(
            name="Menejer",
            phone="+998900000001",
            password="x",
            permissions=["payments.settings"],
        )
        req = self.rf.patch(
            "/api/payment-settings/update/",
            data=json.dumps(
                {"card_number": "8600123456789012", "card_holder": "Ali Valiyev"}
            ),
            content_type="application/json",
            **self._auth("+998900000001"),
        )
        res = views.update_payment_settings(req)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            PaymentSettings.get_settings().card_number, "8600123456789012"
        )


class PaymentRequestGuardTests(_AuthCase):
    """To'lov so'rovini faqat vakolatli menejer hal qila oladi."""

    def setUp(self):
        super().setUp()
        self.student = Student.objects.create(
            name="Ali", surname="Valiyev", phone="+998900000001"
        )
        self.pr = PaymentRequest.objects.create(
            student=self.student, receipt_b64="data:image/png;base64,xxx"
        )
        self.mgr = Manager.objects.create(
            name="Menejer",
            phone="+998900000009",
            password="x",
            permissions=["payments.requests"],
        )

    def _req(self, body, phone=None, role="manager"):
        return self.rf.patch(
            f"/api/payment-requests/{self.pr.id}/accept/",
            data=json.dumps(body),
            content_type="application/json",
            **self._auth(phone or self.mgr.phone, role),
        )

    def test_anonymous_cannot_accept(self):
        req = self.rf.patch(
            f"/api/payment-requests/{self.pr.id}/accept/",
            data=json.dumps({"amount": 100000, "month": "2026-08"}),
            content_type="application/json",
        )
        res = views.accept_payment_request(req, self.pr.id)
        self.assertEqual(res.status_code, 403)

    def test_unauthorized_manager_cannot_accept(self):
        Manager.objects.create(
            name="Oddiy", phone="+998900000002", password="x", permissions=[]
        )
        req = self._req(
            {"amount": 100000, "month": "2026-08"}, phone="+998900000002"
        )
        res = views.accept_payment_request(req, self.pr.id)
        self.assertEqual(res.status_code, 403)

    @patch("register_withvue.telegram.send_receipt")
    def test_authorized_manager_accepts(self, _send_receipt):
        req = self._req({"amount": 100000, "month": "2026-08"})
        res = views.accept_payment_request(req, self.pr.id)
        self.assertEqual(res.status_code, 200)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, "accepted")

    def test_anonymous_cannot_reject(self):
        req = self.rf.patch(
            f"/api/payment-requests/{self.pr.id}/reject/",
            data="{}",
            content_type="application/json",
        )
        res = views.reject_payment_request(req, self.pr.id)
        self.assertEqual(res.status_code, 403)


class ReceiptOwnershipTests(_AuthCase):
    """O'quvchi faqat o'z nomidan chek yubora oladi."""

    def setUp(self):
        super().setUp()
        self.ali = Student.objects.create(
            name="Ali", surname="Valiyev", phone="+998900000001"
        )
        self.vali = Student.objects.create(
            name="Vali", surname="Tosh", phone="+998900000002"
        )

    def test_student_cannot_send_receipt_in_someone_elses_name(self):
        req = self.rf.post(
            "/api/payment-requests/create/",
            data=json.dumps(
                {"student_id": self.vali.id, "receipt_b64": "data:image/png;base64,x"}
            ),
            content_type="application/json",
            **self._auth(self.ali.phone, role="student"),
        )
        res = views.create_payment_request(req)
        self.assertEqual(res.status_code, 403)

    def test_anonymous_receipt_for_own_id_still_works(self):
        """Token'siz chek yuborish (eski mijoz / bot) o'z ID bo'yicha ishlaydi."""
        req = self.rf.post(
            "/api/payment-requests/create/",
            data=json.dumps(
                {"student_id": self.ali.id, "receipt_b64": "data:image/png;base64,x"}
            ),
            content_type="application/json",
        )
        with patch("register_withvue.telegram.notify_managers"):
            res = views.create_payment_request(req)
        self.assertEqual(res.status_code, 201, res.content[:200])


class RateLimitTests(_AuthCase):
    """Ochiq endpoint'larga ortiqcha so'rov 429 oladi."""

    def tearDown(self):
        cache.clear()

    def _probe_many(self, n):
        out = []
        for i in range(n):
            req = self.rf.post(
                "/api/login/",
                data=json.dumps({"phone": "+998900000001", "password": None}),
                content_type="application/json",
            )
            out.append(views.login_student(req))
        return out

    def test_phone_probe_is_rate_limited(self):
        responses = self._probe_many(25)
        codes = [r.status_code for r in responses]
        self.assertIn(429, codes, "limit oshganda 429 qaytishi kerak")

    def test_probe_limit_is_per_phone(self):
        """Boshqa raqamni tekshirish bloklanmagan bo'lishi kerak."""
        # Bitta raqam bo'yicha limitni to'ldiramiz
        for _ in range(22):
            req = self.rf.post(
                "/api/login/",
                data=json.dumps({"phone": "+998900000001", "password": None}),
                content_type="application/json",
            )
            views.login_student(req)
        # Boshqa raqam — limit alohida
        req = self.rf.post(
            "/api/login/",
            data=json.dumps({"phone": "+998900000099", "password": None}),
            content_type="application/json",
        )
        res = views.login_student(req)
        self.assertEqual(res.status_code, 200)

    def test_verification_send_is_rate_limited(self):
        with patch("register_withvue.views.settings") as _s, patch(
            "register_withvue.telegram.tg_call"
        ):
            codes = []
            for i in range(8):
                req = self.rf.post(
                    "/api/verify/send-code/",
                    data=json.dumps({"phone": "+998900000010"}),
                    content_type="application/json",
                )
                res = views.send_verification_code(req)
                codes.append(res.status_code)
            self.assertIn(429, codes)

    def test_verification_check_is_rate_limited(self):
        codes = []
        for i in range(12):
            req = self.rf.post(
                "/api/verify/check-code/",
                data=json.dumps({"phone": "+998900000010", "code": "000000"}),
                content_type="application/json",
            )
            res = views.check_verification_code(req)
            codes.append(res.status_code)
        self.assertIn(429, codes)


class DestructiveActionGuardTests(_AuthCase):
    """Qaytarib bo'lmaydigan amallar faqat menejer/adminga."""

    def setUp(self):
        super().setUp()
        self.student = Student.objects.create(
            name="Ali", surname="Valiyev", phone="+998900000001"
        )

    def test_anonymous_cannot_delete_student(self):
        req = self.rf.delete(f"/api/students/delete/{self.student.id}/")
        res = views.delete_student(req, self.student.id)
        self.assertEqual(res.status_code, 403)
        self.assertTrue(Student.objects.filter(id=self.student.id).exists())

    def test_student_cannot_delete_anyone(self):
        req = self.rf.delete(
            f"/api/students/delete/{self.student.id}/",
            **self._auth(self.student.phone, role="student"),
        )
        res = views.delete_student(req, self.student.id)
        self.assertEqual(res.status_code, 403)
        self.assertTrue(Student.objects.filter(id=self.student.id).exists())

    def test_manager_can_delete_student(self):
        Manager.objects.create(
            name="Menejer", phone="+998900000009", password="x", is_super=True
        )
        req = self.rf.delete(
            f"/api/students/delete/{self.student.id}/",
            **self._auth("+998900000009"),
        )
        res = views.delete_student(req, self.student.id)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Student.objects.filter(id=self.student.id).exists())

    def test_anonymous_cannot_create_teacher(self):
        req = self.rf.post(
            "/api/teachers/create/",
            data=json.dumps({"name": "Xaker", "phone": "+998900000666"}),
            content_type="application/json",
        )
        res = views.create_teacher(req)
        self.assertEqual(res.status_code, 403)
        self.assertFalse(Teacher.objects.filter(phone="+998900000666").exists())

    def test_manager_can_create_teacher(self):
        Manager.objects.create(
            name="Menejer", phone="+998900000009", password="x", is_super=True
        )
        req = self.rf.post(
            "/api/teachers/create/",
            data=json.dumps(
                {"name": "Jasur", "phone": "+998900000777"}
            ),
            content_type="application/json",
            **self._auth("+998900000009"),
        )
        res = views.create_teacher(req)
        self.assertEqual(res.status_code, 201)

    def test_anonymous_cannot_generate_payments(self):
        req = self.rf.post(
            "/api/payments/generate/",
            data=json.dumps({"month": "2026-08"}),
            content_type="application/json",
        )
        res = views.generate_payments(req)
        self.assertEqual(res.status_code, 403)

    def test_anonymous_cannot_broadcast_messages(self):
        req = self.rf.post(
            "/api/messages/send-all/",
            data=json.dumps({"text": "spam"}),
            content_type="application/json",
        )
        res = views.send_message_all(req)
        self.assertEqual(res.status_code, 403)


class MonthlyGenerationPermissionTests(_AuthCase):
    """generate_payments — faqat menejer/admin (ustoz ham yo'q)."""

    def test_teacher_cannot_generate_payments(self):
        Teacher.objects.create(
            name="Ustoz Aka", phone="+998900000888", password="x"
        )
        req = self.rf.post(
            "/api/payments/generate/",
            data=json.dumps({"month": "2026-08"}),
            content_type="application/json",
            **self._auth("+998900000888", role="teacher"),
        )
        res = views.generate_payments(req)
        self.assertEqual(res.status_code, 403)

    def test_manager_can_generate_payments(self):
        Manager.objects.create(
            name="Menejer", phone="+998900000009", password="x", is_super=True
        )
        req = self.rf.post(
            "/api/payments/generate/",
            data=json.dumps({"month": "2026-08"}),
            content_type="application/json",
            **self._auth("+998900000009"),
        )
        res = views.generate_payments(req)
        self.assertEqual(res.status_code, 200)


class PaymentWritePermissionTests(_AuthCase):
    """confirm/update_payment — o'quvchi o'z to'lovini tasdiqlay olmasin."""

    def setUp(self):
        super().setUp()
        self.student = Student.objects.create(
            name="Ali", surname="Vali", phone="+998900000010", stage=1
        )
        self.payment = Payment.objects.create(
            student=self.student, month="2026-08", stage=1, amount_due=400000
        )

    def _patch(self, phone, role, body):
        return self.rf.patch(
            "/x",
            data=json.dumps(body),
            content_type="application/json",
            **self._auth(phone, role),
        )

    def test_student_cannot_confirm_own_payment(self):
        res = views.confirm_payment(
            self._patch(
                "+998900000010",
                "student",
                {"paid_amount": 400000},
            ),
            self.payment.id,
        )
        self.assertEqual(res.status_code, 403)
        self.payment.refresh_from_db()
        self.assertFalse(self.payment.is_paid)

    def test_student_cannot_change_due_amount(self):
        res = views.update_payment_amount(
            self._patch("+998900000010", "student", {"amount_due": 1000}),
            self.payment.id,
        )
        self.assertEqual(res.status_code, 403)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.amount_due, 400000)

    def test_student_cannot_give_self_coins(self):
        res = views.give_manual_coins(
            self.rf.post(
                "/x",
                data=json.dumps(
                    {"student_id": self.student.id, "reason": "manual", "amount": 100}
                ),
                content_type="application/json",
                **self._auth("+998900000010", "student"),
            )
        )
        self.assertEqual(res.status_code, 403)
        self.student.refresh_from_db()
        self.assertEqual(self.student.coin_balance, 0)


class AttendancePermissionTests(_AuthCase):
    """Davomat yozish va boshqaning davomatini ko'rish himoyasi."""

    def setUp(self):
        super().setUp()
        self.teacher = Teacher.objects.create(
            name="Ustoz", phone="+998900000011", password="x"
        )
        self.student = Student.objects.create(
            name="Ali", surname="Vali", phone="+998900000012", stage=1
        )
        self.other = Student.objects.create(
            name="Vali", surname="Aliyev", phone="+998900000013", stage=1
        )

    def test_student_cannot_mark_attendance(self):
        # Board o'chadi (ustoz sifatida) — yozuv yaratiladi
        board = views.attendance_group_day(
            self.rf.get(
                "/api/attendance/group-day/",
                {"group_id": 1},
                **self._auth("+998900000011", "teacher"),
            )
        )
        self.assertEqual(board.status_code, 404)  # guruh yo'q — xatosiz chiqish

        # Oddiy o'quvchi tokeni bilan davomatni o'zgartirishga urinish
        res = views.update_attendance(
            self.rf.patch(
                "/x",
                data=json.dumps({"status": "present"}),
                content_type="application/json",
                **self._auth("+998900000012", "student"),
            ),
            attendance_id=1,
        )
        self.assertEqual(res.status_code, 403)

    def test_student_cannot_read_other_attendance(self):
        res = views.get_student_attendance(
            self.rf.get("/x", **self._auth("+998900000012", "student")),
            student_id=self.other.id,
        )
        self.assertEqual(res.status_code, 403)

    def test_own_attendance_is_readable(self):
        res = views.get_student_attendance(
            self.rf.get("/x", **self._auth("+998900000012", "student")),
            student_id=self.student.id,
        )
        self.assertEqual(res.status_code, 200)

    def test_student_cannot_create_lesson(self):
        res = views.create_lesson(
            self.rf.post(
                "/x",
                data=json.dumps(
                    {
                        "teacher_id": self.teacher.id,
                        "date": "2026-08-10",
                        "title": "Dars",
                    }
                ),
                content_type="application/json",
                **self._auth("+998900000012", "student"),
            )
        )
        self.assertEqual(res.status_code, 403)

    def test_student_cannot_update_student_record(self):
        res = views.update_student(
            self.rf.patch(
                "/x",
                data=json.dumps({"status": "left"}),
                content_type="application/json",
                **self._auth("+998900000012", "student"),
            ),
            student_id=self.other.id,
        )
        self.assertEqual(res.status_code, 403)


class PermissionCatalogEnforcementTests(_AuthCase):
    """Nozik vakolatlar: perm'siz menejer 403, vakolatli menejer o'tadi.

    DEFAULT_PERMISSIONS'dagi (kundalik to'plam) vakolatlar bilan ham
    asosiy ish oqimi ishlashi kerak.
    """

    def setUp(self):
        super().setUp()
        from datetime import time

        from .models import Course, Group, Payment, Product

        self.student = Student.objects.create(
            name="Ali", surname="Vali", phone="+998900000030", stage=1
        )
        self.teacher = Teacher.objects.create(
            name="Ustoz", phone="+998900000031", password="x"
        )
        self.group = Group.objects.create(
            name="G1", teacher=self.teacher, lesson_time=time(10, 0)
        )
        self.course = Course.objects.create(name="Frontend", monthly_fee=400000)
        self.product = Product.objects.create(
            name="Stiker", price_coins=10, stock=100
        )
        self.payment = Payment.objects.create(
            student=self.student, month="2026-08", stage=1, amount_due=400000
        )

    def _mgr(self, phone, perms=None, is_super=False):
        Manager.objects.create(
            name="M",
            surname="X",
            phone=phone,
            password="x",
            is_super=is_super,
            permissions=perms or [],
        )
        return self._auth(phone)

    def test_manager_without_group_perm_cannot_create_group(self):
        auth = self._mgr("+998900000032", perms=["students.view"])
        req = self.rf.post(
            "/x",
            data=json.dumps({"name": "Yangi", "teacher_id": self.teacher.id}),
            content_type="application/json",
            **auth,
        )
        self.assertEqual(views.create_group(req).status_code, 403)

    def test_manager_with_group_perm_can_create_group(self):
        auth = self._mgr("+998900000033", perms=["groups.edit"])
        req = self.rf.post(
            "/x",
            data=json.dumps(
                {"name": "Yangi", "teacher_id": self.teacher.id, "lesson_time": "10:00"}
            ),
            content_type="application/json",
            **auth,
        )
        self.assertEqual(views.create_group(req).status_code, 201)

    def test_cashier_can_take_payment_without_payments_edit(self):
        auth = self._mgr("+998900000034", perms=["cash.view", "cash.close"])
        req = self.rf.post(
            "/x",
            data=json.dumps({"amount": 50000}),
            content_type="application/json",
            **auth,
        )
        res = views.add_payment_installment(req, self.payment.id)
        self.assertEqual(res.status_code, 201)

    def test_manager_without_any_money_perm_cannot_take_payment(self):
        auth = self._mgr("+998900000035", perms=["students.view"])
        req = self.rf.post(
            "/x",
            data=json.dumps({"amount": 50000}),
            content_type="application/json",
            **auth,
        )
        res = views.add_payment_installment(req, self.payment.id)
        self.assertEqual(res.status_code, 403)

    def test_manager_without_shop_perm_cannot_manage_products(self):
        auth = self._mgr("+998900000036", perms=["students.view"])
        req = self.rf.delete(
            "/x", **auth
        )
        self.assertEqual(views.delete_product(req, self.product.id).status_code, 403)

    def test_manager_with_shop_perm_can_delete_product(self):
        auth = self._mgr("+998900000037", perms=["shop.products"])
        req = self.rf.delete("/x", **auth)
        self.assertEqual(views.delete_product(req, self.product.id).status_code, 200)

    def test_financial_reads_need_perm(self):
        # Vakolatsiz menejer — 403 (xodim bo'lsa ham vakolat kerak)
        auth = self._mgr("+998900000038", perms=[])
        for view, kwargs in (
            (views.get_all_payments, {}),
            (views.get_message_history, {}),
            (views.get_graduates, {}),
        ):
            req = self.rf.get("/x", **auth)
            self.assertEqual(view(req, **kwargs).status_code, 403)

        # O'quvchi ham ko'ra olmaydi
        sreq = self.rf.get("/x", **self._auth("+998900000030", "student"))
        self.assertEqual(views.get_all_payments(sreq).status_code, 403)

    def test_financial_reads_with_perm_pass(self):
        # payments.view + history.view + database.view — o'qishlar o'tadi
        auth = self._mgr(
            "+998900000045", perms=["payments.view", "history.view", "database.view"]
        )
        for view, kwargs in (
            (views.get_all_payments, {}),
            (views.get_message_history, {}),
            (views.get_graduates, {}),
        ):
            req = self.rf.get("/x", **auth)
            self.assertEqual(view(req, **kwargs).status_code, 200)

    def test_default_permissions_still_cover_daily_work(self):
        from .access import DEFAULT_PERMISSIONS

        auth = self._mgr("+998900000039", perms=DEFAULT_PERMISSIONS)
        # Kassirlik: pul qabul qilish
        req = self.rf.post(
            "/x",
            data=json.dumps({"amount": 50000}),
            content_type="application/json",
            **auth,
        )
        self.assertEqual(
            views.add_payment_installment(req, self.payment.id).status_code, 201
        )
        # Guruh ko'rish + davomat belgilash (staff) o'tadi
        req = self.rf.get("/x", **auth)
        self.assertEqual(views.get_teachers(req).status_code, 200)


class StudentsRosterScopingTests(_AuthCase):
    """Oddiy o'quvchi butun ro'yxatni ko'ra olmasin — faqat guruhdoshtarlar."""

    def setUp(self):
        super().setUp()
        from datetime import time

        from .models import Group

        self.teacher = Teacher.objects.create(
            name="Ustoz", phone="+998900000021", password="x"
        )
        self.me = Student.objects.create(
            name="Ali", surname="Vali", phone="+998900000022", stage=1
        )
        self.mate = Student.objects.create(
            name="Vali", surname="Aliyev", phone="+998900000023", stage=1
        )
        self.stranger = Student.objects.create(
            name="Sen", surname="Boshqasan", phone="+998900000024", stage=1
        )
        self.group = Group.objects.create(
            name="G1", teacher=self.teacher, lesson_time=time(10, 0)
        )
        self.group.students.add(self.me, self.mate)

    def test_student_sees_only_own_group(self):
        res = views.get_students(
            self.rf.get("/x", **self._auth("+998900000022", "student"))
        )
        self.assertEqual(res.status_code, 200)
        rows = json.loads(res.content)
        ids = {r["id"] for r in rows}
        self.assertEqual(ids, {self.me.id, self.mate.id})
        self.assertNotIn(self.stranger.id, ids)

    def test_teacher_sees_full_roster(self):
        res = views.get_students(
            self.rf.get("/x", **self._auth("+998900000021", "teacher"))
        )
        self.assertEqual(res.status_code, 200)
        rows = json.loads(res.content)
        ids = {r["id"] for r in rows}
        self.assertIn(self.stranger.id, ids)


class ChangePasswordBruteForceTests(_AuthCase):
    """change_password ochiq endpoint — parol taxminash cheklovi."""

    def setUp(self):
        super().setUp()
        self.student = Student.objects.create(
            name="Ali",
            surname="Vali",
            phone="+998900000014",
            password=make_password("EskiParol123"),
        )

    def _post(self, old):
        return views.change_password(
            self.rf.post(
                "/x",
                data=json.dumps(
                    {
                        "phone": "+998900000014",
                        "old_password": old,
                        "new_password": "YangiParol123",
                    }
                ),
                content_type="application/json",
                REMOTE_ADDR="10.0.0.1",
            )
        )

    def test_wrong_old_password_is_limited(self):
        codes = {_post.status_code for _post in (self._post("x") for _ in range(6))}
        self.assertIn(429, codes)

    def test_success_resets_the_counter(self):
        # Parolni to'g'ri o'zgartirib, hisoblagichni tozalaymiz
        self.assertEqual(self._post("EskiParol123").status_code, 200)

        # Keyin noto'g'ri urinishlar yana to'liq limit oladi
        self.student.password = make_password("EskiParol123")
        self.student.save(update_fields=["password"])
        self.assertEqual(self._post("xatoParol").status_code, 401)


class CrossRoleDenyTests(_AuthCase):
    """Rol chegaralari: o'quvchi/ustoz tokeni boshqalar ma'lumotini ololmaydi.

    Token o'g'irlangan (yoki soxta front yaratilgan) taqdirda ham
    o'quvchi faqat o'z ma'lumotiga ega bo'ladi — barcha boshqa
    endpointlar 403 qaytaradi.
    """

    def setUp(self):
        super().setUp()
        self.student = Student.objects.create(
            name="Ali", surname="Vali", phone="+998900000020"
        )
        self.other = Student.objects.create(
            name="Vali", surname="Aliyev", phone="+998900000021"
        )
        self.teacher = Teacher.objects.create(
            name="Ustoz Aka", phone="+998900000022", password="x"
        )
        self.unperm_manager = Manager.objects.create(
            name="Bo'sh",
            surname="Menejer",
            phone="+998900000023",
            password="x",
            permissions=[],  # hech qanday vakolat yo'q
        )

    def _student_auth(self):
        return self._auth(self.student.phone, "student")

    def test_student_token_cannot_list_all_students(self):
        res = self.client.get("/api/students/overview/", **self._student_auth())
        self.assertEqual(res.status_code, 403)

    def test_student_token_cannot_read_others_balance(self):
        res = self.client.get(
            f"/api/coins/balance/{self.other.id}/", **self._student_auth()
        )
        self.assertEqual(res.status_code, 403)

    def test_student_token_cannot_give_coins(self):
        res = self.client.post(
            "/api/coins/add/",
            data=json.dumps(
                {"student_id": self.other.id, "amount": 100, "reason": "test"}
            ),
            content_type="application/json",
            **self._student_auth(),
        )
        self.assertEqual(res.status_code, 403)

    def test_student_token_cannot_see_all_payments(self):
        res = self.client.get("/api/payments/", **self._student_auth())
        self.assertEqual(res.status_code, 403)

    def test_student_token_cannot_see_leads(self):
        res = self.client.get("/api/leads/", **self._student_auth())
        self.assertEqual(res.status_code, 403)

    def test_student_token_cannot_see_payment_requests(self):
        res = self.client.get("/api/payment-requests/", **self._student_auth())
        self.assertIn(res.status_code, (403, 404))

    def test_student_token_cannot_see_attendance(self):
        res = self.client.get("/api/monthly-absences/", **self._student_auth())
        self.assertEqual(res.status_code, 403)

    def test_student_token_cannot_delete_student(self):
        res = self.client.delete(
            f"/api/students/delete/{self.other.id}/", **self._student_auth()
        )
        self.assertEqual(res.status_code, 403)

    def test_permissionless_manager_cannot_give_coins(self):
        res = self.client.post(
            "/api/coins/add/",
            data=json.dumps(
                {"student_id": self.other.id, "amount": 100, "reason": "test"}
            ),
            content_type="application/json",
            **self._auth(self.unperm_manager.phone, "manager"),
        )
        self.assertEqual(res.status_code, 403)

    def test_permissionless_manager_cannot_see_leads(self):
        res = self.client.get(
            "/api/leads/", **self._auth(self.unperm_manager.phone, "manager")
        )
        self.assertEqual(res.status_code, 403)

    def test_super_manager_sees_everything(self):
        super_mgr = Manager.objects.create(
            name="Super",
            surname="Direktor",
            phone="+998900000024",
            password="x",
            is_super=True,
        )
        res = self.client.get("/api/leads/", **self._auth(super_mgr.phone, "manager"))
        self.assertEqual(res.status_code, 200)
        res2 = self.client.get(
            "/api/students/overview/", **self._auth(super_mgr.phone, "manager")
        )
        self.assertEqual(res2.status_code, 200)
