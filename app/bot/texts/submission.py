"""Тексты подачи показаний (бот) и сообщения сервиса подачи (app/readings.py, для API)."""
import re

from app.bot.texts import registration as _reg
from app.bot.texts.fmt import TYPE_GEN, esc

# Общая строка про смоделированную передачу (бот, сервис подачи, сообщение из мини-приложения).
UK_MOCK = "В демо-версии передача в управляющую компанию смоделирована."

# --- Вход ---
INSTRUCTION = (
    "Пришлите фото счётчика — цифры распознаем сами.\n\n"
    "Чтобы всё получилось:\n"
    "— снимайте прямо, без бликов;\n"
    "— в кадре должны быть все цифры и серийный номер или штрихкод.\n\n"
    "**ПРИШЛИТЕ ВАШЕ ФОТО В ЧАТ**"
)
RETAKE = "Пришлите новое фото счётчика. Снимайте прямо, без бликов, чтобы в кадр попали все цифры."
ADD_PROMPT = "Пришлите фото нового счётчика или введите показание вручную — так мы его добавим."
PHOTO_RECEIVED = "Фото получили."
PHOTO_REPLACED = "Взяли новое фото."
PHOTO_FAILED = "Не получилось загрузить фото. Пришлите его ещё раз или введите показание вручную."
PHOTO_GONE = "Фото уже не сохранилось. Пришлите его ещё раз или введите показание вручную."
PENDING_PHOTO = "Теперь разберём фото, которое вы прислали."

# --- Выбор счётчика ---
PICK_PHOTO = "Какой это счётчик?"
PICK_MANUAL = "Для какого счётчика вводим показание?"
NO_METERS_PHOTO = "Счётчиков у вас пока нет — добавим этот."
NO_METERS_MANUAL = "Счётчиков у вас пока нет — сначала добавим счётчик."
ASK_TYPE = "Выберите тип счётчика."
ASK_TARIFF = (
    "Сколько тарифов у счётчика?\n\n"
    "Где посмотреть: на табло по очереди горят Т1, Т2 (и Т3), в квитанции есть строки «день» и «ночь», "
    "число тарифов указано и в паспорте счётчика."
)
ASK_ADDRESS = "По какому адресу этот счётчик?"
NO_METER = "Этого счётчика уже нет среди ваших."
PAGE_NOTE = "Показали {start}–{end} из {total}."

# --- Новый адрес (те же тексты, что в регистрации) ---
ASK_NEW_ADDRESS = f"Напишите адрес одной строкой: город, улица, дом, квартира.\n\n{_reg.ADDRESS_EXAMPLE}"
ADDRESS_NOT_FOUND = _reg.ADDRESS_NOT_FOUND
ADDRESS_NOT_IN_REGISTRY = _reg.ADDRESS_NOT_FOUND_ASIS
ADDRESS_ONE = _reg.ADDRESS_ONE              # {address}, {notes}
ADDRESS_LOCAL_NOTE = _reg.LOCAL_NOTE
ADDRESS_MANY = _reg.ADDRESS_MANY
ASK_FLAT = _reg.ASK_FLAT                    # {address}
FLAT_ERROR = _reg.FLAT_ERROR
ADDRESS_ALREADY = "Этот адрес уже есть у вас: {label}."

