"""Telegram bot yordamchi funksiyalari.

Bot oqimi:
  1. O'quvchi botga kirib /start bosadi
  2. Bot "telefon raqamni yuborish" tugmasini ko'rsatadi
  3. O'quvchi raqamini yuboradi -> bazadagi Student bilan bog'lanadi
  4. Manager sayt orqali xabar yuborsa, ulangan o'quvchilarga TG orqali boradi
"""

import logging
import re
import threading

import requests
from django.conf import settings

from .models import SentMessage, Student, TelegramSubscriber

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/{method}"

WEBHOOK_PATH = "/api/tg/webhook/"

# Webhook kalitini qayta o'rnatishlar orasidagi eng qisqa oraliq.
# Telegram rad etilgan update'ni bir necha marta qayta yuboradi —
# har biriga setWebhook chaqirmaslik uchun.
_RESYNC_MIN_SECONDS = 300
_last_resync = 0.0
_resync_lock = threading.Lock()


def resync_webhook():
    """Webhook'ni joriy maxfiy kalit bilan qayta ro'yxatdan o'tkazadi.

    Kalit mos kelmay qolsa (masalan SECRET_KEY almashgan yoki webhook
    boshqa muhitdan o'rnatilgan bo'lsa) Telegram'dan kelgan har bir
    update 403 bilan rad etiladi va bot butunlay jim bo'ladi. Bepul
    planda Shell yo'q — ya'ni buni qo'lda tuzatib ham bo'lmaydi, deploy
    kutish kerak bo'lardi.

    Shuning uchun mos kelmagan kalit ko'rinishi bilan o'zimiz qayta
    ro'yxatdan o'tkazamiz. Telegram rad etilgan update'ni qayta
    yuboradi va u safar kalit to'g'ri keladi.
    """
    global _last_resync
    import time

    if not settings.TG_BOT_TOKEN or not settings.PUBLIC_BASE_URL:
        return False

    with _resync_lock:
        now = time.monotonic()
        if now - _last_resync < _RESYNC_MIN_SECONDS:
            return False
        _last_resync = now

    target = settings.PUBLIC_BASE_URL.rstrip("/") + WEBHOOK_PATH
    payload = {
        "url": target,
        "allowed_updates": ["message", "edited_message"],
        "max_connections": 40,
    }
    if settings.TG_WEBHOOK_SECRET:
        payload["secret_token"] = settings.TG_WEBHOOK_SECRET

    try:
        res = tg_call("setWebhook", payload)
    except Exception:  # noqa: BLE001 — webhook javobi baribir qaytishi kerak
        logger.exception("Webhook kalitini qayta o'rnatib bo'lmadi")
        return False

    if res.get("ok"):
        logger.warning("Webhook kaliti qayta o'rnatildi: %s", target)
        return True
    logger.error("setWebhook rad etdi: %s", res.get("description"))
    return False

WELCOME_TEXT = (
    "Assalomu alaykum! 👋\n\n"
    "Bu ITLINE o'quv markazining rasmiy xabarlar boti.\n"
    "To'lov eslatmalari va e'lonlarni olish uchun quyidagi tugma orqali "
    "telefon raqamingizni yuboring 👇"
)
LINKED_TEXT = "✅ {name}, siz xabarlarga muvaffaqiyatli ulandingiz!"
NOT_FOUND_TEXT = (
    "❌ Bu raqam bazadan topilmadi.\n"
    "Iltimos, o'quv markazida ro'yxatdan o'tgan raqamingizni yuboring "
    "yoki administratorga murojaat qiling."
)
TEACHER_LINKED_TEXT = (
    "✅ {name}, siz ustoz sifatida ulandingiz!\n\n"
    "Bu yerga o'z guruhlaringiz haqidagi xabarlar keladi: dars "
    "eslatmalari va markazdan e'lonlar."
)
MANAGER_LINKED_TEXT = (
    "✅ {name}, siz menejer sifatida ulandingiz!\n\n"
    "Panelda e'tibor talab qiladigan narsa paydo bo'lsa shu yerga "
    "xabar keladi — masalan yangi to'lov cheki."
)
LEAD_LINKED_TEXT = (
    "✅ Rahmat{name}! Raqamingiz qabul qilindi.\n\n"
    "ITLINE o'quv markazining yangiliklari va kurslar haqidagi "
    "e'lonlar shu yerga keladi."
)

