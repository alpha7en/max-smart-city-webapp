"""HttpRecognizer ↔ services/meter_reader: запрос, разбор ответа, своя уверенность, ошибки."""
from __future__ import annotations

import httpx
import pytest

from app.config import load_settings
from app.integrations.recognizer import (
    CONF_CHECK,
    CONF_OK,
    FEW_DIGITS,
    LOW_CONF,
    HttpRecognizer,
    StubRecognizer,
    get_recognizer,
)

URL = "http://meter-reader:8000/recognize"
# Ответ сервиса из services/meter_reader/README.md.
SAMPLE = {
    "meter_type": "hot_water", "reading": 595.825, "reading_text": "00595.825", "integer_digits": "00595",
    "fraction_digits": "825", "unit": "m3", "tariff": None, "brand": "Бетар", "model": "СГВ-15",
    "serial_number": "123456", "confidence": 0.95, "type_evidence": "red ring and 'СГВ' marking",
}


@pytest.fixture
def photo(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"\xff\xd8fake")
    return str(p)


def rec_with(handler) -> tuple[HttpRecognizer, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrap(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return HttpRecognizer(URL, client=httpx.AsyncClient(transport=httpx.MockTransport(wrap))), seen


async def test_request_and_sample_answer(photo):
    r, seen = rec_with(lambda _: httpx.Response(200, json=SAMPLE))
    rec = await r.recognize(photo, "hot_water", 1)
    body = seen[0].read()
    assert str(seen[0].url) == URL and b'name="image"; filename="a.jpg"' in body
    assert b'name="meter_type"' in body and b"hot_water" in body
    assert rec.values == {"t1": 595_825, "t2": None, "t3": None}
    assert (rec.serial, rec.confidence, rec.stub, rec.error) == ("123456", CONF_OK, False, None)
    assert (rec.brand, rec.model) == ("Бетар", "СГВ-15")


def parse(selected="hot_water", tariffs=1, **answer):
    """Ответ сервиса SAMPLE с правками answer для счётчика selected."""
    return HttpRecognizer._parse({**SAMPLE, **answer}, selected, tariffs)


def test_reading_text_preferred_over_float():
    assert parse(reading=0.1 + 0.2, reading_text="00000.300").values["t1"] == 300
    assert parse(reading_text=None, reading=12.5).values["t1"] == 12_500


@pytest.mark.parametrize("kw", [{"reading_text": None, "reading": None}, {"reading_text": "abc", "reading": None}])
def test_nothing_read_is_zero_confidence(kw):
    rec = parse(**kw)
    assert rec.values["t1"] is None and rec.confidence == 0.0 and rec.serial == "123456"
    assert rec.model == "СГВ-15"


def test_model_is_not_serial():
    rec = parse(serial_number=None, model="ВСХ-15", brand=" ")
    assert (rec.serial, rec.model, rec.brand) == (None, "ВСХ-15", None)


@pytest.mark.parametrize("kw, selected", [
    ({"meter_type": "cold_water"}, "hot_water"),                       # на фото другой счётчик
    ({"meter_type": "unknown"}, "hot_water"),
    ({"reading_text": "01058.3745", "fraction_digits": "3745"}, "hot_water"),  # лишний красный барабан
    ({"reading_text": "105834.745", "integer_digits": "105834"}, "hot_water"),  # лишний чёрный
    ({"meter_type": "electricity", "unit": "kWh"}, "electricity"),       # свет и газ ещё не проверены
    ({"meter_type": "gas"}, "gas"),
])
def test_doubtful_answers_ask_to_check(kw, selected):
    rec = parse(selected, **kw)
    assert rec.values["t1"] is not None and rec.confidence == CONF_CHECK


def test_service_confidence_thresholds():
    """Ниже SERVICE_MIN — не распознали; ниже SERVICE_SURE — прочитали, но с кодом low_confidence."""
    rec = parse(confidence=0.3)
    assert rec.confidence == 0.0 and rec.values["t1"] is None and rec.issues == [LOW_CONF]
    assert not rec.readable(("t1",))
    rec = parse(confidence=0.59, issues=["glare"])
    assert rec.confidence == 0.0 and rec.issues == ["glare"]          # причина от сервиса важнее нашей
    rec = parse(confidence=0.6)                                        # граница: ещё читаем
    assert rec.values["t1"] == 595_825 and rec.confidence == 0.6 and rec.issues == [LOW_CONF]
    rec = parse(confidence=0.79)
    assert rec.readable(("t1",)) and rec.issues == [LOW_CONF]
    rec = parse(confidence=0.8)
    assert rec.confidence == 0.8 and rec.issues == []
    assert parse(confidence=None).confidence == CONF_OK


@pytest.mark.parametrize("tariff, tariffs, field", [
    ("T2", 2, "t2"), ("Т3", 3, "t3"), ("T1", 2, "t1"), (None, 2, "t1"), ("T2", 1, "t1"), ("T3", 2, "t1"),
])
def test_tariff_goes_to_its_field(tariff, tariffs, field):
    rec = parse("electricity", tariffs, meter_type="electricity", tariff=tariff, reading_text="012345.6",
                integer_digits="012345", fraction_digits="6")
    assert {f for f, v in rec.values.items() if v is not None} == {field}
    assert rec.values[field] == 12_345_600


@pytest.mark.parametrize("handler", [
    lambda _: httpx.Response(502, json={"detail": "Yandex Cloud returned 429"}),
    lambda _: httpx.Response(400, json={"detail": "cannot decode image"}),
    lambda _: httpx.Response(200, text="not json"),
    lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused", request=req)),
])
async def test_errors_never_raise(photo, handler):
    r, _ = rec_with(handler)
    rec = await r.recognize(photo, "cold_water", 1)
    assert rec.error and rec.confidence == 0.0 and rec.values == {}


def test_url_selects_http_client():
    assert isinstance(get_recognizer(load_settings({})), StubRecognizer)
    r = get_recognizer(load_settings({"RECOGNIZER_URL": URL}))
    assert isinstance(r, HttpRecognizer) and r.url == URL


# --- Коды проблем с фото: readable / issues / issue_note ---

def test_old_answer_has_no_issues():
    rec = parse()
    assert (rec.issues, rec.note, rec.confidence) == ([], None, CONF_OK)


@pytest.mark.parametrize("answer, issues", [
    ({"readable": False, "issues": ["glare", "angle"]}, ["glare", "angle"]),
    ({"readable": False, "issues": []}, ["digits_not_visible"]),
    ({"readable": False, "issues": ["serial_not_visible"]}, ["digits_not_visible", "serial_not_visible"]),
    ({"reading_text": None, "reading": None}, ["digits_not_visible"]),               # старый ответ без цифр
    ({"readable": False, "issues": ["smudge", "glare", "glare"]}, ["other", "glare"]),  # чужой код → other
])
def test_unreadable_gives_zero_confidence_and_issues(answer, issues):
    rec = parse(**answer, issue_note="  Табло   закрыто бликом ")
    assert rec.confidence == 0.0 and rec.values["t1"] is None and not rec.readable(("t1",))
    assert rec.issues == issues and rec.note == "Табло закрыто бликом" and rec.serial == "123456"


def test_wrong_type_readable_value_kept_with_check():
    rec = parse("cold_water", meter_type="cold_water", issues=["wrong_type"], readable=True)
    assert rec.values["t1"] == 595_825 and rec.confidence == CONF_CHECK and rec.issues == ["wrong_type"]
    assert rec.readable(("t1",))


def test_serial_not_visible_does_not_block_value():
    rec = parse(issues=["serial_not_visible"], readable=True, serial_number=None)
    assert (rec.values["t1"], rec.confidence, rec.serial, rec.issues) == (595_825, CONF_OK, None, ["serial_not_visible"])


def test_heat_meter_in_other_units_asks_to_check():
    rec = parse("heat", meter_type="heat", unit="MWh", reading_text="0012.345", integer_digits="0012",
                fraction_digits="345")
    assert rec.values["t1"] == 12_345 and rec.confidence == CONF_CHECK


async def test_http_error_is_service_issue(photo):
    r, _ = rec_with(lambda _: httpx.Response(502, json={"detail": "Yandex Cloud returned 429"}))
    rec = await r.recognize(photo, "cold_water", 1)
    assert rec.issues == ["service"] and rec.error


async def test_stub_error_caption_with_codes(photo):
    rec = await StubRecognizer().recognize(photo, "cold_water", 1, hint={"caption": "ошибка glare too_dark"})
    assert rec.error and rec.issues == ["glare", "too_dark"]
    rec = await StubRecognizer().recognize(photo, "cold_water", 1, hint={"caption": "Ошибка"})
    assert rec.issues == ["digits_not_visible"]


# --- Скриншот владельца: бледное табло света, «2168», номер не виден ---

BLURRY_2168 = {"meter_type": "electricity", "reading_text": "2168", "integer_digits": "2168",
               "fraction_digits": None, "confidence": 0.95, "readable": True,
               "issues": ["blurry", "serial_not_visible"], "serial_number": None, "unit": "kWh"}


def test_blurry_with_few_digits_is_not_recognized():
    rec = HttpRecognizer._parse(BLURRY_2168, "electricity", 1)
    assert not rec.readable(("t1",)) and rec.values["t1"] is None and rec.confidence == 0.0
    assert rec.issues == ["blurry", "serial_not_visible", FEW_DIGITS]


def test_old_container_answer_without_issues_is_read_as_is():
    """Старый контейнер (без readable/issues): читаем, но только прочитанные цифры и с предупреждением."""
    old = {k: v for k, v in BLURRY_2168.items() if k not in ("readable", "issues")}
    rec = HttpRecognizer._parse(old, "electricity", 1)
    assert rec.values["t1"] == 2_168_000 and rec.texts == {"t1": "2168"}
    assert rec.confidence == CONF_CHECK and rec.issues == [FEW_DIGITS] and rec.serial is None


@pytest.mark.parametrize("answer, readable, issues", [
    # 5 целых — типично для света: размытость — только предупреждение
    ({"reading_text": "12168", "integer_digits": "12168", "issues": ["blurry"]}, True, ["blurry"]),
    # ведущие нули считаются: 6 разрядов на табло
    ({"reading_text": "002168.4", "integer_digits": "002168", "fraction_digits": "4", "issues": ["blurry"]},
     True, ["blurry"]),
    # 4 разряда без признаков плохого фото — читаем, но предупреждаем
    ({"reading_text": "2168", "integer_digits": "2168", "issues": ["glare"]}, True, ["glare", FEW_DIGITS]),
    ({"reading_text": "2168", "integer_digits": "2168", "issues": ["partially_covered"]}, False,
     ["partially_covered", FEW_DIGITS]),
    ({"reading_text": "2168", "integer_digits": "2168", "issues": ["digits_not_visible"]}, False,
     ["digits_not_visible", FEW_DIGITS]),
])
def test_few_digits_rule_edges(answer, readable, issues):
    rec = HttpRecognizer._parse({**BLURRY_2168, "serial_number": "01234567", **answer}, "electricity", 1)
    assert rec.readable(("t1",)) is readable and rec.issues == issues


def test_few_digits_rule_not_for_heat():
    rec = parse("heat", meter_type="heat", reading_text="12.345", integer_digits="12", fraction_digits="345",
                issues=["blurry"], readable=True)
    assert rec.readable(("t1",)) and FEW_DIGITS not in rec.issues


@pytest.mark.parametrize("answer, text", [
    ({}, "595,825"),                                                           # ведущие нули не показываем
    ({"reading_text": "02168", "integer_digits": "02168", "fraction_digits": None}, "2168"),
    ({"reading_text": "12345.6", "integer_digits": "12345", "fraction_digits": "6"}, "12345,6"),
    ({"reading_text": None, "reading": 12.5}, "12,5"),
])
def test_texts_only_digits_that_were_read(answer, text):
    assert parse(**answer).texts == {"t1": text}


async def test_answer_logged_without_personal_data(photo, caplog):
    r, _ = rec_with(lambda _: httpx.Response(200, json={**BLURRY_2168, "serial_number": "0123456789"}))
    with caplog.at_level("INFO", logger="app.integrations.recognizer"):
        await r.recognize(photo, "electricity", 1)
    (msg,) = [m.getMessage() for m in caplog.records if "recognizer answer" in m.getMessage()]
    assert "'reading_text': '2168'" in msg and "'issues': ['blurry', 'serial_not_visible']" in msg
    assert "0123456789" not in msg and "'serial': True" in msg
