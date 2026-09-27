"""Адреса: локальный разбор и провайдер DaData с откатом на локальный (без сети)."""
import asyncio
import json

import httpx
import pytest

from app.domain.addresses import AddressCandidate, format_full, norm_key
from app.integrations.address_service import DADATA_URL, AddressService, LocalParser

parse = LocalParser().parse
MSK = 'г Москва'


def one(text: str) -> AddressCandidate:
    res = parse(text)
    assert len(res) == 1, text
    return res[0]


@pytest.mark.parametrize('text, locality, street, house, block, flat', [
    ('Москва, Арбат 47к1, кв 32', MSK, 'Арбат', '47', 'к1', '32'),
    ('г. Москва, ул. Арбат, д. 47, корп. 1, кв. 32', MSK, 'ул Арбат', '47', 'к1', '32'),
    ('Мск, Ленинский проспект, 119с5', MSK, 'Ленинский пр-кт', '119', 'с5', None),
    ('СПб, Невский пр-кт 28, кв 5', 'г Санкт-Петербург', 'Невский пр-кт', '28', None, '5'),
    ('Московская обл., г. Одинцово, ул. Маршала Жукова, д. 5', 'г Одинцово', 'ул Маршала Жукова', '5', None, None),
    ('ул Ленина 5А кв 7', None, 'ул Ленина', '5А', None, '7'),
    ('Ленина 5 а 12', None, 'Ленина', '5А', None, '12'),
])
def test_local_parser(text, locality, street, house, block, flat):
    c = one(text)
    assert (c.locality, c.street, c.house, c.block, c.flat) == (locality, street, house, block, flat)
    assert (c.status, c.source) == ('unverified', 'local')
    assert c.full_text == format_full(c)


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
    assert seen == {'url': DADATA_URL, 'auth': 'Token test-key',
                    'body': {'query': 'Москва, Арбат 47к1, кв 32', 'count': 5}}
    assert len(res) == 2  # без house_fias_id отброшен, дубль схлопнут
    c = res[0]
    assert (c.source, c.status, c.flat, c.flat_fias_id, c.fias_id) == ('dadata', 'verified_house', '32', None, 'hf-47')
    assert (c.street, c.house, c.block, c.postal_code, c.oktmo) == ('ул Арбат', '47', 'к1', '119002', '45374000')
    assert (c.geo_lat, c.geo_lon, c.house_flat_count) == (55.75, 37.59, 120)
    assert c.full_text == 'г. Москва, ул. Арбат, д. 47, корп. 1, кв. 32'
    assert norm_key(c) == 'h:hf-47|f:32' and c.raw['value']


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
