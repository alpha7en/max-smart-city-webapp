"""ФИО и телефон: нормализация и проверка."""
from __future__ import annotations

import re
from dataclasses import dataclass

_WORD = re.compile(r"^[А-Яа-яЁё]+(?:[-'’][А-Яа-яЁё]+)*$")
_LATIN = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class NameCheck:
    value: str | None
    error: str | None  # 'empty' | 'latin' | 'digits' | 'chars' | 'too_few' | 'too_many' | 'word_len' | 'too_long'


def _title(word: str) -> str:
    return re.sub(r"[А-Яа-яЁё]+", lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), word)


def normalize_name(text: str) -> str:
    """Схлопывает пробелы и приводит к «Иванова Анна-Мария»."""
    return " ".join(_title(w) for w in (text or "").split())


def validate_name(text: str) -> NameCheck:
    """ФИО: 2–4 слова кириллицей (дефис/апостроф внутри), 2–30 букв в слове, всего ≤ 100."""
    name = normalize_name(text)
    if not name:
        return NameCheck(None, "empty")
    if _LATIN.search(name):
        return NameCheck(None, "latin")
    if re.search(r"\d", name):
        return NameCheck(None, "digits")
    words = name.split()
    if not all(_WORD.match(w) for w in words):
        return NameCheck(None, "chars")
    if len(words) < 2:
        return NameCheck(None, "too_few")
    if len(words) > 4:
        return NameCheck(None, "too_many")
    if any(not 2 <= len(re.sub(r"[-'’]", "", w)) <= 30 for w in words):
        return NameCheck(None, "word_len")
    if len(name) > 100:
        return NameCheck(None, "too_long")
    return NameCheck(name, None)


def first_name(full_name: str | None) -> str:
    """Имя из «Фамилия Имя Отчество» (второе слово); если слово одно — оно."""
    words = (full_name or "").split()
    return words[1] if len(words) > 1 else (words[0] if words else "")


def short_name(full_name: str | None) -> str:
    """«Иванова Анна Сергеевна» → «Анна И.»: имя и инициал фамилии, без чужих персональных данных."""
    words = (full_name or "").split()
    if len(words) < 2:
        return words[0] if words else "Без имени"
    return f"{words[1]} {words[0][0]}."


_GEN_IRREGULAR = {"пётр": "Петра", "петр": "Петра", "павел": "Павла", "лев": "Льва", "любовь": "Любови"}


def genitive(name: str) -> str:
    """Русское имя в родительном падеже (для «доступ от Анны И.»): Анна → Анны, Ольга → Ольги,
    Мария → Марии, Илья → Ильи, Сергей → Сергея, Игорь → Игоря, Олег → Олега, Пётр → Петра.
    Незнакомое окончание (Нелли, Жан-Поль…) — без изменений."""
    low = name.lower()
    if low in _GEN_IRREGULAR:
        return _GEN_IRREGULAR[low]
    if not name or not name[-1].isalpha() or "-" in name:
        return name
    if low.endswith("ия"):
        return name[:-1] + "и"
    if low.endswith("а"):
        return name[:-1] + ("и" if low[-2:-1] in "гкхжчшщ" else "ы")
    if low.endswith("я"):
        return name[:-1] + "и"
    if low.endswith(("й", "ь")):
        return name[:-1] + "я"
    if low[-1] in "бвгджзклмнпрстфхцчшщ":
        return name + "а"
    return name


_MALE_A = {"никита", "илья", "фома", "кузьма", "лука", "савва", "данила", "гаврила", "миша", "гоша", "паша",
           "дима", "лёша", "алёша", "ваня", "вова", "петя", "коля", "толя", "юра"}
_BOTH = {"саша", "женя", "валя", "шура", "слава"}


def is_female(full_name: str | None) -> bool | None:
    """Род по «Фамилия Имя Отчество»: сначала отчество (-вна/-чна → ж, -ич → м), затем имя (-а/-я → ж, кроме
    Никиты, Ильи…). None — не угадать (Саша, Женя, незнакомое имя на -ь)."""
    words = (full_name or "").split()
    if len(words) >= 3:
        patronymic = words[2].lower()
        if patronymic.endswith(("вна", "чна", "кызы")):
            return True
        if patronymic.endswith(("ич", "оглы")):
            return False
    name = first_name(full_name).lower()
    if not name or name in _BOTH:
        return None
    if name == "любовь":
        return True
    if name.endswith(("а", "я")):
        return name not in _MALE_A
    if name.endswith("ь"):
        return False if name in ("игорь", "лазарь") else None
    return False


def past(full_name: str | None, masc: str) -> str:
    """Глагол прошедшего времени по роду: past(«Смирнова Мария Павловна», «вышел») → «вышла»;
    «открыл» → «открыла»; род не угадать → «вышел(а)»."""
    fem = masc[:-2] + "ла" if masc.endswith("ел") else masc + "а"
    female = is_female(full_name)
    return fem if female else masc if female is False else masc + "(а)"


def short_name_gen(full_name: str | None) -> str:
    """«Иванова Анна Сергеевна» → «Анны И.» (short_name в родительном падеже)."""
    words = (full_name or "").split()
    if len(words) < 2:
        return genitive(words[0]) if words else "собственника"
    return f"{genitive(words[1])} {words[0][0]}."


def normalize_phone(text: str | None) -> str | None:
    """Российский номер → '+7XXXXXXXXXX' или None. Принимает 8…, 7…, +7…, 10 цифр."""
    if not text or re.search(r"[^\d\s()+\-.]", text.strip()):
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "3489":
        return None
    return "+7" + digits


def parse_vcf_phone(vcf: str | None) -> str | None:
    """Номер из vCard (первая строка TEL)."""
    for line in (vcf or "").splitlines():
        head, _, value = line.partition(":")
        if head.split(";")[0].strip().upper() == "TEL":
            return normalize_phone(value.strip())
    return None


def format_phone(phone: str | None) -> str:
    """'+79123456789' → '+7 912 345-67-89'."""
    if not phone or not re.fullmatch(r"\+7\d{10}", phone):
        return phone or ""
    d = phone[2:]
    return f"+7 {d[:3]} {d[3:6]}-{d[6:8]}-{d[8:]}"
