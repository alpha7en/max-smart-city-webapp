"""Качество фото и серийный номер: живой баг владельца (размытое табло света «2168», номер не виден).

Бот не должен: показывать «2168,00» (дробную часть модель не читала), молча пропускать номер счётчика,
прятать предупреждения сервиса за общим «Проверьте цифры внимательно»."""
from __future__ import annotations

import httpx
import pytest

from app.bot.states import S
from app.bot.texts import common as C
from app.bot.texts import submission as T
from app.integrations.recognizer import HttpRecognizer
from tests.test_recognizer import BLURRY_2168
from tests.test_submission import buttons, meter, readings, register, state, to_review

LIGHT = "Свет · Арбат 47к1, кв 32"
COLD = "Хол. вода · Арбат 47к1, кв 32"
OLD_2168 = {k: v for k, v in BLURRY_2168.items() if k not in ("readable", "issues")}  # старый контейнер


def service(answer: dict) -> HttpRecognizer:
    """Настоящий клиент meter-reader, сервис отвечает answer."""
    return HttpRecognizer("http://meter-reader:8000/recognize",
                          client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=answer))))


@pytest.fixture
async def user(repo):
    return await register(repo)


async def test_screenshot_blurry_2168_fails_with_reasons_then_manual_asks_serial(chat, api, repo, deps, user):
    mid = await meter(repo, user, "electricity", serial=None)
    deps.recognizer = service(BLURRY_2168)
    await to_review(chat, LIGHT)
    text = api.last_text()
    assert text.startswith(T.FAILED_HEAD)
    assert T.ISSUE_TEXTS["blurry"] in text and T.ISSUE_TEXTS["few_digits"] in text and T.FAILED_SERIAL in text
    assert "2168" not in text and "Проверьте цифры" not in text
    assert buttons(api) == [T.BTN_RETAKE, T.BTN_MANUAL, C.BTN_CANCEL]
    assert (await state(repo)).state == S.SUB_AWAIT_PHOTO
    await chat.press(T.BTN_MANUAL)
    await chat.text("2168")
    assert (await state(repo)).state == S.SUB_SERIAL_INPUT  # номер обязателен и без фото
    await chat.text("ГОСТ 31818")
    assert api.last_text().startswith("Это не похоже на серийный номер")
    await chat.text("12")
    assert api.last_text().startswith("Слишком короткий номер — в нём обычно от 8 до 16 цифр")
    await chat.text("01234567")
    review = api.last_text()
    assert "Показание: **2168 кВт·ч**" in review and ",00" not in review
    assert "Серийный номер: **01234567** — сохраним" in review
    await chat.press(T.BTN_SEND)
    assert "за **октябрь**.\nЭлектричество · Арбат 47к1, кв 32\n**2168 кВт·ч**" in api.last_text()
    assert (await readings(repo, mid))[-1]["t1"] == 2_168_000
    assert (await repo.get_meter(mid))["serial"] == "01234567"


async def test_old_container_answer_honest_warning_and_serial_required(chat, api, repo, deps, user):
    mid = await meter(repo, user, "electricity", serial=None)
    deps.recognizer = service(OLD_2168)
    await to_review(chat, LIGHT)
    assert api.last_text() == f"{LIGHT}\n\n{T.SERIAL_MISSING}"
    assert buttons(api) == [T.BTN_RETAKE, T.BTN_SERIAL, C.BTN_CANCEL]
    assert T.BTN_SEND not in buttons(api)
    assert await readings(repo, mid) == []  # к «Отправить» без номера не пускаем
    await chat.text("01234567")  # номер можно написать сразу, без кнопки
    review = api.last_text()
    assert review.startswith(f"{LIGHT}\n\nПоказание: **2168 кВт·ч**\nСерийный номер: **01234567** — сохраним")
    assert T.REVIEW_WARN["few_digits"].format(typical="обычно 5–6") in review
    assert T.CHECK_DIGITS not in review and ",00" not in review
    await chat.press(T.BTN_SEND)
    assert "**2168 кВт·ч**" in api.last_text() and "2168,00" not in api.last_text()


