"""Supermenejer bo'limi.

Bu yerdagi hamma narsa faqat supermenejerga ochiq: menejerlarni
yaratish va ularning vakolatlarini belgilash, ustoz oyliklari va
avanslar (ular avtomatik xarajatlarga tushadi), panelga kirgan
qurilmalar ro'yxati.
"""

import json
from datetime import timedelta

from django.db import models as db_models, transaction
from django.http import JsonResponse
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .access import (
    ACTIONS,
    DEFAULT_PERMISSIONS,
    action_catalog,
    clean_permissions,
    find_manager_by_phone,
    log_action,
    permission_catalog,
    phone_key,
    require_super,
)
from .models import (
    ActivityLog,
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
    log_action(
        request,
        "manager.create",
        f"{manager.name} {manager.surname} menejer qilib qo'shildi — "
        f"{len(permissions)} ta vakolat",
        target_type="manager",
        target_id=manager.id,
        target_name=f"{manager.name} {manager.surname}".strip(),
        permissions=permissions,
    )
    return JsonResponse(_manager_row(manager), status=201)


@csrf_exempt
def set_manager_password(request, manager_id):
    """Menejerning parolini almashtiradi.

    Supermenejer menejer parolini unutgan yoki xodim ketgan holatda
    tiklay olishi kerak. Eski parol so'ralmaydi — bu supermenejerning
    vakolati. Supermenejer akkauntlariga tegilmaydi.
    """
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
            {"error": "Supermenejer parolini bu yerdan o'zgartirib bo'lmaydi"},
            status=400,
        )

    password = (data.get("password") or "").strip()
    if len(password) < 4:
        return JsonResponse(
            {"error": "Parol kamida 4 belgidan iborat bo'lishi kerak"}, status=400
        )

    manager.password = make_password(password)
    manager.save(update_fields=["password"])
    log_action(
        request,
        "manager.password",
        f"{manager.name} {manager.surname} paroli almashtirildi".strip(),
        target_type="manager",
        target_id=manager.id,
        target_name=f"{manager.name} {manager.surname}".strip(),
    )
    return JsonResponse({"message": "Parol yangilandi", "id": manager.id})


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

    before = set(manager.permissions or [])
    manager.permissions = clean_permissions(data.get("permissions") or [])
    manager.save(update_fields=["permissions"])

    after = set(manager.permissions)
    added, removed = sorted(after - before), sorted(before - after)
    parts = []
    if added:
        parts.append(f"+{len(added)}")
    if removed:
        parts.append(f"−{len(removed)}")
    change = ", ".join(parts) or "o'zgarishsiz"
    log_action(
        request,
        "manager.permissions",
        f"{manager.name} vakolatlari: {change} (jami {len(after)})",
        target_type="manager",
        target_id=manager.id,
        target_name=f"{manager.name} {manager.surname}".strip(),
        added=added,
        removed=removed,
    )
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
    log_action(
        request,
        "device.block",
        f"{device.user_name or device.phone} qurilmasi "
        + ("bloklandi" if blocked else "blokdan chiqarildi"),
        target_type="device",
        target_id=device.id,
        target_name=device.user_name or device.phone,
        is_blocked=blocked,
    )
    return JsonResponse({"id": device.id, "is_blocked": device.is_blocked})


# ─────────────────────────────────────────
# BOSH SAHIFA
# ─────────────────────────────────────────