PENDING_TEXT = (
    "✅ Raqamingiz qabul qilindi!\n\n"
    "Siz hali bazada yo'qsiz. Administrator sizni ro'yxatga qo'shayotganda "
    "shu yerga tasdiqlash kodi keladi — kodni administratorga ayting."
)
CODE_TEXT = (
    "🔐 Ro'yxatdan o'tish kodi: {code}\n\n"
    "Bu kodni administratorga ayting. Kod 10 daqiqa amal qiladi.\n"
    "Agar siz ro'yxatdan o'tishni so'ramagan bo'lsangiz, e'tiborsiz qoldiring."
)


def tg_call(method, payload, timeout=15):
    """Telegram Bot API chaqiruvi."""
    token = settings.TG_BOT_TOKEN
    if not token:
        # Tokensiz URL '.../bot/sendMessage' bo'lib, Telegram tushunarsiz
        # 404 qaytaradi — sababi ko'rinib tursin
        raise RuntimeError(
            "TG_BOT_TOKEN o'rnatilmagan — bot xabar yubora olmaydi"
        )
    url = API_URL.format(token=token, method=method)
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        data = resp.json()
    except requests.RequestException as e:
        raise RuntimeError(f"Telegramga ulanib bo'lmadi: {e}") from e
    except ValueError as e:  # JSON emas (proxy/HTML xato sahifasi)
        raise RuntimeError("Telegram noto'g'ri javob qaytardi") from e
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Telegram API xatosi"))
    return data["result"]


