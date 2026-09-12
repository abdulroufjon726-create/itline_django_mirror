# Xavfsizlikni mustahkamlash (Security Hardening)

Bu hujjat CRM tizimiga qilingan xavfsizlik yaxshilanishlari va deploy'dan
oldin bajarilishi shart bo'lgan qadamlarni bayon qiladi.

## Nima uchun kerak bo'ldi

Audit paytida aniqlangan asosiy muammolar:

| # | Muammo | Xavf |
|---|--------|------|
| 1 | Panel API'sining ko'p qismi (o'quvchilar, ustozlar, to'lovlar, xabarlar tarixi) **umuman autentifikatsiyasiz** edi | Istalgan odam API'ga so'rov yuborib, barcha o'quvchilarning ism-telefonini, to'lov holatini ko'ra olardi |
| 2 | `update_payment_settings` (karta raqamini o'zgartirish) **ochiq** edi | Hujumchi to'lov kartasini o'zinikiga almashtirib, barcha o'quvchi to'lovlarini o'zlashtirardi |
| 3 | `create_teacher` ochiq edi | Istalgan odam o'ziga ustoz profili ochib panelga kira olardi |
| 4 | `accept/reject_payment_request` ochiq edi | Soxta to'lov tasdiqlash |
| 5 | O'quvchi bo'yicha endpoint'larda **IDOR**: `student_id` URL'dan olinardi | O'quvchi ID'sini o'zgartirib, boshqaning balansi, coinlari, to'lovlarini ko'ra olardi |
| 6 | Login'dagi parolsiz "raqam bor/yo'q" tekshiruvi cheklanmagan edi | Raqamlarni sanab, kim o'quvchi ekanini bilib olishardi |
| 7 | Kodni tekshirish endpoint'i cheklanmagan edi | 5 xonalik kodni taxmin qilish |
| 8 | `delete_student` ochiq edi | Istalgan odam bazadagi o'quvchini o'chira olardi |
| 9 | Frontend'ning o'quvchi/ustoz panellari JWT yubormasdi | Backend gate qo'yilganda ularning so'rovlari bloklanardi |

## Qilingan ishlar

### 1. API Auth Gate — "sukut bo'yicha rad etish" (`config/middleware.py`)

`ApiAuthGateMiddleware` — `/api/` ostidagi **HAR BIR** so'rov JWT token
talab qiladi. Token yo'q/imzosi yaroqsiz bo'lsa — 401.

Bu teskari mantiq: ilgari *har bir* view'da tekshiruv yozish kerak edi
(va unutilardi), endi esa yangi endpoint qo'shilsa avtomatik himoyalangan
bo'ladi. Ochiq qolishi kerak bo'lganlari `PUBLIC_API_PATHS` ro'yxatida:

- login/register/token refresh/change-password
- tasdiqlash kodi (send/check)
- Telegram webhook (o'z secret'i bilan) va Face ID terminal (URL'dagi secret)
- `ping` (uptime monitoring)
- `payment-requests/create` — o'quvchi chek yuboradi (token'siz eski mijoz)

### 2. View darajasidagi tekshiruvlar (`register_withvue/views.py`)

| Endpoint | Himoya |
|----------|--------|
| `create_teacher` | `_require_staff` (menejer/ustoz) |
| `generate_payments` | `_require_manager_or_admin` |
| `update_payment_settings` | `caller_manager` + `payments.settings` vakolati |
| `get_payment_settings` | token bo'lishi shart (karta raqami maxfiy) |
| `accept/reject_payment_request` | `caller_manager` + `payments.requests` |
| `send_message_student/group/all/students` | menejer — vakolat bilan, ustoz — ruxsat bilan (`_require_staff_perm`) |
| `delete_student` | `_require_manager_or_admin` |

### 3. IDOR himoyasi (o'quvchi ma'lumotlari)

Quyidagi endpoint'larda chaqiruvchi o'quvchi bo'lsa — **faqat o'z**
yozuvini ko'radi (menejer/ustoz barchasini ko'raveradi):

- `students/<id>/wallet/`
- `coins/student/<id>/`
- `coins/transactions/<id>/`
- `orders/student/<id>/`
- `payments/<id>/`
- `payment-requests/student/<id>/`
- `payment-requests/create` — boshqaning nomidan chek yuborish bloklandi

### 4. Rate limiting (ochiq endpoint'larda)

