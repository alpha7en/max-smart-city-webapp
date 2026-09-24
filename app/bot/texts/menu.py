"""Тексты меню-дашборда и экранов из меню (поток S4)."""

# --- Дашборд (строки; собирает domain/dashboard.py) ---
PERIOD_OPEN = "Показания за {month} — до {deadline}, {left}"
PERIOD_NEXT = "Следующая подача — с {start}"
METER = "{type} · {address}"
METER_SUBMITTED = "{meter} — подано {date}"
METER_NOT_SUBMITTED = "{meter} — не подано"
METERS_SUMMARY = "Счётчиков: {total}, не подано: {left}"
METERS_ALL_DONE = "Счётчиков: {total}, всё подано"
NO_METERS = "Счётчиков пока нет — пришлите фото любого, и мы его добавим."
VERIFICATION = "Поверка: {meter} — до {date}"
VERIFICATION_OVERDUE = "Поверка: {meter} — срок истёк {date}"
MORE = ", и ещё {n}"
BILL = "Счёт: {amount} до {date} (демо)"
BILL_OVERDUE = "Счёт: {amount}, срок был {date} (демо)"
BILLS = "Счета: {count} на {amount}, ближайший до {date} (демо)"
PENDING = "Доступ к адресу «{label}» ждёт подтверждения собственника."
PENDING_MANY = "Доступ к {n} адресам ждёт подтверждения собственников."
FOOTER = "Подробнее — в мини-приложении."

# --- Срочное действие (строка дашборда и кнопка, ≤ 32) ---
URGENT_VERIFICATION = "Запишитесь на поверку: {n} дн."
URGENT_VERIFICATION_TODAY = "Запишитесь на поверку сегодня"
URGENT_VERIFICATION_OVERDUE = "Поверка просрочена — запишитесь"
URGENT_BILL = "Оплатите счёт: {n} дн."
URGENT_BILL_TODAY = "Оплатите счёт сегодня"
URGENT_BILL_OVERDUE = "Счёт просрочен — оплатите"
URGENT_SUBMIT = "Подайте показания: {n} дн."
URGENT_SUBMIT_TODAY = "Подайте показания сегодня"

# --- Кнопки меню ---
BTN_SUBMIT = "Подать показания"
BTN_METERS = "Мои счётчики"
BTN_PROFILE = "Профиль"
BTN_ADD_METER = "Добавить счётчик"

# --- Мои счётчики ---
METERS_TITLE = "Ваши счётчики:"
METERS_LAST = "Последнее: {value} ({date})"
METERS_NO_READINGS = "Показаний пока нет"
METERS_VERIFICATION = "поверка до {date}"
METERS_PENDING = "Счётчики адресов, которые ждут подтверждения собственника, появятся после одобрения."

# --- Честные заглушки ---
VERIFICATION_STUB = (
    "Запись на поверку появится скоро.\n\n"
    "Пока поверить счётчик можно самостоятельно: позвоните в управляющую компанию "
    "или аккредитованную организацию — специалист приедет и проверит счётчик на месте."
)
PAY_STUB = (
    "Оплата в боте появится скоро. Сейчас оплатить можно по квитанции — в банке или в приложении банка.\n\n"
    "Счёт в боте — демо."
)
