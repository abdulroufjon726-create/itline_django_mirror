"""Edu Tizim ro'yxati bo'yicha qo'shilgan funksiyalar testlari.

Qamrab olinadi:
  * o'quvchi holati (kutilmoqda / bog'lanish kerak / faol),
  * barcha ro'yxatlar uchun umumiy sana oralig'i (?from=&to=/?month=/?year=),
  * ustoz kesimidagi to'lov statistikasi va tarixi,
  * xonalar va xona bandligi tekshiruvi,
  * kurs darajalari va daraja narxining to'lovga ta'siri,
  * jadvaldan o'quvchi yuklash (dry-run va haqiqiy yozish).
"""

import json
from datetime import date, datetime, time, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase
from django.test.client import RequestFactory

from . import views
from .models import (
    CashEntry,
    CashSession,
    Course,
    CourseLevel,
    Group,
    Manager,
    Payment,
    Room,
    StagePrice,
    Student,
    Teacher,
)

TODAY = date(2026, 8, 15)


def _fixed_today():
    return patch("register_withvue.views.tashkent_today", return_value=TODAY)


def _created_at(day, hour=12):
    """O'sha Toshkent kunining ichidagi payt — UTC'da saqlanadi."""
    return datetime(day.year, day.month, day.day, hour, tzinfo=dt_timezone.utc)


class ApiCase(TestCase):
    """Menejer sifatida chaqirish uchun umumiy asos."""

    def setUp(self):
        self.rf = RequestFactory()
        self.manager = Manager.objects.create(
            name="Menejer", phone="+998901112233", password="x", is_super=True
        )

    def _headers(self):
        return {"HTTP_X_USER_PHONE": self.manager.phone}

    def get(self, path, **params):
        return self.rf.get(path, data=params, **self._headers())

    def post(self, path, body):
        return self.rf.post(
            path,
            data=json.dumps(body),
            content_type="application/json",
            **self._headers(),
        )

    def patch_(self, path, body):
        return self.rf.patch(
            path,
            data=json.dumps(body),
            content_type="application/json",
            **self._headers(),
        )

    def body(self, response):
        return json.loads(response.content)


# ─────────────────────────────────────────
# O'QUVCHI HOLATI
# ─────────────────────────────────────────


