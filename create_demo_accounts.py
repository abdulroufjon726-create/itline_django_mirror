"""Ertagalik ko'rgazma uchun demo hisoblar (PROD bazada).

Yaratiladi / yangilanadi:
  - Manager  (supermenejer)  — +998 999 888 777 / Demo2026!   (barcha vakolat)
  - Manager  (menejer)       — +998 977 010 203 / Manager2026! (panelga kiradi)
  - Teacher  (ustoz)         — +998 977 010 404 / Teacher2026!
  - Student  (o'quvchi)      — +998 977 010 505 / Student2026!

Har biri o'z guruhiga bog'lanadi; guruh bo'lmasa "Demo Guruhi" ochiladi.

Ishga tushirish:  python create_demo_accounts.py
"""
import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django.db.utils

with open("prod_db_url.tmp") as f:
    os.environ["DATABASE_URL"] = f.read().strip()

django.setup()

from django.contrib.auth.hashers import make_password

from register_withvue.models import Group, Manager, Student, Teacher

REPORT = "demo_accounts.txt"

accounts = []


def note(line):
    print(line)
    accounts.append(line)


# ── 1. Supermenejer ──────────────────────────────────────────
super_m, created = Manager.objects.update_or_create(
    phone="+998999888777",
    defaults={
        "name": "Demo Super",
        "password": make_password("Demo2026!"),
        "is_super": True,
    },
)
note(f"supermenejer | +998 999 888 777 | Demo2026! | {'yaratildi' if created else 'yangilandi'}")

# ── 2. Oddiy menejer ─────────────────────────────────────────
mgr, created = Manager.objects.update_or_create(
    phone="+998977010203",
    defaults={
        "name": "Demo Menejer",
        "password": make_password("Manager2026!"),
        "is_super": False,
    },
)
note(f"menejer | +998 977 010 203 | Manager2026! | {'yaratildi' if created else 'yangilandi'}")

# ── 3. Guruh ─────────────────────────────────────────────────
# Guruh maydonlarini modeldan o'qib xavfsiz yaratamiz
group = Group.objects.filter(name="Demo Guruhi").first()
if not group:
    from django.db import models as djm

    fields = {f.name: f for f in Group._meta.fields}
    kwargs = {"name": "Demo Guruhi"}
    if "teacher" in fields:
        kwargs["teacher"] = None  # keyin bog'lanadi
    group = Group.objects.create(**kwargs)
note(f"guruh | Demo Guruhi | id={group.id} | mavjud")

# ── 4. Ustoz ─────────────────────────────────────────────────
teacher, created = Teacher.objects.update_or_create(
    phone="+998977010404",
    defaults={
        "name": "Demo",
        "password": make_password("Teacher2026!"),
        "is_senior": False,
        "salary_mode": "per_student",
        "salary_per_student": 50000,
    },
)
note(f"ustoz | +998 977 010 404 | Teacher2026! | {'yaratildi' if created else 'yangilandi'}")

# Ustozni guruhga bog'lash (maydon nomi turlicha bo'lishi mumkin)
gfields = {f.name for f in Group._meta.fields}
if "teacher" in gfields:
    group.teacher = teacher
    group.save()

# ── 5. O'quvchi ──────────────────────────────────────────────
student, created = Student.objects.update_or_create(
    phone="+998977010505",
    defaults={
        "name": "Demo",
        "surname": "O'quvchi",
        "password": make_password("Student2026!"),
        "initial_password": "",  # ochiq parol saqlanmaydi
        "status": "active",
        "teacher": teacher,
        "coin_balance": 0,
    },
)
# Guruhga M2M orqali bog'lash
if hasattr(student, "groups"):
    student.groups.add(group)
note(f"o'quvchi | +998 977 010 505 | Student2026! | {'yaratildi' if created else 'yangilandi'}")

note(f"guruh a'zosi: {student.name} -> {[g.name for g in student.groups.all()]}")

with open(REPORT, "w", encoding="utf-8") as f:
    f.write("\n".join(accounts) + "\n")
print(f"\n{REPORT} ga yozildi")