def get_overview(request):
    """Supermenejer bosh sahifasi — bir qarashda butun markaz holati."""
    denied = require_super(request)
    if denied:
        return denied

    from .models import Payment, PaymentRequest

    month = (request.GET.get("month") or "").strip() or _current_month()
    now = timezone.now()

    students = Student.objects.filter(
        is_admin=False, is_excellence=False, is_graduate=False
    )
    payments = Payment.objects.filter(month=month)

    collected = sum(p.paid_amount or 0 for p in payments)
    expected = sum(max(0, (p.amount_due or 0) - (p.discount or 0)) for p in payments)

    try:
        year, mon = month.split("-")
        expenses = Expense.objects.filter(date__year=int(year), date__month=int(mon))
    except ValueError:
        expenses = Expense.objects.none()
    spent = sum(e.amount or 0 for e in expenses)

    # Oyliklar: shu oy uchun to'lanmagan ustozlar
    counts = _students_count_map()
    salaries = {s.teacher_id: s for s in TeacherSalary.objects.filter(month=month)}
    unpaid_salaries = 0
    for t in Teacher.objects.all():
        s = salaries.get(t.id)
        if s and s.is_paid:
            continue
        manual = s.manual_amount if s else None
        amount = (
            (t.salary_per_student or 0) * counts.get(t.id, 0)
            if manual is None
            else manual
        )
        if amount > 0:
            unpaid_salaries += amount

    return JsonResponse(
        {
            "month": month,
            "students": students.count(),
            "teachers": Teacher.objects.count(),
            "groups": _group_count(),
            "managers": Manager.objects.filter(is_active=True, is_super=False).count(),
            "collected": collected,
            "expected": expected,
            "spent": spent,
            "profit": collected - spent,
            "unpaid_salaries": unpaid_salaries,
            "pending_requests": PaymentRequest.objects.filter(
                status="pending"
            ).count(),
            "devices_total": LoginDevice.objects.count(),
            "devices_blocked": LoginDevice.objects.filter(is_blocked=True).count(),
            "activity_today": ActivityLog.objects.filter(
                created_at__gte=now - timedelta(days=1)
            ).count(),
        }
    )


def _group_count():
    from .models import Group

    return Group.objects.count()


# ─────────────────────────────────────────
# HARAKATLAR JURNALI
# ─────────────────────────────────────────


def get_activity(request):
    """Panelda kim nima qilgani.

    Filtrlar: ?manager_id= ?action= ?days= ?search= ?limit= ?before_id=
    `before_id` — "yana yuklash" uchun: shu ID'dan eskiroqlari qaytadi.
    """
    denied = require_super(request)
    if denied:
        return denied

    qs = ActivityLog.objects.all()

    manager_id = request.GET.get("manager_id")
    if manager_id:
        try:
            qs = qs.filter(manager_id=int(manager_id))
        except ValueError:
            return JsonResponse({"error": "manager_id noto'g'ri"}, status=400)

    action = request.GET.get("action")
    if action:
        # "payment" kabi prefiks ham ishlaydi — butun bo'lim bo'yicha filtr
        qs = qs.filter(action=action) if "." in action else qs.filter(
            action__startswith=f"{action}."
        )

    days = request.GET.get("days")
    if days:
        try:
            since = timezone.now() - timedelta(days=int(days))
            qs = qs.filter(created_at__gte=since)
        except ValueError:
            return JsonResponse({"error": "days noto'g'ri"}, status=400)

    search = (request.GET.get("search") or "").strip()
    if search:
        qs = qs.filter(
            db_models.Q(description__icontains=search)
            | db_models.Q(actor_name__icontains=search)
            | db_models.Q(target_name__icontains=search)
        )

    before_id = request.GET.get("before_id")
    if before_id:
        try:
            qs = qs.filter(id__lt=int(before_id))
        except ValueError:
            return JsonResponse({"error": "before_id noto'g'ri"}, status=400)

    try:
        limit = min(200, max(1, int(request.GET.get("limit") or 60)))
    except ValueError:
        limit = 60

    rows = [
        {
            "id": a.id,
            "actor_name": a.actor_name,
            "actor_phone": a.actor_phone,
            "actor_role": a.actor_role,
            "manager_id": a.manager_id,
            "action": a.action,
            "action_label": ACTIONS.get(a.action, a.action),
            "description": a.description,
            "target_type": a.target_type,
            "target_id": a.target_id,
            "target_name": a.target_name,
            "meta": a.meta,
            "ip": a.ip,
            "created_at": a.created_at,
        }
        for a in qs.select_related("manager")[:limit]
    ]

    return JsonResponse(
        {
            "rows": rows,
            "actions": action_catalog(),
            "has_more": len(rows) == limit,
        }
    )