# --- Распознавание ---
LOOKING = "Смотрим на фото…"
RECOGNIZE_FAILED = (
    "Не получилось разобрать цифры — так бывает из-за бликов или съёмки под углом.\n\n"
    "Попробуйте переснять или введите показание вручную."
)
FAILED_HEAD = "Не получилось распознать показание."
FAILED_TAIL = "Переснимите или введите показание вручную."
FAILED_TAIL_SERVICE = "Введите показание вручную или попробуйте позже."
# Коды проблем от сервиса распознавания (app/integrations/recognizer.ISSUES) → причина и совет.
ISSUE_TEXTS = {
    "no_meter": "Похоже, на фото нет счётчика — снимите его табло целиком.",
    "wrong_type": "Похоже, это не счётчик {kind} — выберите другой счётчик.",
    "digits_not_visible": "Цифры не видны — поднесите телефон ближе, чтобы в кадр целиком попал ряд цифр.",
    "blurry": "Фото размыто — держите телефон неподвижно и дождитесь фокуса.",
    "glare": "Блики — снимите под небольшим углом или без вспышки.",
    "too_dark": "Слишком темно — включите свет или фонарик.",
    "angle": "Снято под сильным углом — держите телефон прямо напротив табло.",
    "partially_covered": "Часть цифр закрыта — уберите то, что мешает, и переснимите.",
    "multiple_meters": "В кадре несколько счётчиков — снимите только нужный.",
    "display_off": "Табло не горит — нажмите кнопку на счётчике, чтобы оно включилось.",
    "other": "Не получилось разобрать цифры на фото.",
    "service": "Сервис распознавания сейчас не отвечает.",
    "low_confidence": "Мы не уверены в цифрах: фото нечёткое или мелкое — снимите ближе и ровнее.",
    "few_digits": "Видно не все цифры показания — в кадр должен попасть весь ряд цифр табло.",
}
FAILED_SERIAL = "Серийный номер тоже не виден — пусть в кадр попадёт и он или штрихкод."
MAX_ISSUES = 2
NOTE_LIMIT = 160
WRONG_TYPE_WARN = "Похоже, на фото счётчик другого типа — проверьте, тот ли счётчик выбран."
SWITCHED_METER = "Этот счётчик у вас уже есть — {label}. Запишем показание для него."
SERIAL_MISMATCH = (
    "Номер на фото не совпадает с этим счётчиком.\n"
    "На фото: **{photo}**\n"
    "У счётчика «{label}»: **{saved}**\n\n"
    "Если это другой счётчик — выберите его или добавьте новый. "
    "Если номер распознан с ошибкой — продолжим с текущим."
)
SERIAL_OF_OTHER = (
    "Номер на фото — **{photo}** — записан у другого вашего счётчика: «{other}».\n\n"
    "Если на фото он — выберите его. Если номер распознан с ошибкой — продолжим с «{label}»."
)
# Мини-приложение (обычный текст, без разметки).
API_UNREADABLE = "Не разобрали цифры. Переснимите прямо, без бликов, или введите вручную."
API_SERIAL_MISMATCH = "Номер на фото — {photo}, у счётчика — {saved}. Проверьте, тот ли счётчик выбран."
API_PARTIAL = "{fields}: на фото не видно — введите вручную."
API_SERIAL_NOT_ON_PHOTO = "Номер на фото не виден — убедитесь, что это счётчик с номером {serial}."
API_SERIAL_REQUIRED = ("Не разобрали серийный номер — он нужен, чтобы не перепутать счётчики. "
                       "Введите номер с корпуса счётчика или переснимите так, чтобы он был в кадре.")
API_SERIAL_BAD = "Это не похоже на серийный номер счётчика. Номер обычно рядом со штрихкодом, например: {example}"
API_SERIAL_TAKEN = "Номер {serial} уже записан у другого вашего счётчика. Проверьте номер."


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[а-яёa-z]{4,}", text.casefold())}


def issue_lines(issues: list[str], note: str | None, meter_type: str, *, markup: bool = True) -> list[str]:
    """До MAX_ISSUES причин с советом + пояснение модели, если оно не повторяет причины.
    serial_not_visible показанию не мешает — не показываем. markup=False — для мини-приложения (без esc)."""
    codes = [c for c in dict.fromkeys(issues) if c in ISSUE_TEXTS]
    if note and codes != ["service"]:
        codes = [c for c in codes if c != "other"]  # у «другого» пояснение модели точнее
    lines = [ISSUE_TEXTS[c].format(kind=TYPE_GEN.get(meter_type, "")) for c in codes[:MAX_ISSUES]]
    note = (note or "").strip()
    if note and "service" not in codes:
        if len(note) > NOTE_LIMIT:
            note = note[:NOTE_LIMIT].rsplit(" ", 1)[0] + "…"
        stems, seen = _stems(note), _stems(" ".join(lines))
        if not stems or len(stems & seen) < len(stems) * 0.6:
            note = note[0].upper() + note[1:]
            lines.append((esc(note) if markup else note) + ("" if note[-1] in ".!?…" else "."))
    return lines


def recognize_failed(issues: list[str], note: str | None, meter_type: str, *, need_serial: bool = False) -> str:
    """Экран «не получилось распознать»: что не так и что делать. Нет причин — общий текст.
    need_serial — у счётчика нет сохранённого номера, а на фото его тоже не видно."""
    lines = issue_lines(issues, note, meter_type)
    if need_serial and "serial_not_visible" in issues:
        lines.append(FAILED_SERIAL)
    if not lines:
        return RECOGNIZE_FAILED
    tail = FAILED_TAIL_SERVICE if "service" in issues else FAILED_TAIL
    return "\n".join([FAILED_HEAD, "", *lines, "", tail])


