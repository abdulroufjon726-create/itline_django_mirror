"""Supermenejer bo'limi.

Bu yerdagi hamma narsa faqat supermenejerga ochiq: menejerlarni
yaratish va ularning vakolatlarini belgilash, ustoz oyliklari va
avanslar (ular avtomatik xarajatlarga tushadi), panelga kirgan
qurilmalar ro'yxati.
"""

import json

from django.db import models as db_models, transaction
from django.http import JsonResponse
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .access import (
    DEFAULT_PERMISSIONS,
    clean_permissions,
    find_manager_by_phone,
    permission_catalog,
    phone_key,
    require_super,
)
from .models import (
    Expense,
    LoginDevice,
    Manager,
    Student,
    Teacher,
    TeacherAdvance,
    TeacherSalary,
)


def _body(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return None


def _manager_row(m):
    return {
        "id": m.id,
        "name": m.name,
        "surname": m.surname,
        "phone": m.phone,
        "is_active": m.is_active,
        "is_super": m.is_super,
        "permissions": m.permissions or [],
        "created_at": m.created_at,
    }


# ─────────────────────────────────────────
# VAKOLATLAR
# ─────────────────────────────────────────


def get_permission_catalog(request):
    """Barcha mavjud vakolatlar — bo'limlarga ajratilgan holda."""
    denied = require_super(request)
    if denied:
        return denied
    return JsonResponse(
        {"sections": permission_catalog(), "defaults": DEFAULT_PERMISSIONS}
    )


def get_super_managers(request):
    """Menejerlar ro'yxati — vakolatlari bilan."""
    denied = require_super(request)
    if denied:
        return denied
    managers = Manager.objects.order_by("-is_super", "name", "surname")
    if request.GET.get("all") not in ("1", "true", "yes"):
        managers = managers.filter(is_active=True)
    return JsonResponse([_manager_row(m) for m in managers], safe=False)


@csrf_exempt
def create_super_managed_manager(request):
    """Yangi menejer — vakolatlari bilan birga yaratiladi.

    Menejer qo'shish endi faqat shu yerda; menejer panelidagi eski
    forma olib tashlangan.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied

    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    phone = (data.get("phone") or "").strip()
    password = data.get("password") or ""
    name = (data.get("name") or "").strip()

    if not name:
        return JsonResponse({"error": "Ism kiritilishi shart"}, status=400)
    if not phone:
        return JsonResponse({"error": "Telefon raqam kiritilishi shart"}, status=400)
    if not password:
        return JsonResponse({"error": "Parol kiritilishi shart"}, status=400)
    if find_manager_by_phone(phone, active_only=False):
        return JsonResponse(
            {"error": "Bu telefon raqam allaqachon ro'yxatdan o'tgan"}, status=400
        )

    permissions = data.get("permissions")
    permissions = (
        clean_permissions(permissions)
        if permissions is not None
        else list(DEFAULT_PERMISSIONS)
    )

    manager = Manager.objects.create(
        name=name,
        surname=(data.get("surname") or "").strip(),
        phone=phone,
        password=make_password(password),
        permissions=permissions,
    )
    return JsonResponse(_manager_row(manager), status=201)


@csrf_exempt
def update_manager_permissions(request, manager_id):
    """Menejerning vakolatlarini to'liq almashtiradi."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied

    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    manager = Manager.objects.filter(id=manager_id).first()
    if not manager:
        return JsonResponse({"error": "Menejer topilmadi"}, status=404)
    if manager.is_super:
        return JsonResponse(
            {"error": "Supermenejerning vakolatlari cheklanmaydi"}, status=400
        )

    manager.permissions = clean_permissions(data.get("permissions") or [])
    manager.save(update_fields=["permissions"])
    return JsonResponse(_manager_row(manager))


# ─────────────────────────────────────────
# QURILMALAR
# ─────────────────────────────────────────


def get_devices(request):
    """Panelga kirgan qurilmalar. ?role=manager — faqat menejerlar."""
    denied = require_super(request)
    if denied:
        return denied

    qs = LoginDevice.objects.select_related("manager")
    role = request.GET.get("role")
    if role == "manager":
        qs = qs.filter(role__in=["manager", "super"])
    elif role:
        qs = qs.filter(role=role)

    rows = [
        {
            "id": d.id,
            "device_id": d.device_id,
            "phone": d.phone,
            "role": d.role,
            "user_name": d.user_name,
            "manager_id": d.manager_id,
            "user_agent": d.user_agent,
            "ip": d.ip,
            "login_count": d.login_count,
            "is_blocked": d.is_blocked,
            "first_seen": d.first_seen,
            "last_seen": d.last_seen,
        }
        for d in qs[:500]
    ]
    return JsonResponse(rows, safe=False)


@csrf_exempt
def set_device_blocked(request, device_pk):
    """Qurilmani bloklash / blokdan chiqarish."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied

    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    device = LoginDevice.objects.filter(id=device_pk).first()
    if not device:
        return JsonResponse({"error": "Qurilma topilmadi"}, status=404)

    blocked = bool(data.get("is_blocked"))
    device.is_blocked = blocked
    device.blocked_at = timezone.now() if blocked else None
    device.save(update_fields=["is_blocked", "blocked_at"])
    return JsonResponse({"id": device.id, "is_blocked": device.is_blocked})


# ─────────────────────────────────────────
# USTOZ OYLIKLARI
# ─────────────────────────────────────────


def _current_month():
    return timezone.localdate().strftime("%Y-%m")


def _students_count_map():
    """Har ustozda nechta haqiqiy o'quvchi bor (ustoz profillari kirmaydi)."""
    return dict(
        Student.objects.filter(
            is_admin=False, is_excellence=False, is_graduate=False
        )
        .filter(teacher__isnull=False)
        .values_list("teacher_id")
        .annotate(n=db_models.Count("id"))
    )


def _salary_row(teacher, salary, students_count, advances):
    """Bitta ustozning shu oydagi oylik holati."""
    default_amount = (teacher.salary_per_student or 0) * students_count
    manual = salary.manual_amount if salary else None
    amount = default_amount if manual is None else manual

    advance_total = sum(a.amount for a in advances)
    # Avans allaqachon xarajatga tushgan — oy oxirida faqat qolgani
    # to'lanadi va faqat o'shasi xarajatga qo'shiladi
    remaining = max(0, amount - advance_total)

    return {
        "teacher_id": teacher.id,
        "teacher_name": teacher.name,
        "salary_per_student": teacher.salary_per_student or 0,
        "students_count": students_count,
        "default_amount": default_amount,
        "manual_amount": manual,
        "amount": amount,
        "advance_total": advance_total,
        "remaining": remaining,
        "is_paid": bool(salary and salary.is_paid),
        "paid_amount": salary.paid_amount if salary else 0,
        "paid_at": salary.paid_at if salary else None,
        "note": salary.note if salary else "",
        "advances": [
            {
                "id": a.id,
                "amount": a.amount,
                "note": a.note,
                "date": a.date,
            }
            for a in advances
        ],
    }


def get_salaries(request):
    """Tanlangan oy uchun barcha ustozlarning oyligi."""
    denied = require_super(request)
    if denied:
        return denied

    month = (request.GET.get("month") or "").strip() or _current_month()
    counts = _students_count_map()

    salaries = {
        s.teacher_id: s for s in TeacherSalary.objects.filter(month=month)
    }
    advances = {}
    for a in TeacherAdvance.objects.filter(month=month):
        advances.setdefault(a.teacher_id, []).append(a)

    rows = [
        _salary_row(
            t,
            salaries.get(t.id),
            counts.get(t.id, 0),
            advances.get(t.id, []),
        )
        for t in Teacher.objects.order_by("name")
    ]

    return JsonResponse(
        {
            "month": month,
            "rows": rows,
            "total_amount": sum(r["amount"] for r in rows),
            "total_paid": sum(r["paid_amount"] for r in rows),
            "total_advance": sum(r["advance_total"] for r in rows),
            "total_remaining": sum(
                r["remaining"] for r in rows if not r["is_paid"]
            ),
        }
    )


@csrf_exempt
def update_salary_rate(request, teacher_id):
    """Ustozning bir o'quvchi uchun stavkasini o'zgartiradi."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied

    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    teacher = Teacher.objects.filter(id=teacher_id).first()
    if not teacher:
        return JsonResponse({"error": "Ustoz topilmadi"}, status=404)

    try:
        rate = int(data.get("salary_per_student") or 0)
    except (TypeError, ValueError):
        return JsonResponse({"error": "Stavka noto'g'ri"}, status=400)
    if rate < 0:
        return JsonResponse({"error": "Stavka manfiy bo'lishi mumkin emas"}, status=400)

    teacher.salary_per_student = rate
    teacher.save(update_fields=["salary_per_student"])
    return JsonResponse({"teacher_id": teacher.id, "salary_per_student": rate})


@csrf_exempt
def set_salary_amount(request, teacher_id):
    """Shu oy uchun oylikni qo'lda belgilaydi (null — defaultga qaytadi)."""
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied

    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    teacher = Teacher.objects.filter(id=teacher_id).first()
    if not teacher:
        return JsonResponse({"error": "Ustoz topilmadi"}, status=404)

    month = (data.get("month") or "").strip() or _current_month()
    raw = data.get("manual_amount")
    if raw in (None, ""):
        manual = None
    else:
        try:
            manual = int(raw)
        except (TypeError, ValueError):
            return JsonResponse({"error": "Summa noto'g'ri"}, status=400)
        if manual < 0:
            return JsonResponse({"error": "Summa manfiy bo'lmaydi"}, status=400)

    salary, _created = TeacherSalary.objects.get_or_create(
        teacher=teacher, month=month
    )
    if salary.is_paid:
        return JsonResponse(
            {"error": "Oylik to'langan — avval to'lovni bekor qiling"}, status=400
        )

    salary.manual_amount = manual
    if "note" in data:
        salary.note = str(data.get("note") or "")[:255]
    salary.save()

    counts = _students_count_map()
    advances = list(TeacherAdvance.objects.filter(teacher=teacher, month=month))
    return JsonResponse(
        _salary_row(teacher, salary, counts.get(teacher.id, 0), advances)
    )


@csrf_exempt
@transaction.atomic
def pay_salary(request, teacher_id):
    """Oylikni "to'landi" deb belgilaydi va xarajatlarga yozadi.

    Avans sifatida oldindan olingan pul allaqachon xarajatga tushgan,
    shuning uchun bu yerda faqat qolgan summa yoziladi.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied

    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    teacher = Teacher.objects.filter(id=teacher_id).first()
    if not teacher:
        return JsonResponse({"error": "Ustoz topilmadi"}, status=404)

    month = (data.get("month") or "").strip() or _current_month()
    counts = _students_count_map()
    students_count = counts.get(teacher.id, 0)

    salary, _created = TeacherSalary.objects.get_or_create(
        teacher=teacher, month=month
    )
    if salary.is_paid:
        return JsonResponse({"error": "Bu oylik allaqachon to'langan"}, status=400)

    advances = list(TeacherAdvance.objects.filter(teacher=teacher, month=month))
    row = _salary_row(teacher, salary, students_count, advances)
    net = row["remaining"]

    if net <= 0:
        return JsonResponse(
            {"error": "To'lanadigan summa qolmagan (avans oylikni qoplagan)"},
            status=400,
        )

    expense = Expense.objects.create(
        title=f"Oylik — {teacher.name}",
        amount=net,
        category="salary",
        date=timezone.localdate(),
        note=f"{month} oyligi"
        + (f" (avans ayirilgan: {row['advance_total']})" if row["advance_total"] else ""),
    )

    salary.students_count = students_count
    salary.paid_amount = net
    salary.is_paid = True
    salary.paid_at = timezone.now()
    salary.expense = expense
    salary.save()

    return JsonResponse(
        _salary_row(teacher, salary, students_count, advances), status=201
    )


@csrf_exempt
@transaction.atomic
def unpay_salary(request, teacher_id):
    """To'lovni bekor qiladi — xarajat yozuvi ham o'chiriladi."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied

    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    month = (data.get("month") or "").strip() or _current_month()
    salary = TeacherSalary.objects.filter(
        teacher_id=teacher_id, month=month
    ).first()
    if not salary or not salary.is_paid:
        return JsonResponse({"error": "To'langan oylik topilmadi"}, status=404)

    if salary.expense_id:
        Expense.objects.filter(id=salary.expense_id).delete()

    salary.is_paid = False
    salary.paid_at = None
    salary.paid_amount = 0
    salary.expense = None
    salary.save()

    counts = _students_count_map()
    advances = list(
        TeacherAdvance.objects.filter(teacher_id=teacher_id, month=month)
    )
    return JsonResponse(
        _salary_row(
            salary.teacher, salary, counts.get(salary.teacher_id, 0), advances
        )
    )


@csrf_exempt
@transaction.atomic
def create_advance(request, teacher_id):
    """Ustoz oyligidan oldindan pul oldi — darhol xarajatlarga yoziladi."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied

    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    teacher = Teacher.objects.filter(id=teacher_id).first()
    if not teacher:
        return JsonResponse({"error": "Ustoz topilmadi"}, status=404)

    month = (data.get("month") or "").strip() or _current_month()
    try:
        amount = int(data.get("amount") or 0)
    except (TypeError, ValueError):
        return JsonResponse({"error": "Summa noto'g'ri"}, status=400)
    if amount <= 0:
        return JsonResponse({"error": "Summa 0 dan katta bo'lishi kerak"}, status=400)

    salary = TeacherSalary.objects.filter(teacher=teacher, month=month).first()
    if salary and salary.is_paid:
        return JsonResponse(
            {"error": "Bu oy oyligi to'langan — avans qo'shib bo'lmaydi"}, status=400
        )

    note = str(data.get("note") or "")[:255]
    expense = Expense.objects.create(
        title=f"Avans — {teacher.name}",
        amount=amount,
        category="salary",
        date=timezone.localdate(),
        note=(f"{month} oyligidan avans" + (f" · {note}" if note else "")),
    )
    advance = TeacherAdvance.objects.create(
        teacher=teacher,
        month=month,
        amount=amount,
        note=note,
        date=timezone.localdate(),
        expense=expense,
    )

    counts = _students_count_map()
    advances = list(TeacherAdvance.objects.filter(teacher=teacher, month=month))
    return JsonResponse(
        {
            "advance_id": advance.id,
            **_salary_row(teacher, salary, counts.get(teacher.id, 0), advances),
        },
        status=201,
    )


@csrf_exempt
@transaction.atomic
def delete_advance(request, advance_id):
    """Avansni o'chiradi — bog'liq xarajat ham o'chadi."""
    if request.method != "DELETE":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied

    advance = TeacherAdvance.objects.filter(id=advance_id).first()
    if not advance:
        return JsonResponse({"error": "Avans topilmadi"}, status=404)

    salary = TeacherSalary.objects.filter(
        teacher_id=advance.teacher_id, month=advance.month
    ).first()
    if salary and salary.is_paid:
        return JsonResponse(
            {"error": "Oylik to'langan — avval to'lovni bekor qiling"}, status=400
        )

    if advance.expense_id:
        Expense.objects.filter(id=advance.expense_id).delete()
    teacher = advance.teacher
    month = advance.month
    advance.delete()

    counts = _students_count_map()
    advances = list(TeacherAdvance.objects.filter(teacher=teacher, month=month))
    return JsonResponse(
        _salary_row(teacher, salary, counts.get(teacher.id, 0), advances)
    )
