"""HttpRecognizer ↔ services/recognizer: запрос, разбор ответа, своя уверенность, ошибки."""
from __future__ import annotations

import httpx
import pytest

from app.config import load_settings
from app.integrations.recognizer import (
    CONF_OK,
    FEW_DIGITS,
    LOW_CONF,
    HttpRecognizer,
    StubRecognizer,
    get_recognizer,
)

URL = "http://recognizer:8000/recognize"
# Ответ сервиса из services/recognizer/README.md.
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


@pytest.mark.parametrize("kw", [{"reading_text": None, "reading": None}, {"reading_text": "abc", "reading": None}])
def test_nothing_read_is_zero_confidence(kw):
    rec = parse(**kw)
    assert rec.values["t1"] is None and rec.confidence == 0.0 and rec.serial == "123456"
    assert rec.model == "СГВ-15"


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


BLURRY_2168 = {"meter_type": "electricity", "reading_text": "2168", "integer_digits": "2168",
               "fraction_digits": None, "confidence": 0.95, "readable": True,
               "issues": ["blurry", "serial_not_visible"], "serial_number": None, "unit": "kWh"}


def test_blurry_with_few_digits_is_not_recognized():
    rec = HttpRecognizer._parse(BLURRY_2168, "electricity", 1)
    assert not rec.readable(("t1",)) and rec.values["t1"] is None and rec.confidence == 0.0
    assert rec.issues == ["blurry", "serial_not_visible", FEW_DIGITS]
