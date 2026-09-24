"""Форма /recognize: подсказки meter_type и tariffs необязательны и доходят до распознавателя."""
from fastapi.testclient import TestClient

from meter_reader.api import app
from meter_reader.schemas import MeterReading


class FakeRecognizer:
    def __init__(self):
        self.calls = []

    async def recognize(self, data, attempts=2, *, meter_type=None, tariffs=None):
        self.calls.append((meter_type, tariffs))
        return MeterReading(meter_type=meter_type or "unknown", reading=None, reading_text=None,
                            integer_digits=None, fraction_digits=None, unit=None, brand=None, model=None,
                            confidence=None, issues=["digits_not_visible"])


def test_form_hints_passed_and_optional():
    fake = FakeRecognizer()
    app.state.recognizer = fake  # без lifespan: ключи Yandex не нужны
    client = TestClient(app)
    files = {"image": ("m.jpg", b"jpeg", "image/jpeg")}
    assert client.post("/recognize", files=files).status_code == 200
    r = client.post("/recognize", files=files, data={"meter_type": "electricity", "tariffs": "2"})
    assert r.status_code == 200
    body = r.json()
    assert body["readable"] is False and body["issues"] == ["digits_not_visible"] and "issue_note" in body
    client.post("/recognize", files=files, data={"meter_type": "boiler", "tariffs": "x"})
    assert fake.calls == [(None, None), ("electricity", 2), (None, None)]
