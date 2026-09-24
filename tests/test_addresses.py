"""Адреса: локальный разбор, ключ дедупликации, короткие подписи, провайдер DaData (без сети)."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.domain.addresses import (
    AddressCandidate, button_text, format_full, norm_key, short_labels,
)
from app.integrations.address_service import (
    DADATA_URL, AddressService, LocalParser, get_address_service,
)

parse = LocalParser().parse
MSK = 'г Москва'


def one(text: str) -> AddressCandidate:
    res = parse(text)
    assert len(res) == 1, text
    return res[0]


def addr(street, house, block=None, flat=None, loc=MSK, reg=MSK) -> AddressCandidate:
    return AddressCandidate('', reg, loc, street, house, block, flat)


# ---------- локальный парсер ----------

@pytest.mark.parametrize('text, locality, street, house, block, flat', [
    ('Москва, Арбат 47к1, кв 32', MSK, 'Арбат', '47', 'к1', '32'),
    ('Москва Арбат 47к1 32', MSK, 'Арбат', '47', 'к1', '32'),
    ('москва арбат 47к1 кв32', MSK, 'Арбат', '47', 'к1', '32'),
    ('г. Москва, ул. Арбат, д. 47, корп. 1, кв. 32', MSK, 'ул Арбат', '47', 'к1', '32'),
    ('Москва, Арбат, 47 к 1, 32', MSK, 'Арбат', '47', 'к1', '32'),
    ('Москва, Арбат д47 корпус 1 квартира 32', MSK, 'Арбат', '47', 'к1', '32'),
    ('Мск, Ленинский проспект, 119с5', MSK, 'Ленинский пр-кт', '119', 'с5', None),
    ('Москва, Ленинградское шоссе, дом 16А, стр. 3', MSK, 'Ленинградское ш', '16А', 'с3', None),
    ('Москва, 1-я Тверская-Ямская ул., д. 10', MSK, '1-я Тверская-Ямская ул', '10', None, None),
    ('Москва, ул. 8 Марта, 5, 7', MSK, 'ул 8 Марта', '5', None, '7'),
    ('СПб, Невский пр-кт 28, кв 5', 'г Санкт-Петербург', 'Невский пр-кт', '28', None, '5'),
    ('Санкт-Петербург, наб. реки Мойки, 12, 3', 'г Санкт-Петербург', 'наб реки Мойки', '12', None, '3'),
    ('Питер, Садовая 5 лит А', 'г Санкт-Петербург', 'Садовая', '5', 'лит А', None),
    ('Севастополь, ул. Ленина, 10', 'г Севастополь', 'ул Ленина', '10', None, None),
    ('Казань, ул. Баумана, 10/2', 'Казань', 'ул Баумана', '10/2', None, None),
    ('г. Казань ул. Баумана 10', 'г Казань', 'ул Баумана', '10', None, None),
    ('Екатеринбург, проспект Ленина 5, квартира 010', 'Екатеринбург', 'пр-кт Ленина', '5', None, '10'),
    ('Московская обл., г. Одинцово, ул. Маршала Жукова, д. 5', 'г Одинцово', 'ул Маршала Жукова', '5', None, None),
    ('ул Ленина 5А кв 7', None, 'ул Ленина', '5А', None, '7'),
    ('Ленина 5 а 12', None, 'Ленина', '5А', None, '12'),
    ('Ленина 5 к 1', None, 'Ленина', '5', 'к1', None),
    ('Королёва переулок 3, кв. 15', None, 'Королёва пер', '3', None, '15'),
])
def test_local_parser(text, locality, street, house, block, flat):
    c = one(text)
    assert (c.locality, c.street, c.house, c.block, c.flat) == (locality, street, house, block, flat)
    assert (c.status, c.source) == ('unverified', 'local')
    assert c.full_text == format_full(c)


def test_local_parser_region():
    assert one('Москва Арбат 1').region == MSK
    assert one('Московская обл., г. Одинцово, ул. Маршала Жукова, д. 5').region == 'Московская обл'


@pytest.mark.parametrize('text', [
    '', '   ', 'привет', 'Москва', 'Арбат', '47', '123 456', 'Москва 47', 'ул. 47', 'Москва, Арбат',
    'ааа 99999999', 'Арбат 0', 'Арбат 47 кв 3 кв 4', 'Ленина 5бк', 'Арбат 47' * 60,
])
def test_local_parser_rejects(text):
    assert parse(text) == []


# ---------- ключ и форматирование ----------

def test_norm_key_same_address_different_spelling():
    keys = {norm_key(one(t)) for t in (
        'Москва, Арбат 47к1, кв 32', 'г. Москва, ул. Арбат, д. 47, корп. 1, кв. 32',
        'москва арбат 47 к 1 32', 'Москва, улица Арбат, дом 47, корпус 1, квартира 032',
    )}
    keys.add(norm_key(addr('Арбат ул.', '47к1', flat='32')))
    assert keys == {'t:москва|москва|арбат|47|к1|f:32'}


def test_norm_key_yo_and_types():
    assert norm_key(one('Москва, ул. Королёва 5')) == norm_key(one('Москва, Королева улица 5'))


def test_norm_key_differs():
    base = norm_key(one('Москва, Арбат 47к1, кв 32'))
    assert norm_key(one('Москва, Арбат 47к1, кв 33')) != base
    assert norm_key(one('Москва, Арбат 47с1, кв 32')) != base
    assert norm_key(one('Москва, Арбат 47, кв 32')) != base
    assert norm_key(one('Казань, Баумана 10/2')) != norm_key(one('Казань, Баумана 102'))


def test_norm_key_fias():
    c = addr('ул Арбат', '47', flat='032')
    c.house_fias_id = 'ABC-1'
    assert norm_key(c) == 'h:abc-1|f:32'
    assert norm_key(c.with_flat(None)) == 'h:abc-1|f:'


def test_candidate_roundtrip_and_with_flat():
    c = addr('ул Арбат', '47', 'к1')
    c.house_fias_id, c.flat_fias_id, c.fias_id, c.status = 'h1', 'f1', 'f1', 'verified_flat'
    assert AddressCandidate.from_dict({**c.to_dict(), 'junk': 1}) == c
    d = c.with_flat('кв. 32')
    assert (d.flat, d.flat_fias_id, d.fias_id, d.status) == ('32', None, 'h1', 'verified_house')
    assert d.full_text == 'г. Москва, ул. Арбат, д. 47, корп. 1, кв. 32'
    assert c.flat is None  # исходный не мутирован


def test_format_full_and_button_text():
    c = addr('ул Арбат', '47', 'к1', '32')
    assert format_full(c) == 'г. Москва, ул. Арбат, д. 47, корп. 1, кв. 32'
    assert button_text(c) == 'Москва, ул. Арбат 47к1, кв 32'
    o = addr('ул Маршала Жукова', '5', loc='г Одинцово', reg='Московская обл')
    assert format_full(o) == 'Московская обл., г. Одинцово, ул. Маршала Жукова, д. 5'
    assert 'Московская' not in button_text(o)
    long = addr('ул Большая Серпуховская имени Очень Длинного Человека', '12', 'с3', '1005')
    t = button_text(long)
    assert len(t) <= 40 and t.endswith('12с3, кв 1005') and '…' in t


# ---------- короткие подписи ----------

def test_short_labels_one_city():
    labels = short_labels([addr('ул Арбат', '47', 'к1', '32'), addr('ул Тверская', '7', flat='105'),
                           addr('Ленинский пр-кт', '30')])
    assert labels == ['Арбат 47к1, кв 32', 'Тверская 7, кв 105', 'Ленинский 30']


def test_short_labels_two_cities_same_street():
    spb = 'г Санкт-Петербург'
    labels = short_labels([addr('ул Арбат', '10', flat='5'), addr('ул Арбат', '10', flat='5', loc=spb, reg=spb),
                           addr('ул Ленина', '1', loc='г Екатеринбург', reg='Свердловская обл')])
    assert labels == ['Мск, Арбат 10, кв 5', 'СПб, Арбат 10, кв 5', 'Екатер., Ленина 1']


def test_short_labels_long_street():
    [label] = short_labels([addr('ул Большая Никитская', '24', 'с1', '105')])
    assert label == 'Большая Ник… 24с1, кв105'
    assert len(label) == 24


def test_short_labels_type_only_when_ambiguous():
    assert short_labels([addr('ул Ленина', '5', flat='1'), addr('пер Ленина', '5', flat='1')]) == \
        ['ул. Ленина 5, кв 1', 'пер. Ленина 5, кв 1']
    assert short_labels([addr('Ленинский пр-кт', '5'), addr('Ленинский пер', '5')]) == \
        ['Ленинский пр-кт 5', 'Ленинский пер. 5']


def test_short_labels_collisions():
    a, b = (addr('ул Ленина', '5', flat='1', loc='г Советск', reg=r) for r in ('Калининградская обл', 'Кировская обл'))
    la, lb = short_labels([a, b])
    assert la != lb and lb.endswith(' (2)') and len(lb) <= 24
    long = [addr(f'ул Академика Королева {w}', '1', flat='1') for w in ('Верхняя', 'Нижняя', 'Средняя')]
    labels = short_labels(long)
    assert len(set(labels)) == 3 and all(len(x) <= 24 and x.split(' (')[0].endswith('1, кв1') for x in labels)


def test_short_labels_never_cut_house_and_flat():
    [label] = short_labels([addr('ул Очень Длинная Улица', '1234', 'с12', '10005')], max_len=20)
    assert label.endswith('1234с12, кв10005')


# ---------- DaData ----------

def sugg(house='47', block='1', flat=None, house_fias='hf-47', flat_fias=None, street='ул Арбат') -> dict:
    return {'value': f'г Москва, {street}, д {house}', 'data': {
        'postal_code': '119002', 'region_with_type': MSK, 'city_with_type': MSK, 'settlement_with_type': None,
        'street_with_type': street, 'house_type': 'д', 'house': house, 'block_type': 'к' if block else None,
        'block': block, 'flat': flat, 'house_fias_id': house_fias, 'flat_fias_id': flat_fias,
        'fias_id': flat_fias or house_fias, 'oktmo': '45374000', 'house_cadnum': '77:01:0001', 'geo_lat': '55.75',
        'geo_lon': '37.59', 'house_flat_count': '120'}}


def service(handler) -> AddressService:
    return AddressService('test-key', transport=httpx.MockTransport(handler))


def run(svc: AddressService, text: str) -> list[AddressCandidate]:
    async def go():
        try:
            return await svc.suggest(text)
        finally:
            await svc.aclose()
    return asyncio.run(go())


def test_dadata_success_maps_fields_and_moves_flat():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(url=str(req.url), auth=req.headers['Authorization'], body=json.loads(req.content))
        return httpx.Response(200, json={'suggestions': [
            sugg(), sugg(house='47', block=None, house_fias='hf-47a'), sugg(house_fias=None), sugg(),
        ]})

    res = run(service(handler), 'Москва, Арбат 47к1, кв 32')
    assert seen == {'url': DADATA_URL, 'auth': 'Token test-key', 'body': {'query': 'Москва, Арбат 47к1, кв 32', 'count': 5}}
    assert len(res) == 2  # без house_fias_id отброшен, дубль схлопнут
    c = res[0]
    assert (c.source, c.status, c.flat, c.flat_fias_id, c.fias_id) == ('dadata', 'verified_house', '32', None, 'hf-47')
    assert (c.street, c.house, c.block, c.postal_code, c.oktmo) == ('ул Арбат', '47', 'к1', '119002', '45374000')
    assert (c.geo_lat, c.geo_lon, c.house_flat_count) == (55.75, 37.59, 120)
    assert c.full_text == 'г. Москва, ул. Арбат, д. 47, корп. 1, кв. 32'
    assert norm_key(c) == 'h:hf-47|f:32' and c.raw['value']


def test_dadata_verified_flat():
    res = run(service(lambda r: httpx.Response(200, json={'suggestions': [sugg(flat='32', flat_fias='ff-32')]})),
              'Москва, Арбат 47к1, кв 32')
    assert [(c.status, c.flat, c.flat_fias_id) for c in res] == [('verified_flat', '32', 'ff-32')]


def test_dadata_empty():
    assert run(service(lambda r: httpx.Response(200, json={'suggestions': []})), 'Москва, Нетакой 1') == []


def test_dadata_max_five():
    many = [sugg(house=str(i), house_fias=f'h{i}') for i in range(1, 9)]
    assert len(run(service(lambda r: httpx.Response(200, json={'suggestions': many})), 'Арбат')) == 5


def _timeout(req):
    raise httpx.ReadTimeout('slow', request=req)


@pytest.mark.parametrize('handler', [
    lambda r: httpx.Response(500),
    lambda r: httpx.Response(429, json={'message': 'limit'}),
    lambda r: httpx.Response(200, content=b'not json'),
    _timeout,
])
def test_dadata_failure_falls_back_to_local(handler):
    res = run(service(handler), 'Москва, Арбат 47к1, кв 32')
    assert [(c.source, c.status, c.street, c.house, c.block, c.flat) for c in res] == \
        [('local', 'unverified', 'Арбат', '47', 'к1', '32')]


def test_no_key_uses_local(monkeypatch):
    monkeypatch.delenv('DADATA_API_KEY', raising=False)
    svc = get_address_service(SimpleNamespace(DADATA_API_KEY=''))
    assert not svc.verified
    assert [c.source for c in run(svc, 'Москва, Арбат 1')] == ['local']
    assert run(get_address_service(None), '  ') == []


def test_get_address_service_key_sources(monkeypatch):
    monkeypatch.delenv('DADATA_API_KEY', raising=False)
    monkeypatch.setattr(AddressService, '__init__', lambda self, key=None: setattr(self, 'key', key))
    assert get_address_service(SimpleNamespace(DADATA_API_KEY='k1')).key == 'k1'
    monkeypatch.setenv('DADATA_API_KEY', ' k2 ')
    assert get_address_service(object()).key == 'k2'