class StudentStatusTests(ApiCase):
    def test_manager_can_pick_status_on_create(self):
        resp = views.register_student(
            self.post("/api/register/", {"name": "Ali", "phone": "+998900000001",
                                         "status": "contact"})
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self.body(resp)["status"], "contact")
        self.assertEqual(Student.objects.get(name="Ali").status, "contact")

    def test_manager_default_is_active(self):
        views.register_student(self.post("/api/register/", {"name": "Vali", "phone": "+998900000002"}))
        self.assertEqual(Student.objects.get(name="Vali").status, "active")

    def test_self_registration_lands_in_pending(self):
        """Menejer sarlavhasisiz kelgan ro'yxatdan o'tish — kutilmoqda."""
        req = self.rf.post(
            "/api/register/",
            data=json.dumps({"name": "Sami", "phone": "+998900000003"}),
            content_type="application/json",
        )
        views.register_student(req)
        self.assertEqual(Student.objects.get(name="Sami").status, "pending")

    def test_unknown_status_rejected(self):
        resp = views.register_student(
            self.post("/api/register/", {"name": "X", "phone": "+998900000004",
                                         "status": "kutilmoqda"})
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("status", self.body(resp)["error"])

    def test_status_change_is_recorded(self):
        s = Student.objects.create(name="Ali", surname="V", phone="+998900000005")
        resp = views.update_student(
            self.patch_("/x", {"status": "contact", "status_note": "javob bermadi"}),
            student_id=s.id,
        )
        self.assertEqual(resp.status_code, 200)
        s.refresh_from_db()
        self.assertEqual(s.status, "contact")
        self.assertEqual(s.status_note, "javob bermadi")
        self.assertIsNotNone(s.status_changed_at)

    def test_payments_are_generated_only_for_active_students(self):
        StagePrice.objects.create(stage=1, price=400000)
        active = Student.objects.create(name="Faol", surname="A", phone="+99890000011",
                                        status="active")
        pending = Student.objects.create(name="Kutgan", surname="B", phone="+99890000012",
                                         status="pending")
        contact = Student.objects.create(name="Qo'ng'iroq", surname="C", phone="+99890000013",
                                         status="contact")

        resp = views.generate_payments(self.post("/api/payments/generate/", {"month": "2026-08"}))
        self.assertEqual(resp.status_code, 200)

        self.assertTrue(Payment.objects.filter(student=active, month="2026-08").exists())
        self.assertFalse(Payment.objects.filter(student=pending).exists())
        self.assertFalse(Payment.objects.filter(student=contact).exists())
        self.assertEqual(self.body(resp)["inactive"], 2)


# ─────────────────────────────────────────
# SANA ORALIG'I
# ─────────────────────────────────────────


class DateRangeTests(ApiCase):
    def setUp(self):
        super().setUp()
        # 10-avgustda 3 ta, 20-avgustda 2 ta, sentabrda 1 ta
        self.made = []
        for i, (day, status) in enumerate(
            [
                (date(2026, 8, 10), "pending"),
                (date(2026, 8, 10), "contact"),
                (date(2026, 8, 10), "active"),
                (date(2026, 8, 20), "active"),
                (date(2026, 8, 20), "pending"),
                (date(2026, 9, 5), "active"),
            ]
        ):
            s = Student.objects.create(
                name=f"O'quvchi{i}", surname="T", phone=f"+9989000001{i:02d}", status=status
            )
            Student.objects.filter(id=s.id).update(created_at=_created_at(day))
            self.made.append(s)

    def _overview(self, **params):
        return self.body(views.get_students_overview(self.get("/api/students/overview/", **params)))

    def test_range_filters_by_registration_day(self):
        data = self._overview(**{"from": "2026-08-10", "to": "2026-08-20"})
        self.assertEqual(data["count"], 5)
        self.assertEqual(data["summary"]["total"], 5)
        self.assertEqual(data["summary"]["pending"], 2)
        self.assertEqual(data["summary"]["contact"], 1)
        self.assertEqual(data["summary"]["active"], 2)
        self.assertEqual(data["range"], {"from": "2026-08-10", "to": "2026-08-20"})

    def test_boundary_days_are_included(self):
        self.assertEqual(self._overview(**{"from": "2026-08-10", "to": "2026-08-10"})["count"], 3)
        self.assertEqual(self._overview(**{"from": "2026-08-20", "to": "2026-08-20"})["count"], 2)

    def test_month_and_year_shortcuts(self):
        self.assertEqual(self._overview(month="2026-08")["count"], 5)
        self.assertEqual(self._overview(year="2026")["count"], 6)
        self.assertEqual(self._overview(year="2025")["count"], 0)

    def test_status_filter_narrows_list_but_not_summary(self):
        data = self._overview(**{"from": "2026-08-10", "to": "2026-08-20", "status": "pending"})
        self.assertEqual(data["count"], 2)
        # Davr statistikasi butun oraliqni ko'rsatib turishi kerak
        self.assertEqual(data["summary"]["total"], 5)

    def test_multiple_statuses(self):
        data = self._overview(**{"month": "2026-08", "status": "pending,contact"})
        self.assertEqual(data["count"], 3)

    def test_reversed_range_is_rejected(self):
        resp = views.get_students_overview(
            self.get("/api/students/overview/", **{"from": "2026-08-20", "to": "2026-08-10"})
        )
        self.assertEqual(resp.status_code, 400)

    def test_bad_format_is_rejected(self):
        resp = views.get_students_overview(
            self.get("/api/students/overview/", **{"from": "20-08-2026"})
        )
        self.assertEqual(resp.status_code, 400)

    def test_months_in_range_covers_partial_months(self):
        self.assertEqual(
            views.months_in_range(date(2026, 8, 10), date(2026, 10, 3)),
            ["2026-08", "2026-09", "2026-10"],
        )


# ─────────────────────────────────────────
# USTOZ STATISTIKASI VA TARIXI
# ─────────────────────────────────────────


class TeacherStatsTests(ApiCase):
    def setUp(self):
        super().setUp()
        self.teacher = Teacher.objects.create(name="Jasur", phone="+998901234567")
        self.other = Teacher.objects.create(name="Dilshod", phone="+998901234568")

        self.s1 = Student.objects.create(name="A", surname="A", phone="+99890000021",
                                         teacher=self.teacher)
        self.s2 = Student.objects.create(name="B", surname="B", phone="+99890000022",
                                         teacher=self.teacher)
        self.s3 = Student.objects.create(name="C", surname="C", phone="+99890000023",
                                         teacher=self.teacher, status="pending")
        Student.objects.create(name="D", surname="D", phone="+99890000024", teacher=self.other)

        # Avgust: s1 to'liq to'ladi, s2 yarim, s3 (kutayotgan) hisobsiz
        Payment.objects.create(student=self.s1, month="2026-08", stage=1,
                               amount_due=400000, paid_amount=400000, is_paid=True)
        Payment.objects.create(student=self.s2, month="2026-08", stage=1,
                               amount_due=400000, paid_amount=0)

    def _overview(self, **params):
        resp = views.get_teachers_overview(
            self.get("/api/teachers/overview/", format="full", **params)
        )
        return self.body(resp)

    def test_overview_counts_students_paid_and_money(self):
        row = next(t for t in self._overview(month="2026-08")["teachers"]
                   if t["id"] == self.teacher.id)
        self.assertEqual(row["students_count"], 3)
        self.assertEqual(row["active_count"], 2)
        self.assertEqual(row["pending_count"], 1)
        self.assertEqual(row["paid_students"], 1)
        self.assertEqual(row["unpaid_students"], 1)
        self.assertEqual(row["collected"], 400000)
        self.assertEqual(row["expected"], 800000)
        self.assertEqual(row["remaining"], 400000)
        self.assertEqual(row["collected_percent"], 50)

    def test_discount_lowers_expected(self):
        Payment.objects.filter(student=self.s2, month="2026-08").update(discount=100000)
        row = next(t for t in self._overview(month="2026-08")["teachers"]
                   if t["id"] == self.teacher.id)
        self.assertEqual(row["expected"], 700000)

    def test_default_response_stays_a_plain_list(self):
        """Eski frontend massiv kutadi — standart javob o'zgarmasligi kerak."""
        resp = views.get_teachers_overview(self.get("/api/teachers/overview/"))
        self.assertIsInstance(self.body(resp), list)

    def test_history_breaks_down_by_month_and_student(self):
        Payment.objects.create(student=self.s1, month="2026-09", stage=1,
                               amount_due=400000, paid_amount=200000)

        resp = views.get_teacher_history(
            self.get("/x", **{"from": "2026-08-01", "to": "2026-09-30"}),
            teacher_id=self.teacher.id,
        )
        data = self.body(resp)

        self.assertEqual([m["month"] for m in data["months"]], ["2026-08", "2026-09"])
        self.assertEqual(data["months"][0]["collected"], 400000)
        self.assertEqual(data["months"][1]["collected"], 200000)

        by_name = {s["name"]: s for s in data["students"]}
        self.assertEqual(by_name["A"]["collected"], 600000)
        self.assertEqual(by_name["A"]["months_paid"], 2)
        self.assertFalse(by_name["B"]["has_paid"])
        self.assertEqual(by_name["C"]["status_label"], "Kutilmoqda")

        self.assertEqual(data["totals"]["students"], 3)
        self.assertEqual(data["totals"]["paid_students"], 1)
        self.assertEqual(data["totals"]["pending_students"], 1)

    def test_history_lists_cash_entries_in_range(self):
        session = CashSession.objects.create(date=date(2026, 8, 12))
        entry = CashEntry.objects.create(
            session=session, student=self.s1, student_name="A A", amount=150000,
            month="2026-08",
        )
        CashEntry.objects.filter(id=entry.id).update(created_at=_created_at(date(2026, 8, 12)))

        old = CashEntry.objects.create(
            session=session, student=self.s1, student_name="A A", amount=90000,
            month="2026-07",
        )
        CashEntry.objects.filter(id=old.id).update(created_at=_created_at(date(2026, 7, 3)))

        data = self.body(
            views.get_teacher_history(
                self.get("/x", **{"from": "2026-08-01", "to": "2026-08-31"}),
                teacher_id=self.teacher.id,
            )
        )
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(data["totals"]["received_in_range"], 150000)

    def test_history_404_for_unknown_teacher(self):
        resp = views.get_teacher_history(self.get("/x"), teacher_id=9999)
        self.assertEqual(resp.status_code, 404)


# ─────────────────────────────────────────
# XONALAR
# ─────────────────────────────────────────


class RoomTests(ApiCase):
    def setUp(self):
        super().setUp()
        self.room = Room.objects.create(name="204-xona", capacity=12)

    def test_create_and_duplicate_name(self):
        resp = views.create_room(self.post("/api/rooms/create/", {"name": "205", "capacity": 10}))
        self.assertEqual(resp.status_code, 201)
        again = views.create_room(self.post("/api/rooms/create/", {"name": "205"}))
        self.assertEqual(again.status_code, 400)

    def test_group_gets_room_name_in_text_field(self):
        resp = views.create_group(
            self.post("/api/groups/create/", {"name": "PY-1", "room_id": self.room.id,
                                              "lesson_time": "10:00"})
        )
        self.assertEqual(resp.status_code, 201)
        group = Group.objects.get(name="PY-1")
        self.assertEqual(group.room_ref_id, self.room.id)
        # Eski ko'rinishlar `group.room` matnini o'qiydi
        self.assertEqual(group.room, "204-xona")

    def test_overlapping_lesson_in_same_room_is_blocked(self):
        views.create_group(
            self.post("/api/groups/create/", {"name": "PY-1", "room_id": self.room.id,
                                              "lesson_time": "10:00", "schedule": "odd"})
        )
        resp = views.create_group(
            self.post("/api/groups/create/", {"name": "PY-2", "room_id": self.room.id,
                                              "lesson_time": "11:00", "schedule": "odd"})
        )
        self.assertEqual(resp.status_code, 409)
        data = self.body(resp)
        self.assertEqual(data["code"], "room_busy")
        self.assertEqual(data["conflicts"][0]["name"], "PY-1")

    def test_non_overlapping_time_is_allowed(self):
        views.create_group(
            self.post("/api/groups/create/", {"name": "PY-1", "room_id": self.room.id,
                                              "lesson_time": "10:00", "schedule": "odd"})
        )
        resp = views.create_group(
            self.post("/api/groups/create/", {"name": "PY-2", "room_id": self.room.id,
                                              "lesson_time": "11:30", "schedule": "odd"})
        )
        self.assertEqual(resp.status_code, 201)

    def test_other_days_do_not_clash(self):
        views.create_group(
            self.post("/api/groups/create/", {"name": "PY-1", "room_id": self.room.id,
                                              "lesson_time": "10:00", "schedule": "odd"})
        )
        resp = views.create_group(
            self.post("/api/groups/create/", {"name": "PY-2", "room_id": self.room.id,
                                              "lesson_time": "10:00", "schedule": "even"})
        )
        self.assertEqual(resp.status_code, 201)

    def test_daily_group_clashes_with_every_schedule(self):
        views.create_group(
            self.post("/api/groups/create/", {"name": "Kunlik", "room_id": self.room.id,
                                              "lesson_time": "10:00", "schedule": "daily"})
        )
        resp = views.create_group(
            self.post("/api/groups/create/", {"name": "Toq", "room_id": self.room.id,
                                              "lesson_time": "10:00", "schedule": "odd"})
        )
        self.assertEqual(resp.status_code, 409)

    def test_force_saves_despite_conflict(self):
        views.create_group(
            self.post("/api/groups/create/", {"name": "PY-1", "room_id": self.room.id,
                                              "lesson_time": "10:00", "schedule": "odd"})
        )
        resp = views.create_group(
            self.post("/api/groups/create/", {"name": "PY-2", "room_id": self.room.id,
                                              "lesson_time": "10:00", "schedule": "odd",
                                              "force": True})
        )
        self.assertEqual(resp.status_code, 201)

    def test_renaming_room_updates_groups(self):
        views.create_group(
            self.post("/api/groups/create/", {"name": "PY-1", "room_id": self.room.id,
                                              "lesson_time": "10:00"})
        )
        views.update_room(self.patch_("/x", {"name": "204-A"}), room_id=self.room.id)
        self.assertEqual(Group.objects.get(name="PY-1").room, "204-A")

    def test_deleting_room_keeps_groups(self):
        views.create_group(
            self.post("/api/groups/create/", {"name": "PY-1", "room_id": self.room.id,
                                              "lesson_time": "10:00"})
        )
        resp = views.delete_room(self.rf.delete("/x", **self._headers()), room_id=self.room.id)
        self.assertEqual(resp.status_code, 200)
        group = Group.objects.get(name="PY-1")
        self.assertIsNone(group.room_ref_id)
        self.assertEqual(group.room, "")

    def test_free_text_room_matches_existing_room(self):
        views.create_group(
            self.post("/api/groups/create/", {"name": "PY-1", "room": "204-XONA",
                                              "lesson_time": "10:00"})
        )
        self.assertEqual(Group.objects.get(name="PY-1").room_ref_id, self.room.id)


# ─────────────────────────────────────────
# KURS DARAJALARI
# ─────────────────────────────────────────


class CourseLevelTests(ApiCase):
    def setUp(self):
        super().setUp()
        self.course = Course.objects.create(name="Ingliz tili", monthly_fee=400000)
        StagePrice.objects.create(stage=1, price=300000)
        self.student = Student.objects.create(name="Ali", surname="V", phone="+99890000031")

    def _add_level(self, name, fee=0):
        resp = views.create_course_level(
            self.post("/x", {"name": name, "monthly_fee": fee}), course_id=self.course.id
        )
        return resp, self.body(resp)

    def test_level_price_wins_over_course_price(self):
        _, level = self._add_level("Advanced", 550000)
        group = Group.objects.create(
            name="ENG-ADV", lesson_time=time(10, 0), course=self.course,
            level_id=level["id"],
        )
        group.students.add(self.student)
        self.assertEqual(views.effective_monthly_fee(self.student), 550000)

    def test_level_without_price_falls_back_to_course(self):
        _, level = self._add_level("Beginner")
        group = Group.objects.create(
            name="ENG-BEG", lesson_time=time(10, 0), course=self.course,
            level_id=level["id"],
        )
        group.students.add(self.student)
        self.assertEqual(views.effective_monthly_fee(self.student), 400000)
        self.assertEqual(level["effective_fee"], 400000)

    def test_duplicate_level_name_rejected(self):
        self._add_level("A1")
        resp, _ = self._add_level("A1")
        self.assertEqual(resp.status_code, 400)

    def test_level_price_change_syncs_future_payments(self):
        _, level = self._add_level("Advanced", 500000)
        group = Group.objects.create(
            name="ENG-ADV", lesson_time=time(10, 0), course=self.course,
            level_id=level["id"],
        )
        group.students.add(self.student)

        current = Payment.objects.create(student=self.student, month="2026-08",
                                         stage=1, amount_due=500000)
        future = Payment.objects.create(student=self.student, month="2026-09",
                                        stage=1, amount_due=500000)

        with _fixed_today():
            resp = views.update_course_level(
                self.patch_("/x", {"monthly_fee": 600000}), level_id=level["id"]
            )
        self.assertEqual(resp.status_code, 200)
        current.refresh_from_db()
        future.refresh_from_db()
        self.assertEqual(current.amount_due, 500000)  # joriy oy tarixiy
        self.assertEqual(future.amount_due, 600000)   # keyingi oy yangilandi

    def test_level_from_another_course_rejected(self):
        other = Course.objects.create(name="Matematika", monthly_fee=300000)
        level = CourseLevel.objects.create(course=other, name="Boshlang'ich")
        resp = views.create_group(
            self.post("/api/groups/create/", {"name": "X", "course_id": self.course.id,
                                              "level_id": level.id, "lesson_time": "10:00"})
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("tegishli emas", self.body(resp)["error"])

    def test_deleting_level_keeps_group(self):
        _, level = self._add_level("A1", 450000)
        group = Group.objects.create(
            name="ENG-A1", lesson_time=time(10, 0), course=self.course, level_id=level["id"]
        )
        resp = views.delete_course_level(
            self.rf.delete("/x", **self._headers()), level_id=level["id"]
        )
        self.assertEqual(resp.status_code, 200)
        group.refresh_from_db()
        self.assertIsNone(group.level_id)
        self.assertEqual(group.effective_monthly_fee, 400000)


# ─────────────────────────────────────────
# O'QUVCHILARNI IMPORT QILISH
# ─────────────────────────────────────────


class StudentImportTests(ApiCase):
    def setUp(self):
        super().setUp()
        self.teacher = Teacher.objects.create(name="Jasur", phone="+998901234567")
        self.group = Group.objects.create(
            name="PY-1", lesson_time=time(10, 0), teacher=self.teacher, schedule="even"
        )

    def _import(self, rows, **extra):
        resp = views.import_students(
            self.post("/api/students/import/", {"rows": rows, **extra})
        )
        return resp, self.body(resp)

    def test_dry_run_writes_nothing(self):
        _, data = self._import(
            [{"name": "Ali", "surname": "Valiyev", "phone": "+998900000041"}], dry_run=True
        )
        self.assertEqual(data["summary"]["created"], 1)
        self.assertTrue(data["summary"]["dry_run"])
        self.assertEqual(Student.objects.count(), 0)

    def test_real_run_creates_students(self):
        _, data = self._import(
            [
                {"name": "Ali", "surname": "Valiyev", "phone": "+998900000041"},
                {"name": "Vali", "surname": "Aliyev", "phone": "+998900000042"},
            ]
        )
        self.assertEqual(data["summary"]["created"], 2)
        self.assertEqual(Student.objects.count(), 2)

    def test_full_name_in_one_column_is_split(self):
        self._import([{"name": "Ali Valiyev Botirovich"}])
        s = Student.objects.get()
        self.assertEqual(s.name, "Ali")
        self.assertEqual(s.surname, "Valiyev Botirovich")

    def test_existing_phone_is_reported_not_duplicated(self):
        Student.objects.create(name="Ali", surname="V", phone="+998900000041")
        _, data = self._import([{"name": "Ali", "phone": "+998 90 000 00 41"}])
        self.assertEqual(data["summary"]["created"], 0)
        self.assertEqual(data["summary"]["duplicates"], 1)
        self.assertEqual(Student.objects.count(), 1)

    def test_phone_repeated_inside_the_sheet(self):
        _, data = self._import(
            [
                {"name": "Ali", "phone": "+998900000041"},
                {"name": "Ali", "phone": "+998900000041"},
            ]
        )
        self.assertEqual(data["summary"]["created"], 1)
        self.assertEqual(data["summary"]["duplicates"], 1)

    def test_missing_name_is_an_error(self):
        _, data = self._import([{"phone": "+998900000041"}])
        self.assertEqual(data["summary"]["errors"], 1)
        self.assertEqual(data["rows"][0]["reason"], "Ism ustuni bo'sh")

    def test_students_without_phone_are_allowed(self):
        _, data = self._import([{"name": "Ali"}, {"name": "Vali"}])
        self.assertEqual(data["summary"]["created"], 2)
        self.assertEqual(Student.objects.filter(phone__isnull=True).count(), 2)

    def test_teacher_and_group_matched_by_name(self):
        self._import([{"name": "Ali", "teacher_name": "jasur", "group_name": "py-1"}])
        s = Student.objects.get()
        self.assertEqual(s.teacher_id, self.teacher.id)
        self.assertEqual(list(s.groups.values_list("id", flat=True)), [self.group.id])
        # Guruhning jadvali o'quvchiga ko'chadi
        self.assertEqual(s.schedule, "even")

    def test_unknown_group_name_is_an_error(self):
        _, data = self._import([{"name": "Ali", "group_name": "yo'q-guruh"}])
        self.assertEqual(data["summary"]["errors"], 1)
        self.assertIn("topilmadi", data["rows"][0]["reason"])

    def test_default_status_applies_when_column_missing(self):
        self._import([{"name": "Ali"}], default_status="contact")
        self.assertEqual(Student.objects.get().status, "contact")

    def test_status_column_overrides_default(self):
        self._import([{"name": "Ali", "status": "active"}], default_status="pending")
        self.assertEqual(Student.objects.get().status, "active")

    def test_default_status_is_pending(self):
        self._import([{"name": "Ali"}])
        self.assertEqual(Student.objects.get().status, "pending")

    def test_group_teacher_used_when_row_has_none(self):
        self._import([{"name": "Ali", "group_name": "PY-1"}])
        self.assertEqual(Student.objects.get().teacher_id, self.teacher.id)

    def test_empty_rows_rejected(self):
        resp = views.import_students(self.post("/api/students/import/", {"rows": []}))
        self.assertEqual(resp.status_code, 400)

    def test_requires_permission(self):
        stranger = self.rf.post(
            "/api/students/import/",
            data=json.dumps({"rows": [{"name": "Ali"}]}),
            content_type="application/json",
        )
        self.assertEqual(views.import_students(stranger).status_code, 403)