def send_text(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return tg_call("sendMessage", payload)


def last9(phone):
    """Telefonning oxirgi 9 raqami (solishtirish uchun)."""
    d = re.sub(r"\D", "", str(phone or ""))
    return d[-9:] if len(d) >= 9 else ""


def student_menu():
    """Ulangan o'quvchiga doimiy tugmalar."""
    return {
        "keyboard": [[{"text": "💰 Coinlarim"}]],
        "resize_keyboard": True,
    }


def handle_coin_request(chat_id):
    """O'quvchiga coin balansi va oxirgi harakatlarni ko'rsatadi."""
    sub = TelegramSubscriber.objects.filter(chat_id=chat_id).first()
    student = sub.student if sub else None
    if not student:
        send_text(
            chat_id,
            "Coinlaringizni ko'rish uchun avval /start orqali "
            "telefon raqamingizni yuboring.",
        )
        return

    from .models import CoinTransaction

    lines = [
        f"💰 <b>{student.name} {student.surname}</b>",
        f"Balans: <b>{student.coin_balance}</b> coin",
    ]

    recent = list(
        CoinTransaction.objects.filter(student=student).order_by("-created_at")[:5]
    )
    if recent:
        lines.append("\nOxirgi harakatlar:")
        for t in recent:
            sign = "+" if t.amount >= 0 else ""
            lines.append(
                f"  {t.created_at:%d.%m}  {sign}{t.amount}  {t.get_reason_display()}"
            )
    else:
        lines.append("\nHozircha coin harakati yo'q.")

    # Reytingdagi o'rni — faqat o'quvchining o'z guruhi ichida, butun
    # markaz bo'yicha emas: bu ma'lumot unga yaqinroq va tushunarliroq
    group = student.groups.first()
    if group:
        better = (
            group.students.filter(coin_balance__gt=student.coin_balance)
            .exclude(is_admin=True)
            .count()
        )
        total = group.students.exclude(is_admin=True).count()
        lines.append(f"\n🏆 «{group.name}» guruhida: {better + 1} / {total}")

    send_text(chat_id, "\n".join(lines), reply_markup=student_menu())


def identify_by_phone(phone):
    """Raqam kimga tegishli — (rol, obyekt) juftligi.

    Tartib muhim: bitta raqam bir nechta jadvalda uchraydi. Ustozning
    o'quvchi profili ham bo'ladi (is_admin), menejer ham o'quvchi
    sifatida yozilgan bo'lishi mumkin. Eng "kuchli" roldan boshlaymiz,
    aks holda ustoz o'zini o'quvchi deb tanitib qolardi.
    """
    target = last9(phone)
    if not target:
        return ("unknown", None)

    from .models import Lead, Manager, Teacher

    for m in Manager.objects.filter(is_active=True).only("id", "phone", "name"):
        if last9(m.phone) == target:
            return ("manager", m)

    for t in Teacher.objects.only("id", "phone", "name"):
        if last9(t.phone) == target:
            return ("teacher", t)

    student = find_student_by_phone(phone)
    if student:
        return ("student", student)

    for lead in Lead.objects.only("id", "phone", "phone2", "name"):
        if last9(lead.phone) == target or last9(lead.phone2) == target:
            return ("lead", lead)

    return ("unknown", None)


def find_student_by_phone(phone):
    """Telefon bo'yicha o'quvchini topadi (phone yoki phone2, faollarga ustunlik)."""
    target = last9(phone)
    if not target:
        return None
    match, grad_match = None, None
    qs = Student.objects.filter(is_admin=False, is_excellence=False).values(
        "id", "phone", "phone2", "is_graduate"
    )
    for s in qs:
        if last9(s["phone"]) == target or last9(s["phone2"]) == target:
            if s["is_graduate"]:
                grad_match = grad_match or s["id"]
            else:
                match = match or s["id"]
                break
    sid = match or grad_match
    return Student.objects.filter(id=sid).first() if sid else None


def student_login_info(student):
    """Saytga kirish ma'lumotlari matni: login (telefon) va parol.

    Import qilingan (paroli o'rnatilmagan) o'quvchining paroli — ism va
    familiyasi (login_student shu bilan tekshiradi). O'zi maxsus parol
    o'rnatgan bo'lsa — u hash ko'rinishida saqlanadi va ochib bo'lmaydi.
    """
    phone = student.phone or "—"
    if student.password:
        parol_qatori = (
            "🔒 Parol: siz o'rnatgan maxsus parol — xavfsizlik uchun uni "
            "ko'rsatib bo'lmaydi. Unutgan bo'lsangiz, administratorga murojaat qiling."
        )
    else:
        parol = f"{student.name} {student.surname}".strip()
        parol_qatori = f"🔒 Parol: {parol}  (ism va familiyangiz)"
    return (
        "🔑 Saytga kirish ma'lumotlaringiz:\n"
        f"📱 Login (telefon): {phone}\n"
        f"{parol_qatori}"
    )


def handle_update(update):
    """Webhook'dan kelgan update'ni qayta ishlaydi."""
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    contact = msg.get("contact")
    tg_name = " ".join(
        filter(None, [msg["chat"].get("first_name"), msg["chat"].get("last_name")])
    )

    try:
        if text.startswith("/start"):
            send_text(
                chat_id,
                WELCOME_TEXT,
                reply_markup={
                    "keyboard": [
                        [{"text": "📱 Telefon raqamni yuborish", "request_contact": True}]
                    ],
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                },
            )
            return

        phone = None
        # Faqat tugma orqali ulashilgan (Telegram tasdiqlagan) o'z raqami
        # bo'lsagina parolni ko'rsatamiz — qo'lda yozilgan begona raqam
        # orqali birovning parolini olishning oldini oladi.
        verified_own = False
        if contact:
            phone = contact.get("phone_number")
            sender_id = (msg.get("from") or {}).get("id")
            verified_own = (
                sender_id is not None and contact.get("user_id") == sender_id
            )
        elif re.sub(r"\D", "", text) and len(re.sub(r"\D", "", text)) >= 9:
            phone = text  # raqamni qo'lda yozgan bo'lsa ham qabul qilamiz

        if phone:
            role, who = identify_by_phone(phone)

            # Bazada yo'q bo'lsa ham saqlaymiz — yangi o'quvchi qo'shilganda
            # tasdiqlash kodi shu chat'ga yuboriladi va avtomatik bog'lanadi
            defaults = {
                "phone": last9(phone),
                "tg_name": tg_name[:200],
                "role": role,
            }
            if role == "student":
                defaults["student"] = who
            elif role == "teacher":
                defaults["teacher"] = who
            elif role == "manager":
                defaults["manager"] = who
            elif role == "lead":
                defaults["lead"] = who

            sub, _created = TelegramSubscriber.objects.update_or_create(
                chat_id=chat_id, defaults=defaults
            )

            # Topilmagan raqam mavjud bog'lanishni buzmasligi kerak: ulangan
            # o'quvchi bazada yo'q raqam yozsa, avval student=None bo'lib
            # qolar va u boshqa xabar olmay qo'yardi.
            student = who if role == "student" else None
            if not student and sub.student_id:
                student = sub.student
                sub.role = "student"
                sub.save(update_fields=["role"])
                # Yuborilgan raqam bu o'quvchiniki emas — parolni ko'rsatmaymiz
                verified_own = False

            if student:
                linked_msg = LINKED_TEXT.format(
                    name=f"{student.name} {student.surname}".strip()
                )
                if verified_own:
                    linked_msg += "\n\n" + student_login_info(student)
                else:
                    linked_msg += (
                        "\n\nSaytga kirish ma'lumotlaringizni ko'rish uchun "
                        "'📱 Telefon raqamni yuborish' tugmasi orqali raqamingizni "
                        "yuboring."
                    )
                send_text(chat_id, linked_msg, reply_markup=student_menu())
            elif role == "teacher":
                send_text(
                    chat_id,
                    TEACHER_LINKED_TEXT.format(name=who.name),
                    reply_markup={"remove_keyboard": True},
                )
            elif role == "manager":
                send_text(
                    chat_id,
                    MANAGER_LINKED_TEXT.format(name=who.name),
                    reply_markup={"remove_keyboard": True},
                )
            elif role == "lead":
                send_text(
                    chat_id,
                    LEAD_LINKED_TEXT.format(name=who.name or ""),
                    reply_markup={"remove_keyboard": True},
                )
            else:
                send_text(
                    chat_id, PENDING_TEXT, reply_markup={"remove_keyboard": True}
                )
            return

        if text in ("/coin", "💰 Coinlarim", "/balans"):
            handle_coin_request(chat_id)
            return

        # boshqa har qanday xabar
        send_text(
            chat_id,
            "Xabaringiz qabul qilindi. Ulanish uchun /start ni bosing.",
        )
    except Exception:
        logger.exception("TG update qayta ishlashda xato (chat_id=%s)", chat_id)


def _personalize(text, student, month=""):
    return (
        text.replace("{ism}", f"{student.name} {student.surname}".strip())
        .replace("{oy}", month or "")
    )


UZ_MONTHS = [
    "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
    "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr",
]


def _money(value):
    return f"{int(value or 0):,}".replace(",", " ") + " so'm"


def _month_label(month):
    """'2026-07' -> 'Iyul 2026'."""
    try:
        year, mon = month.split("-")
        return f"{UZ_MONTHS[int(mon) - 1]} {year}"
    except (ValueError, IndexError, AttributeError):
        return month or ""


def build_receipt(payment):
    """To'lov cheki matnini sozlamadagi shablondan tuzadi."""
    from django.utils import timezone

    from .models import ReceiptSettings

    settings_obj = ReceiptSettings.get_settings()
    student = payment.student
    group = student.groups.first() if student else None

    due = max(0, (payment.amount_due or 0) - (payment.discount or 0))
    paid = payment.paid_amount or 0

    values = {
        "{ism}": f"{student.name} {student.surname}".strip() if student else "",
        "{oy}": _month_label(payment.month),
        "{summa}": _money(paid),
        "{jami}": _money(due),
        "{qolgan}": _money(max(0, due - paid)),
        "{sana}": timezone.localdate().strftime("%d.%m.%Y"),
        "{markaz}": settings_obj.center_name,
        "{guruh}": group.name if group else "—",
    }

    text = settings_obj.template or ReceiptSettings.DEFAULT_TEMPLATE
    for key, val in values.items():
        text = text.replace(key, str(val))
    return text


def send_receipt(payment):
    """To'lov tasdiqlangach o'quvchiga chek yuboradi.

    Fon oqimida — menejer tugmani bosgach panel Telegramni kutib
    turmasin. Chek o'chirilgan yoki o'quvchi botga ulanmagan bo'lsa
    jim o'tib ketadi.
    """
    from .models import ReceiptSettings

    if not ReceiptSettings.get_settings().enabled:
        return
    if not payment.student_id:
        return

    text = build_receipt(payment)
    student_id = payment.student_id

    def run():
        subs = TelegramSubscriber.objects.filter(student_id=student_id)
        for sub in subs:
            try:
                send_text(sub.chat_id, text)
            except Exception:  # noqa: BLE001
                logger.exception("Chek yuborilmadi (chat=%s)", sub.chat_id)

    threading.Thread(target=run, daemon=True).start()


def send_photo(chat_id, photo_url, caption=""):
    """Rasm yuboradi. Rasm URL bo'lishi kerak (mahsulot rasmi shunday)."""
    payload = {"chat_id": chat_id, "photo": photo_url, "parse_mode": "HTML"}
    if caption:
        payload["caption"] = caption[:1024]
    return tg_call("sendPhoto", payload)


def notify_managers(text):
    """Botga ulangan menejerlarga bildirishnoma.

    Fon oqimida ishlaydi — panel amali (masalan chek qabul qilish)
    Telegram sekinligi tufayli kutib qolmasin.
    """

    def run():
        subs = TelegramSubscriber.objects.filter(role="manager").exclude(
            manager__isnull=True
        )
        for sub in subs:
            try:
                send_text(sub.chat_id, text)
            except Exception:  # noqa: BLE001 — biri xato bo'lsa qolgani ketaversin
                logger.exception("Menejerga xabar yuborilmadi (chat=%s)", sub.chat_id)

    threading.Thread(target=run, daemon=True).start()


def broadcast_product(product):
    """Yangi mahsulotni botga ulangan o'quvchilarga e'lon qiladi."""
    price = f"{product.price_coins:,}".replace(",", " ")
    caption = (
        f"🛍 <b>Do'konda yangi mahsulot!</b>\n\n"
        f"<b>{product.name}</b>\n"
        f"Narxi: <b>{price} coin</b>"
    )
    if product.description:
        caption += f"\n\n{product.description[:600]}"

    def run():
        subs = TelegramSubscriber.objects.filter(role="student").exclude(
            student__isnull=True
        )
        for sub in subs:
            try:
                if product.image:
                    send_photo(sub.chat_id, product.image, caption)
                else:
                    send_text(sub.chat_id, caption)
            except Exception:  # noqa: BLE001
                logger.exception("Mahsulot e'loni ketmadi (chat=%s)", sub.chat_id)

    threading.Thread(target=run, daemon=True).start()


def send_to_leads(text):
    """Botga ulangan leadlarga reklama xabari. Natija: (yuborildi, xato)."""
    sent = failed = 0
    subs = TelegramSubscriber.objects.filter(role="lead").exclude(lead__isnull=True)
    for sub in subs:
        try:
            send_text(sub.chat_id, text)
            sent += 1
        except Exception:  # noqa: BLE001
            failed += 1
            logger.exception("Leadga xabar ketmadi (chat=%s)", sub.chat_id)
    return sent, failed


def send_to_teachers(text, teacher_ids=None):
    """Ustozlarga xabar. `teacher_ids` berilmasa hammasiga."""
    sent = failed = 0
    subs = TelegramSubscriber.objects.filter(role="teacher").exclude(
        teacher__isnull=True
    )
    if teacher_ids is not None:
        subs = subs.filter(teacher_id__in=list(teacher_ids))
    for sub in subs:
        try:
            send_text(sub.chat_id, text)
            sent += 1
        except Exception:  # noqa: BLE001
            failed += 1
            logger.exception("Ustozga xabar ketmadi (chat=%s)", sub.chat_id)
    return sent, failed


def send_to_students(students, text, kind, month=""):
    """O'quvchilar ro'yxatiga xabar yuboradi. Natija: (sent, failed, no_chat)."""
    students = list(students)
    subs = TelegramSubscriber.objects.filter(student__in=students)
    subs_by_student = {}
    for sub in subs:
        subs_by_student.setdefault(sub.student_id, []).append(sub)

    # Telefon bo'yicha ham qidiramiz: aka-uka bir xil ota-ona raqamini
    # ishlatganda ikkinchisining chat'i student'ga bog'lanmagan bo'ladi —
    # xabar baribir o'sha chat'ga yetib borishi kerak
    need_phone_lookup = [s for s in students if s.id not in subs_by_student]
    if need_phone_lookup:
        wanted = {}
        for s in need_phone_lookup:
            for p in (s.phone, s.phone2):
                key = last9(p)
                # to'plam: phone va phone2 bir xil bo'lsa o'quvchi ikki marta
                # qo'shilib, xabar ham ikki marta ketardi
                if key:
                    wanted.setdefault(key, set()).add(s.id)
        if wanted:
            for sub in TelegramSubscriber.objects.filter(phone__in=wanted.keys()):
                for sid in wanted.get(sub.phone, ()):
                    subs_by_student.setdefault(sid, []).append(sub)

    sent = failed = no_chat = 0
    logs = []
    for student in students:
        student_subs = subs_by_student.get(student.id)
        if not student_subs:
            no_chat += 1
            logs.append(
                SentMessage(
                    student=student, kind=kind, text=text, status="no_chat"
                )
            )
            continue
        body = _personalize(text, student, month)
        for sub in student_subs:
            try:
                send_text(sub.chat_id, body)
                sent += 1
                logs.append(
                    SentMessage(
                        student=student,
                        chat_id=sub.chat_id,
                        kind=kind,
                        text=body,
                        status="sent",
                    )
                )
            except Exception as e:
                failed += 1
                logs.append(
                    SentMessage(
                        student=student,
                        chat_id=sub.chat_id,
                        kind=kind,
                        text=body,
                        status="failed",
                        error=str(e)[:300],
                    )
                )
    SentMessage.objects.bulk_create(logs, batch_size=200)
    return sent, failed, no_chat


def send_to_students_async(students, text, kind, month=""):
    """Katta ro'yxat uchun fon oqimida yuborish."""
    students = list(students)
    t = threading.Thread(
        target=send_to_students, args=(students, text, kind, month), daemon=True
    )
    t.start()