async def test_serial_missing_retake_and_back(chat, api, repo, deps, user):
    await meter(repo, user, "electricity", serial=None)
    deps.recognizer = service(OLD_2168)
    await to_review(chat, LIGHT)
    await chat.press(T.BTN_SERIAL)
    assert (await state(repo)).state == S.SUB_SERIAL_INPUT and buttons(api) == [C.BTN_BACK, C.BTN_CANCEL]
    await chat.press(C.BTN_BACK)
    assert (await state(repo)).state == S.SUB_SERIAL_MISSING
    await chat.press(T.BTN_RETAKE)
    assert (await state(repo)).state == S.SUB_AWAIT_PHOTO
    deps.recognizer = service({**OLD_2168, "reading_text": "012168.5", "integer_digits": "012168",
                               "fraction_digits": "5", "serial_number": "01234567"})
    await chat.photo("https://i/retake")  # счётчик запомнен — новое фото сразу распознаём
    review = api.last_text()
    assert "Показание: **12168,5 кВт·ч**" in review and "Серийный номер: **01234567** — сохраним" in review


async def test_saved_serial_not_on_photo_warns_but_continues(chat, api, repo, deps, user):
    await meter(repo, user, "electricity", serial="0112 3456 7890")
    deps.recognizer = service({**BLURRY_2168, "reading_text": "002168.4", "integer_digits": "002168",
                               "fraction_digits": "4"})
    await to_review(chat, LIGHT)
    review = api.last_text()
    assert (await state(repo)).state == S.SUB_REVIEW
    assert "Показание: **2168,4 кВт·ч**" in review
    assert T.REVIEW_WARN["blurry"] in review and T.CHECK_DIGITS not in review
    assert "Номер на фото не виден — убедитесь, что это счётчик с номером 011234567890." in review
    assert buttons(api)[0] == T.BTN_SEND


async def test_low_service_confidence_without_issues_is_honest(chat, api, repo, deps, user):
    await meter(repo, user, serial="18-123456")
    deps.recognizer = service({"meter_type": "cold_water", "reading_text": "00123.456", "integer_digits": "00123",
                               "fraction_digits": "456", "serial_number": "18-123456", "confidence": 0.7})
    await to_review(chat, COLD)
    review = api.last_text()
    assert T.REVIEW_WARN["low_confidence"] in review and T.CHECK_DIGITS not in review
    deps.recognizer = service({"meter_type": "cold_water", "reading_text": "00123.456", "confidence": 0.5,
                               "serial_number": "18-123456"})
    await chat.photo("https://i/low")
    assert T.ISSUE_TEXTS["low_confidence"] in api.last_text() and api.last_text().startswith(T.FAILED_HEAD)


async def test_clear_answer_keeps_previous_screen(chat, api, repo, deps, user):
    """Чёткое фото воды: прежний экран без предупреждений; свет (не проверен) — прежнее «Проверьте цифры»."""
    await meter(repo, user, serial="18-123456")
    deps.recognizer = service({"meter_type": "cold_water", "reading_text": "00123.456", "integer_digits": "00123",
                               "fraction_digits": "456", "serial_number": "18-123456", "confidence": 0.95,
                               "readable": True, "issues": []})
    await to_review(chat, COLD)
    assert api.last_text() == (f"{COLD}\n\nПоказание: **123,456 м³**\nСерийный номер: **18-123456** — совпадает"
                               f"\n\n{T.REVIEW_QUESTION}")
    assert buttons(api) == [T.BTN_SEND, T.BTN_EDIT, T.BTN_RETAKE, C.BTN_CANCEL]
    await chat.press(C.BTN_CANCEL)
    await meter(repo, user, "electricity", serial="01234567")
    deps.recognizer = service({"meter_type": "electricity", "reading_text": "012345.67", "integer_digits": "012345",
                               "fraction_digits": "67", "serial_number": "01234567", "confidence": 0.95,
                               "readable": True, "issues": []})
    await to_review(chat, LIGHT, "https://i/light")
    review = api.last_text()
    assert "Показание: **12345,67 кВт·ч**" in review and T.CHECK_DIGITS in review
