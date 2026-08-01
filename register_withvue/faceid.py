"""Yuz tanish terminali bilan ishlash (Hikvision DS-K1T3xx).

Ikki yo'nalish bor:

1. Terminal → server ("HTTP listening"). Terminal yuzni taniganda
   hodisani shu yerdagi webhook'ga yuboradi, biz davomatni belgilaymiz.
   Bu asosiy yo'l va u lokal tarmoqda ham ishlaydi — terminal
   internetga chiqa olsa bo'ldi.

2. Server → terminal (ISAPI). O'quvchi va uning rasmini terminalga
   yuborish. Bu faqat terminalga tashqaridan kirish mumkin bo'lganda
   ishlaydi (statik IP / port forwarding), shuning uchun ixtiyoriy.
"""

import base64
import json
import logging

from django.utils import timezone

from .models import Attendance, FaceDevice, FaceEvent, Group, Lesson, Student

log = logging.getLogger(__name__)

# Dars boshlanganidan keyin shu daqiqagacha kelgani "Keldi" hisoblanadi
DEFAULT_GRACE_MINUTES = 15

ODD_DAYS = {0, 2, 4}
EVEN_DAYS = {1, 3, 5}


# ─────────────────────────────────────────
# TERMINAL → SERVER
# ─────────────────────────────────────────


def parse_event(request):
    """Hikvision yuborgan hodisadan kerakli maydonlarni ajratadi.

    Terminal proshivkasiga qarab ikki xil yuboradi: toza JSON yoki
    `multipart/form-data` (ichida `event_log` nomli JSON qism va
    ixtiyoriy rasm). Ikkalasi ham qo'llab-quvvatlanadi.

    Qaytaradi: (ma'lumot_dict, xato_matni). Xato bo'lsa birinchisi None.
    """
    raw = None

    # multipart — qism nomi proshivkada har xil bo'lishi mumkin
    if request.content_type and "multipart" in request.content_type:
        for key in ("event_log", "Event_log", "eventLog"):
            if key in request.POST:
                raw = request.POST[key]
                break
        if raw is None:
            for key, f in request.FILES.items():
                if "log" in key.lower() or "json" in key.lower():
                    raw = f.read().decode("utf-8", "ignore")
                    break
    else:
        raw = (request.body or b"").decode("utf-8", "ignore")

    if not raw:
        return None, "Hodisa ma'lumoti topilmadi"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, "JSON o'qib bo'lmadi"

    ev = data.get("AccessControllerEvent") or {}

    # Shaxs raqami — proshivkada nomi turlicha
    person_id = (
        ev.get("employeeNoString")
        or ev.get("employeeNo")
        or data.get("employeeNoString")
        or ""
    )
    person_id = str(person_id).strip()

    return {
        "person_id": person_id,
        "person_name": str(ev.get("name") or "").strip()[:200],
        "serial": str(ev.get("serialNo") or data.get("macAddress") or "").strip()[:64],
        "device_name": str(ev.get("deviceName") or "").strip(),
        "happened_at": _parse_time(data.get("dateTime")),
        "major": ev.get("majorEventType"),
        "minor": ev.get("subEventType"),
        "verify_mode": str(ev.get("currentVerifyMode") or "").strip(),
    }, None


def _parse_time(value):
    """Terminal vaqtini o'qiydi; o'qib bo'lmasa hozirgi vaqt."""
    if not value:
        return timezone.now()
    from django.utils.dateparse import parse_datetime

    try:
        dt = parse_datetime(str(value))
    except (ValueError, TypeError):
        dt = None
    if dt is None:
        return timezone.now()
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def is_access_granted(info):
    """Bu hodisa "yuz tanildi va ruxsat berildi" degani mi.

    Hikvision'da majorEventType=5 — "Event" turkumi, subEventType=75
    "yuz bo'yicha tasdiqlandi". Eshik ochilmadi / notanish yuz kabi
    hodisalar boshqa kodlar bilan keladi va davomatga tegmaydi.

    Proshivkalar orasida kodlar farq qilishi mumkin, shuning uchun
    shaxs raqami bor bo'lsa ham tan olamiz — raqamsiz hodisa
    baribir hech kimga bog'lanmaydi.
    """
    if not info.get("person_id"):
        return False
    major, minor = info.get("major"), info.get("minor")
    if major == 5 and minor in (1, 38, 75, 76):
        return True
    # Kodlar noma'lum bo'lsa ham raqam bor — o'tkazamiz
    return major is None or minor is None