# Прочитали, но с оговорками (коды сервиса и наши) → предупреждение на экране проверки.
REVIEW_WARN = {
    "blurry": "Фото нечёткое — сверьте каждую цифру с табло.",
    "glare": "На фото блики — сверьте каждую цифру с табло.",
    "angle": "Снято под углом — сверьте каждую цифру с табло.",
    "too_dark": "Фото тёмное — сверьте каждую цифру с табло.",
    "partially_covered": "Часть табло закрыта — проверьте, все ли цифры на месте.",
    "digits_not_visible": "Не все цифры видны чётко — сверьте показание с табло.",
    "few_digits": "Разобрали меньше цифр до запятой, чем бывает у такого счётчика ({typical}), — сверьте с табло.",
    "low_confidence": "Мы не уверены в цифрах: фото нечёткое или мелкое. Сверьте с табло.",
}
TYPICAL_WHOLE = {"cold_water": "обычно 5", "hot_water": "обычно 5", "electricity": "обычно 5–6", "gas": "обычно 5"}


def review_warnings(issues: list[str], meter_type: str) -> list[str]:
    """До MAX_ISSUES конкретных предупреждений; «не уверены» — только если конкретных нет."""
    codes = [c for c in dict.fromkeys(issues) if c in REVIEW_WARN and c != "low_confidence"]
    if not codes and "low_confidence" in issues:
        codes = ["low_confidence"]
    return [REVIEW_WARN[c].format(typical=TYPICAL_WHOLE.get(meter_type, "")) for c in codes[:MAX_ISSUES]]


# --- Проверка ---
VALUE_LINE = "Показание: **{value}**"
TARIFF_LINE = "{label}: **{value}**"
SERIAL_MATCH = "Серийный номер: {serial} — совпадает"
SERIAL_NEW = "Серийный номер: {serial} — сохраним"
SERIAL_IGNORED = "Серийный номер на фото: {serial} — оставили сохранённый"
SERIAL_REJECTED = "Серийный номер на фото: {serial} — не сохраняем"
# Номер необычный для типа (domain/serials.validate_serial → warning): сохраняем, но просим сверить.
SERIAL_TYPICAL = {
    "cold_water": "обычно 8 цифр", "hot_water": "обычно 8 цифр", "electricity": "обычно от 8 до 16 цифр",
    "gas": "обычно 7–8 цифр", "heat": "обычно 6–10 цифр",
}
SERIAL_WARN = "Номер необычный для этого счётчика ({typical}) — сверьте с корпусом."
SERIAL_NOT_ON_PHOTO = "Номер на фото не виден — убедитесь, что это счётчик с номером {serial}."
# Номера нет ни у счётчика, ни на фото: без него не отправляем (счётчики легко перепутать).
SERIAL_MISSING = (
    "Не разобрали серийный номер — он нужен, чтобы не перепутать счётчики. "
    "Переснимите так, чтобы в кадре был номер или штрихкод, или введите номер вручную."
)
ASK_SERIAL = (
    "Напишите серийный номер счётчика — он на корпусе, рядом со штрихкодом. Например: {example}\n\n"
    "Он нужен, чтобы не перепутать счётчики."
)
SERIAL_EXAMPLES = {"cold_water": "18-123456", "hot_water": "18-123456", "electricity": "01234567",
                   "gas": "1234567", "heat": "12345678"}
SERIAL_ERRORS = {
    "empty": "Напишите номер с корпуса счётчика, например: {example}",
    "not_serial": ("Это не похоже на серийный номер: год, ГОСТ, класс или номер пломбы не подходят. "
                   "Номер обычно рядом со штрихкодом, например: {example}"),
    "too_short": "Слишком короткий номер — в нём {typical}. Например: {example}",
    "too_long": "Слишком длинный номер — в нём {typical}. Например: {example}",
}
SERIAL_TAKEN = "Номер {serial} уже записан у другого вашего счётчика: «{other}». Проверьте номер."
PREV_LINE = "В прошлый раз: {value} ({delta})"
PREV_LINE_MULTI = "В прошлый раз: {value}"
CHECK_DIGITS = "Проверьте цифры внимательно."
STUB_NOTE = "Демо-распознавание: значение подставлено, а не распознано."
REVIEW_QUESTION = "Всё верно?"