`ratelimit.py` cache asosida sanaydi (IP + telefon bo'yicha):

| Endpoint | Limit |
|----------|-------|
| Login parolsiz probe (o'quvchi) | 20 / 5 daqiqa |
| Login parolsiz probe (menejer) | 20 / 5 daqiqa |
| Kod yuborish (`verify/send-code`) | 5 / 10 daqiqa |
| Kod tekshirish (`verify/check-code`) | 10 / 5 daqiqa |

Parol bilan login allaqachon cheklangan (5 / 5 daqiqa).

### 5. Xavfsizlik header'lari (`config/settings.py`)

Har doim (DEBUG'da ham): `nosniff`, `X-Frame-Options: DENY`,
`strict-origin-when-cross-origin` referrer policy.
Production'da: `SECURE_SSL_REDIRECT`, `HSTS` (1 yil, preload),
secure cookie'lar.

### 6. CORS tozalandi

`CORS_ALLOW_ALL_ORIGINS` olib tashlangan — faqat localhost + env orqali
qo'shiladigan domenlar (`CORS_EXTRA_ORIGINS`). Eski `X-User-Phone`
sarlavhasi ro'yxatdan chiqarildi.

### 7. Frontend: avtomatik JWT (`itline_fr/src/main.ts`)

`window.fetch` bir joyda o'ralgan — localStorage'da token bo'lsa
`Authorization: Bearer ...` sarlavhasi har bir `/api/` so'roviga
o'zi qo'shiladi. Menejer paneli `managerApi.authHeaders()` orqali,
o'quvchi panellari esa shu interceptor orqali tokenni yuboradi.

### 8. Testlar va jonli tekshiruv

- Eski testlar `X-User-Phone` sarlavhasidan JWT'ga ko'chirildi
  (`tests_edu_setup`, `tests_cash`, `tests_telegram`, `tests_phones`) —
  15 ta eski muvaffaqiyatsizlik tuzatildi.
- `tests_security.py` — 40 ta yangi test: gate, IDOR, karta himoyasi,
  to'lov so'rovi vakolati, rate limit, o'chirish/ broadcast himoyasi.
- To'plam: **195 test, hammasi OK**.

### 9. Ikkinchi bosqich — qo'shimcha teshiklar (adversarial audit)

Birinchi bosqishdan keyin "yangi ko'z bilan" qayta tekshiruvda
quyidagi qolgan xavflar topilib tuzatildi:

- **Yozuv endpoint'lari himoyasiz qolgan edi**: `confirm_payment`,
  `update_payment_amount` (o'quvchi o'z to'lovini "to'langan" qilib
  yuborardi), `update_student`, `create_lesson`, `update_attendance`
  va `give_manual_coins` (o'quvchi o'ziga coin berardi). Endi:
  menejer/admin — to'lov, o'quvchi va dars yozuvlarida; ustoz/menejer/
  admin — davomat va coin berishda.
- **`/api/students/` ro'yxati** oddiy o'quvchiga butun bazani (ism,
  telefon, balans, holat) qaytarardi — endi oddiy o'quvchi faqat o'z
  guruhdoshtarlarini ko'radi; menejer/ustoz/admin to'liq ro'yxat.
- **`student-attendance/<id>/`** — boshqa o'quvchining davomati o'qilishi
  mumkin edi (IDOR). Endi faqat o'zi yoki xodimlar.
- **`attendance/group-day` va `group-month`** — panel ma'lumoti, endi
  token bilan kirgan har kim (menejer/ustoz/admin) ko'radi.
- **`change_password`** (ommaviy) parol taxminashga ochiq edi — 5
  urinish/5 daqiqa limit qo'shildi.
- **Rate limit XFF zaifligi** — `X-Forwarded-For`'ning BIRINCHI
  yozuvi ishlatilardi, uni mijoz o'zi yozib soxtalashtira olardi.
  Endi O'NGDAGI (oxirgi) yozuv olinadi — uni faqat ishonchli proksi
  qo'shadi.
- **Frontend axios**: `DefaultFee.vue` axios ishlatardi — fetch
  interceptor uni chetlab o'tardi. Endi axios interceptor ham bor
  (`main.ts`) va har bir `/api/` so'roviga token qo'shadi.
- To'plam: **207 test, hammasi OK**. Jonli HTTP smoke (18 tekshiruv)
  va brauzer (Preview) orqali panellar haqiqiy ma'lumot bilan
  sinovdan o'tkazildi.

### 10. Uchinchi bosqich — nozik vakolatlar (permission katalogi)

Rollar yetarli emas edi: har qanday menejer hatto berilmagan
vakolat bilan ham moliyaviy amallarni bajarardi. Endi katalogdagi
kalitlar backendda ham yuritiladi (frontend `can()` bilan bir xil):

- **Moliya**: to'lov tasdiqlash/tuzatish — `payments.edit`; pul
  qabul qilish — `payments.edit` YOKI `cash.view` (kassir); to'lovlar
  tarixini ko'rish — `payments.view`/`history.view`; karta —
  `payments.settings`.
- **O'quvchilar**: import — `students.add`; ko'chirish —
  `students.transfer`; o'chirish — `students.delete`.
- **Ustozlar**: qo'shish/tahrirlash/o'chirish — `teachers.add/edit/delete`.
- **Guruhlar/kurslar**: `groups.edit`, `groups.delete`, `courses.edit`.
- **Do'kon/coin**: mahsulot — `shop.products`, buyurtma —
  `shop.orders`, sozlamalar — `coins.settings` (admin o'quvchi
  panellari ham o'tadi).
- **O'qish endpoint'lari** (`payments/`, `history`, `graduates`,
  `ad-channels`, `managers/`): menejerdan tegishli vakolat so'raladi
  (`_staff_read`), ustoz/admin o'quvchi o'z panellari uchun o'tadi.
- **Yangiliklar**: `user_id` endi body'dan emas, JWT'dan olinadi —
  oddiy o'quvchi boshqaning nomidan yangilik joylay olmaydi.
- **`/teachers/`** anonim ro'yxatdan o'tish sahifasiga faqat id+ism
  qaytaradi (telefonlar faqat xodimga).
- Yangi yordamchilar: `_perm_any`, `_perm_any_or_admin`, `_staff_read`.
- To'plam: **216 test, hammasi OK**. Jonli smoke: faqat
  `students.view` li menejer moliyaga 403 oladi, kassir (`cash.*`)
  pul qabul qiladi lekin tarixni ko'ra olmaydi, super — hammasi OK.

Jonli tekshiruv (haqiqiy server + brauzer orqali): token'siz so'rovlar
401, menejer/o'quvchi logini va panellari ishlaydi, o'quvchi boshqaning
balansini 403 bilan ko'radi, soxta token 401 oladi, 25 tezkor probe 429
beradi. Shu jarayonda ikkita alohida xato topilib tuzatildi:

- `refresh_view` (token yangilash) da `@csrf_exempt` tushib qolgan edi —
  har bir token yangilash 403 berardi (12 soatdan keyin hamma qayta
  login qilishga majbur bo'lardi).
- Frontend'da 21 faylda backend manzili qotirib qo'yilgan edi —
  hammasi `src/config.ts` dagi bitta `API` o'zgaruvchisiga ko'chirildi
  (`VITE_API_BASE` env bilan boshib o'tkaziladi).

## Deploy'dan oldin bajarilishi SHART bo'lgan qadamlar

1. **Render Environment'da `ADMIN_PASSWORD` va `EXCELLENCE_PASSWORD`**
   o'rnatilganini tekshiring — kodda ochiq turgan standart parollar
   (`excel2024` / `excellence2024`) faqat o'rnalmagan bo'lsa ishlatiladi
   va ogohlantirish yozadi.

2. **`SECRET_KEY`** production'da kuchli (tartibsiz, 50+ belgi) bo'lishi
   kerak — JWT tokenlar shu kalit bilan imzolanadi. Uning oshkor
   bo'lishi barcha tokenlarni soxta yasash imkonini beradi.

3. **`CORS_EXTRA_ORIGINS`** — frontend'ning haqiqiy domenini qo'shing:
   ```
   CORS_EXTRA_ORIGINS=https://sizning-domen.vercel.app
   ```

4. **Rate limit cache** — hozircha `locmem` (bitta Render worker uchun).
   Ko'p instance ishlatilsa `Redis` o'tkazing, aks holda limitlar
   har bir worker'da alohida sanaladi.

5. **Refresh token blacklist** — `rest_framework_simplejwt.token_blacklist`
   ilovasini yoqing (o'g'irlangan refresh tokenni bekor qilish uchun).

6. **Deploy'dan keyin smoke test**: token'siz `GET /api/students/` —
   401 olishi kerak; login → panel ochilishi; o'quvchi chek yuborishi;
   menejer paneli to'liq ishlashi.

## Qoldiq tavsiyalar (keyingi qadam)

- `payment-requests/create` uchun bot chat_id asosida token bilan
  autentifikatsiya (hozir eski mijozlar uchun token'siz qoldi).
- Telegram webhook'ga alohida `TG_WEBHOOK_SECRET` env o'zgaruvchisi
  (hozir SECRET_KEY'dan hosil bo'ladi — yaxshi, lekin alohida kalit
  aylanishni osonlashtiradi).
- 2FA (SMS yoki Telegram tasdiqlash) supermenejer loginiga.
- Audit jurnali (`ActivityLog`) uchun alomatlar: kutilmaganda ko'p
  401/403 bo'lsa admin xabari.