# ─────────────────────────────────────────
# DAVOMAT BELGILASH
# ─────────────────────────────────────────


def group_has_lesson_today(group, day):
    """Guruh jadvali bo'yicha bugun dars bormi."""
    weekday = day.weekday()
    if group.schedule == "daily":
        return weekday != 6  # yakshanba dam
    if group.schedule == "odd":
        return weekday in ODD_DAYS
    if group.schedule == "even":
        return weekday in EVEN_DAYS
    return False


def student_group_for_today(student, day):
    """O'quvchining bugun darsi bor guruhi.

    Bir nechta guruhda bo'lsa bugun darsi borini tanlaymiz — aks
    holda ikkinchi guruhning davomati birinchisiga yozilib ketardi.
    """
    groups = list(student.groups.all())
    if not groups:
        return None
    today = [g for g in groups if group_has_lesson_today(g, day)]
    if not today:
        return None
    # Bir nechta bo'lsa — darsi eng erta boshlanadigani
    return min(today, key=lambda g: (g.lesson_time or timezone.now().time(), g.id))


def decide_status(group, when, grace_minutes=DEFAULT_GRACE_MINUTES):
    """Kelgan vaqtga qarab "Keldi" yoki "Kech keldi"."""
    lesson_time = group.lesson_time
    if not lesson_time:
        return "present"

    local = timezone.localtime(when)
    arrived = local.hour * 60 + local.minute
    starts = lesson_time.hour * 60 + lesson_time.minute
    return "present" if arrived <= starts + grace_minutes else "late"


def mark_attendance(student, info):
    """Yuz tanilgan o'quvchiga davomat qo'yadi.

    Qaytaradi: (status_kaliti, izoh, attendance_yoki_None).

    Coin berish mavjud `update_attendance` mantig'idan foydalanadi —
    ikki joyda ikki xil hisoblanib qolmasligi uchun.
    """
    # Aylanma importni oldini olish uchun shu yerda
    from .views import ATTENDANCE_REASON, apply_coin_transaction, get_attendance_coins_map

    when = info["happened_at"]
    day = timezone.localtime(when).date()

    group = student_group_for_today(student, day)
    if group is None:
        if not student.groups.exists():
            return "no_group", "O'quvchi guruhga biriktirilmagan", None
        return "no_lesson", "Bu guruhda bugun dars yo'q", None

    lesson, _ = Lesson.objects.get_or_create(
        group=group,
        date=day,
        defaults={"title": group.name, "teacher": group.teacher},
    )

    attendance, created = Attendance.objects.get_or_create(
        student=student, lesson=lesson, defaults={"status": "absent"}
    )

    new_status = decide_status(group, when)

    # Ustoz allaqachon qo'lda belgilagan bo'lsa ustidan yozmaymiz —
    # odam ko'rgani terminaldan ishonchliroq
    if not created and attendance.status != "absent":
        return (
            "already",
            f"Allaqachon belgilangan: {attendance.get_status_display()}",
            attendance,
        )

    coins = get_attendance_coins_map()
    old_status = attendance.status

    if old_status in coins:
        apply_coin_transaction(
            student,
            -coins[old_status],
            ATTENDANCE_REASON.get(old_status, "manual"),
            note=f"'{old_status}' bekor qilindi (yuz tanish)",
            attendance=attendance,
        )
    if new_status in coins:
        apply_coin_transaction(
            student,
            coins[new_status],
            ATTENDANCE_REASON.get(new_status, "manual"),
            note=f"Yuz tanish: {new_status}",
            attendance=attendance,
        )

    attendance.status = new_status
    attendance.save(update_fields=["status"])

    label = "Keldi" if new_status == "present" else "Kech keldi"
    return "marked", f"{group.name} — {label}", attendance