# --- Ручной ввод ---
ASK_VALUE = "Напишите показание с табло, например: {example}"
ASK_TARIFF_VALUE = "{label} ({n} из {total}): напишите показание, например: {example}"
RECOGNIZED_HINT = "На фото разобрали: {value}"
# Многотарифный: на фото виден не каждый тариф — спрашиваем только недостающие.
PARTIAL_GOT = "На фото разобрали: {got}. Остальные тарифы на табло не видны — допишите их."
ASK_TARIFF_ONLY = "{label}: напишите показание, например: {example}"
PREV_HINT = "В прошлый раз: {value}"
PARSE_ERRORS = {
    "empty": "Напишите показание цифрами, например: {example}",
    "format": "Не похоже на показание. Напишите только цифры и запятую, например: {example}",
    "too_many_digits": "Слишком много цифр до запятой — на табло их не больше {digits}. Например: {example}",
    "too_many_decimals": "После запятой — не больше {decimals} цифр. Например: {example}",
}
EXAMPLES = {
    "cold_water": "123,456", "hot_water": "123,456", "electricity": "12345,6",
    "gas": "1234,567", "heat": "12,345",
}

# --- Правдоподобие и повторная подача ---
LESS_THAN_PREV = (
    "Показание меньше прошлого: было {prev}, сейчас {new}.\n\n"
    "Счётчик не крутится назад — проверьте цифры."
)
TOO_BIG = (
    "Большой прирост: {delta} за {months}. Обычно не больше {limit}.\n\n"
    "Всё верно?"
)
ALREADY_SUBMITTED = "За {month} уже передано: {old}.\n\nЗаменить на {new}?"
KEPT_OLD = "Оставили прежнее показание."

# --- Готово ({meter} — «холодной воды (Арбат 47к1, кв 32)», fmt.meter_of) ---
DONE = "Готово! Записали показание счётчика {meter} за {month}: {value}."
FLAGGED_DONE = "Прирост больше обычного — отметили показание для проверки."
ASK_VERIF = (
    "Когда следующая поверка этого счётчика? Дата указана в его паспорте — напишите её, например: 15.03.2030\n\n"
    "Не знаете — нажмите «Позже»."
)
VERIF_ERRORS = {
    "format": "Напишите дату так: 15.03.2030",
    "no_such_date": "Такой даты нет. Напишите, например: 15.03.2030",
    "past": "Эта дата уже прошла. Напишите дату следующей поверки, например: 15.03.2030",
    "too_far": "Дата слишком далеко. Проверьте год, например: 15.03.2030",
}
VERIF_SAVED = "Записали: поверка до {date}. Напомним заранее."
VERIF_LATER = "Хорошо, пропустим дату поверки."

# --- Кнопки ---
BTN_MANUAL = "Ввести вручную"
BTN_NEW_METER = "Новый счётчик"
BTN_OTHER_ADDRESS = "Другой адрес"
TARIFF_BUTTONS = {1: "1 тариф", 2: "День-ночь", 3: "3 тарифа"}
BTN_YES = "Да"
BTN_REENTER = "Нет, ввести иначе"
BTN_NOT_MINE = "Моего нет — ввести иначе"
BTN_SAVE_AS_IS = "Сохранить как есть"
BTN_FIX = "Исправить"
BTN_PRIVATE_HOUSE = "Частный дом"
BTN_SEND = "Отправить"
BTN_EDIT = "Исправить"
BTN_RETAKE = "Переснять"
BTN_OTHER_METER = "Это другой счётчик"
BTN_SAME_METER = "Номер на фото неверный"
BTN_SERIAL = "Ввести номер"
BTN_PICK_OTHER = "Выбрать другой счётчик"
BTN_CONFIRM_BIG = "Да, всё верно"
BTN_REPLACE = "Заменить"
BTN_KEEP_OLD = "Оставить старое"
BTN_MORE = "Подать ещё"
BTN_LATER = "Позже"
BTN_PAGE_NEXT = "Показать ещё"
BTN_PAGE_FIRST = "К началу списка"
LATER_WORDS = {"позже", "потом", "пропустить", "не знаю"}

# --- Сообщения сервиса подачи (app/readings.py → API мини-приложения) ---
API_ACCEPTED = f"Записали показание. {UK_MOCK}"
API_FLAGGED = "Записали показание и отметили большой прирост для проверки."
API_NO_ACCESS = "Нет доступа к передаче показаний по этому счётчику."
API_BAD_FORMAT = "Не похоже на показание{field}. Напишите, например: {example}"
API_EMPTY = "Не заполнено показание{field}. Напишите, например: {example}"
API_LESS = "Показание меньше прошлого ({prev}). Проверьте цифры."
API_NEEDS_CONFIRM = "Большой прирост: {delta} за {months}. Если всё верно — подтвердите."
API_ALREADY = "За {month} показание уже передано: {old}. Заменить его?"