def get_activity_summary(request):
    """Bosh sahifa uchun: kim nechta amal qilgan (oxirgi N kun)."""
    denied = require_super(request)
    if denied:
        return denied

    try:
        days = int(request.GET.get("days") or 7)
    except ValueError:
        days = 7
    since = timezone.now() - timedelta(days=days)

    recent = ActivityLog.objects.filter(created_at__gte=since)

    by_actor = (
        recent.exclude(actor_name="")
        .values("actor_name", "actor_role", "manager_id")
        .annotate(n=db_models.Count("id"))
        .order_by("-n")[:10]
    )
    by_action = (
        recent.values("action").annotate(n=db_models.Count("id")).order_by("-n")[:8]
    )

    return JsonResponse(
        {
            "days": days,
            "total": recent.count(),
            "by_actor": [
                {
                    "name": r["actor_name"],
                    "role": r["actor_role"],
                    "manager_id": r["manager_id"],
                    "count": r["n"],
                }
                for r in by_actor
            ],
            "by_action": [
                {
                    "action": r["action"],
                    "label": ACTIONS.get(r["action"], r["action"]),
                    "count": r["n"],
                }
                for r in by_action
            ],
        }
    )


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


def _collected_map(month):
    """Har ustozning o'quvchilaridan shu oy yig'ilgan pul.

    Foizli oylik shu summadan hisoblanadi — ya'ni ustoz o'zi olib
    kelgan tushumdan ulush oladi.
    """
    from .models import Payment

    rows = (
        Payment.objects.filter(month=month, student__teacher__isnull=False)
        .values_list("student__teacher_id")
        .annotate(total=db_models.Sum("paid_amount"))
    )
    return {tid: (total or 0) for tid, total in rows}