def handle_event(device, info):
    """Hodisani qayta ishlaydi va FaceEvent yozuvini qaytaradi."""
    student = None
    status, note, attendance = "unknown", "Bu raqam hech kimga bog'lanmagan", None

    if info["person_id"]:
        student = Student.objects.filter(
            face_person_id=info["person_id"]
        ).first()

    if student:
        try:
            status, note, attendance = mark_attendance(student, info)
        except Exception as exc:  # noqa: BLE001 — hodisa baribir yozilsin
            log.exception("Yuz tanish davomati belgilanmadi")
            status, note = "ignored", f"Xato: {exc}"[:255]

    event = FaceEvent.objects.create(
        device=device,
        person_id=info["person_id"][:32],
        person_name=info["person_name"],
        student=student,
        attendance=attendance,
        status=status,
        note=note[:255],
        happened_at=info["happened_at"],
    )

    if device:
        FaceDevice.objects.filter(pk=device.pk).update(last_event_at=timezone.now())

    return event


# ─────────────────────────────────────────
# SERVER → TERMINAL (ISAPI)
# ─────────────────────────────────────────


def push_student(device, student, photo_b64=""):
    """O'quvchini (va rasmi bo'lsa yuzini) terminalga yozadi.

    Faqat terminal manzili sozlangan bo'lsa ishlaydi. Qaytaradi:
    (muvaffaqiyat, xabar).
    """
    if not device.can_push:
        return False, "Terminal manzili sozlanmagan — yuzni terminalning o'zida yozing"
    if not student.face_person_id:
        return False, "O'quvchiga terminal raqami berilmagan"

    try:
        import requests
        from requests.auth import HTTPDigestAuth
    except ImportError:  # pragma: no cover
        return False, "requests kutubxonasi yo'q"

    auth = HTTPDigestAuth(device.username, device.password)
    base = device.host.rstrip("/")
    name = f"{student.name} {student.surname}".strip()[:32]

    try:
        res = requests.post(
            f"{base}/ISAPI/AccessControl/UserInfo/Record?format=json",
            auth=auth,
            timeout=15,
            json={
                "UserInfo": {
                    "employeeNo": student.face_person_id,
                    "name": name,
                    "userType": "normal",
                    "Valid": {
                        "enable": True,
                        "beginTime": "2020-01-01T00:00:00",
                        "endTime": "2035-12-31T23:59:59",
                        "timeType": "local",
                    },
                }
            },
        )
        if res.status_code >= 400:
            # Allaqachon bor bo'lsa — bu xato emas, davom etamiz
            if "exist" not in res.text.lower():
                return False, f"Terminal rad etdi ({res.status_code}): {res.text[:120]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Terminalga ulanib bo'lmadi: {exc}"

    if not photo_b64:
        return True, "O'quvchi terminalga yozildi (rasm yuborilmadi)"

    try:
        image = base64.b64decode(photo_b64.split(",")[-1])
    except (ValueError, TypeError):
        return False, "Rasm formati noto'g'ri"

    try:
        res = requests.post(
            f"{base}/ISAPI/Intelligent/FDLib/FDSetUp?format=json",
            auth=auth,
            timeout=30,
            files={
                "FaceDataRecord": (
                    None,
                    json.dumps(
                        {
                            "faceLibType": "blackFD",
                            "FDID": "1",
                            "FPID": student.face_person_id,
                        }
                    ),
                    "application/json",
                ),
                "img": ("face.jpg", image, "image/jpeg"),
            },
        )
        if res.status_code >= 400:
            return False, f"Yuz qabul qilinmadi ({res.status_code}): {res.text[:120]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Yuz yuborilmadi: {exc}"

    return True, "O'quvchi va yuzi terminalga yozildi"