def _salary_row(teacher, salary, students_count, advances, collected=0):
    """Bitta ustozning shu oydagi oylik holati."""
    mode = teacher.salary_mode or "per_student"
    percent = float(teacher.salary_percent or 0)

    if mode == "percent":
        default_amount = int(round(collected * percent / 100))
    else:
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
        "salary_mode": mode,
        "salary_per_student": teacher.salary_per_student or 0,
        "salary_percent": percent,
        "collected": collected,
        "students_count": students_count,
        "default_amount": default_amount,
        # Stavka ham, foiz ham qo'yilmagan bo'lsa oylik 0 bo'lib
        # ko'rinadi — panel buni "sozlanmagan" deb ko'rsatadi
        "is_configured": bool(
            teacher.salary_per_student if mode == "per_student" else percent
        ),
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

    collected = _collected_map(month)

    rows = [
        _salary_row(
            t,
            salaries.get(t.id),
            counts.get(t.id, 0),
            advances.get(t.id, []),
            collected.get(t.id, 0),
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


def _apply_salary_settings(teacher, data):
    """Oylik sozlamalarini qo'llaydi. Xato bo'lsa matn qaytaradi."""
    if "salary_mode" in data:
        mode = str(data.get("salary_mode") or "").strip()
        if mode not in dict(Teacher.SALARY_MODE_CHOICES):
            return "Oylik usuli noto'g'ri"
        teacher.salary_mode = mode

    if "salary_per_student" in data:
        try:
            rate = int(data.get("salary_per_student") or 0)
        except (TypeError, ValueError):
            return "Stavka noto'g'ri"
        if rate < 0:
            return "Stavka manfiy bo'lishi mumkin emas"
        teacher.salary_per_student = rate

    if "salary_percent" in data:
        try:
            percent = round(float(data.get("salary_percent") or 0), 2)
        except (TypeError, ValueError):
            return "Foiz noto'g'ri"
        if percent < 0 or percent > 100:
            return "Foiz 0 va 100 orasida bo'lishi kerak"
        teacher.salary_percent = percent

    return None


@csrf_exempt
def update_salary_rate(request, teacher_id):
    """Ustozning oylik sozlamalari — usul, stavka yoki foiz."""
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

    error = _apply_salary_settings(teacher, data)
    if error:
        return JsonResponse({"error": error}, status=400)

    teacher.save(
        update_fields=["salary_mode", "salary_per_student", "salary_percent"]
    )

    month = (data.get("month") or "").strip() or _current_month()
    salary = TeacherSalary.objects.filter(teacher=teacher, month=month).first()
    advances = list(TeacherAdvance.objects.filter(teacher=teacher, month=month))
    return JsonResponse(
        _salary_row(
            teacher,
            salary,
            _students_count_map().get(teacher.id, 0),
            advances,
            _collected_map(month).get(teacher.id, 0),
        )
    )


@csrf_exempt
def bulk_salary_settings(request):
    """Bir xil sozlamani bir nechta (yoki barcha) ustozga qo'yadi.

    13 ta ustozga bitta-bitta stavka kiritish zerikarli — ko'pincha
    hammasiga bir xil qiymat beriladi, keyin ayrimlari o'zgartiriladi.

    Body: {teacher_ids?: [...], salary_mode?, salary_per_student?,
           salary_percent?}. `teacher_ids` berilmasa hammasiga.
    """
    if request.method != "PATCH":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    denied = require_super(request)
    if denied:
        return denied

    data = _body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    ids = data.get("teacher_ids")
    qs = Teacher.objects.all()
    if ids:
        try:
            qs = qs.filter(id__in=[int(x) for x in ids])
        except (TypeError, ValueError):
            return JsonResponse({"error": "teacher_ids noto'g'ri"}, status=400)

    teachers = list(qs)
    if not teachers:
        return JsonResponse({"error": "Ustoz topilmadi"}, status=404)

    for teacher in teachers:
        error = _apply_salary_settings(teacher, data)
        if error:
            return JsonResponse({"error": error}, status=400)

    Teacher.objects.bulk_update(
        teachers, ["salary_mode", "salary_per_student", "salary_percent"]
    )

    first = teachers[0]
    detail = (
        f"{first.salary_percent}%"
        if first.salary_mode == "percent"
        else f"{first.salary_per_student:,} so'm/o'quvchi".replace(",", " ")
    )
    log_action(
        request,
        "salary.settings",
        f"{len(teachers)} ta ustozga oylik sozlamasi qo'yildi: {detail}",
        target_type="teacher",
        target_name=f"{len(teachers)} ustoz",
        count=len(teachers),
    )

    return JsonResponse({"updated": len(teachers)})


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
        _salary_row(
            teacher, salary, counts.get(teacher.id, 0), advances,
            _collected_map(month).get(teacher.id, 0),
        )
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
    collected = _collected_map(month).get(teacher.id, 0)
    row = _salary_row(teacher, salary, students_count, advances, collected)
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

    log_action(
        request,
        "salary.pay",
        f"{teacher.name} — {month} oyligi {net:,} so'm to'landi".replace(",", " "),
        target_type="teacher",
        target_id=teacher.id,
        target_name=teacher.name,
        month=month,
        amount=net,
        advance_total=row["advance_total"],
    )

    return JsonResponse(
        _salary_row(teacher, salary, students_count, advances, collected),
        status=201,
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

    log_action(
        request,
        "salary.unpay",
        f"{salary.teacher.name} — {month} oylik to'lovi bekor qilindi, "
        "xarajat yozuvi ham o'chirildi",
        target_type="teacher",
        target_id=salary.teacher_id,
        target_name=salary.teacher.name,
        month=month,
    )

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
    log_action(
        request,
        "salary.advance",
        f"{teacher.name} — {month} oyligidan {amount:,} so'm avans".replace(",", " ")
        + (f" ({note})" if note else ""),
        target_type="teacher",
        target_id=teacher.id,
        target_name=teacher.name,
        month=month,
        amount=amount,
    )

    counts = _students_count_map()
    advances = list(TeacherAdvance.objects.filter(teacher=teacher, month=month))
    return JsonResponse(
        {
            "advance_id": advance.id,
            **_salary_row(
            teacher, salary, counts.get(teacher.id, 0), advances,
            _collected_map(month).get(teacher.id, 0),
        ),
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
        _salary_row(
            teacher, salary, counts.get(teacher.id, 0), advances,
            _collected_map(month).get(teacher.id, 0),
        )
    )
