"""Поиск адреса: DaData suggest (если есть ключ) с молчаливым откатом на локальный разбор."""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from app.domain.addresses import (
    STREET_TYPES, AddressCandidate, canon_block, clean_flat, format_full, norm_key,
)

log = logging.getLogger(__name__)

DADATA_URL = 'https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address'
MAX_RESULTS = 5
MAX_QUERY = 300

# ---------- локальный разбор ----------

_FEDERAL = (
    (r'москва|мск', 'г Москва'),
    (r'санкт[\s-]*петербург|с\.?\s*-?\s*петербург|спб|питер', 'г Санкт-Петербург'),
    (r'севастополь', 'г Севастополь'),
)
_KNOWN_CITY = re.compile(
    r'^\s*(?:(?:г|город)\.?\s*)?(' + '|'.join(p for p, _ in _FEDERAL) + r')(?=[\s,.]|$)[\s,.]*', re.I)
_TYPED_CITY = re.compile(r'^(?:г|город)(?:\.\s*|\s+)(.+)$', re.I)
_FLAT = re.compile(r'(?:^|[\s,])(?:кв|квартира)\.?\s*№?\s*(\d{1,5}[а-яёa-z]?)\s*$', re.I)
_L = r'[а-яёa-z]'
_TAIL = re.compile(
    r'[\s,]+(?:(?:д|дом)\.?\s*)?'
    rf'(?P<house>\d{{1,4}}(?:\s*/\s*\d{{1,4}})?(?:{_L}(?!{_L}|\d)|\s(?![кс]\s*\d){_L}(?!{_L}|\d))?(?!\d))'  # литера: 5А, 5 А (но не «5 к 1»)
    rf'(?:\s*,?\s*(?P<btype>корпус|корп|к|строение|стр|с|литера|литер|лит)\.?\s*(?P<block>\d{{1,3}}{_L}?|{_L})(?!{_L}|\d))?'
    r'(?:[\s,]+(?P<flat>\d{1,5}' + _L + r'?))?\s*$', re.I)
_REGION_WORDS = {'обл', 'область', 'край', 'респ', 'республика', 'ао'}
_STREET_CANON = {
    'улица': 'ул', 'проспект': 'пр-кт', 'пр-т': 'пр-кт', 'просп': 'пр-кт', 'переулок': 'пер', 'шоссе': 'ш',
    'набережная': 'наб', 'бульвар': 'б-р', 'бул': 'б-р', 'площадь': 'пл', 'пр-д': 'проезд', 'тупик': 'туп',
    'ал': 'аллея', 'микрорайон': 'мкр',
}
_KEEP_LOWER = {'реки', 'канала', 'им', 'имени', 'лет', 'де', 'на', 'и', 'у'}


def _cap(word: str) -> str:
    if not word.islower():
        return word
    parts = word.split('-')
    for i, p in enumerate(parts):
        if p and p[0].isalpha() and p not in _KEEP_LOWER and not (i and parts[i - 1][-1:].isdigit()):
            parts[i] = p[0].upper() + p[1:]
    return '-'.join(parts)


def _words(s: str) -> list[str]:
    return [w.lower().replace('ё', 'е').rstrip('.') for w in s.split()]


def _has_street_type(s: str) -> bool:
    return any(w in STREET_TYPES for w in _words(s))


def _canon_street(s: str) -> str:
    out = []
    for tok in s.split():
        key = tok.lower().replace('ё', 'е').rstrip('.')
        out.append(_STREET_CANON.get(key, key) if key in STREET_TYPES else _cap(tok.strip('.,')))
    return ' '.join(t for t in out if t)


def _flat(v: str | None) -> str | None:
    v = clean_flat(v)
    return (v.lstrip('0') or '0').upper() if v else None


class LocalParser:
    """Регулярный разбор строки адреса. Ничего не проверяет по справочникам: status=unverified."""

    def flat_of(self, text: str) -> str | None:
        m = _FLAT.search(text or '')
        return _flat(m.group(1)) if m else None

    def parse(self, text: str) -> list[AddressCandidate]:
        try:
            c = self._parse(text)
        except Exception:  # разбор не должен ронять бота
            log.exception('local address parse failed')
            return []
        return [c] if c else []

    def _parse(self, text: str) -> AddressCandidate | None:
        s = re.sub(r'\s+', ' ', (text or '').replace('\n', ', ')).strip()
        if not s or len(s) > MAX_QUERY:
            return None
        flat = None
        if m := _FLAT.search(s):
            flat, s = _flat(m.group(1)), s[:m.start()].rstrip(' ,')
        m = _TAIL.search(' ' + s)
        if not m:
            return None
        if m.group('flat') and not flat:
            flat = _flat(m.group('flat'))
        elif m.group('flat'):
            return None  # два номера квартиры — не угадываем
        house = re.sub(r'\s+', '', m.group('house')).upper()
        if not int(re.match(r'\d+', house).group()):
            return None
        block = canon_block(m.group('btype'), m.group('block'))
        region, locality, rest = self._place(s[:max(m.start() - 1, 0)])
        street = _canon_street(rest)
        name = [w for w in _words(street) if w not in STREET_TYPES]
        if len(re.sub(r'[^а-яa-z]', '', ''.join(name))) < 2:
            return None
        c = AddressCandidate(full_text='', region=region, locality=locality, street=street,
                             house=house, block=block, flat=flat, status='unverified', source='local')
        c.full_text = format_full(c)
        return c

    @staticmethod
    def _place(prefix: str) -> tuple[str | None, str | None, str]:
        """Отделить регион и населённый пункт от улицы."""
        region = locality = None
        prefix = prefix.strip(' ,')
        if m := _KNOWN_CITY.match(prefix):
            name = m.group(1).lower()
            region = locality = next(v for p, v in _FEDERAL if re.fullmatch(p, name))
            prefix = prefix[m.end():]
        segs = [x.strip() for x in prefix.split(',') if x.strip()]
        while len(segs) >= 2:
            seg, words = segs[0], _words(segs[0])
            if region is None and any(w in _REGION_WORDS for w in words):
                region = ' '.join(_cap(w.strip('.')) if w.lower().rstrip('.') not in _REGION_WORDS
                                  else w.lower().rstrip('.') for w in seg.split())
            elif locality is None and not re.search(r'\d', seg) and not _has_street_type(seg):
                tm = _TYPED_CITY.match(seg)
                locality = 'г ' + ' '.join(_cap(w) for w in tm.group(1).split()) if tm else \
                    ' '.join(_cap(w) for w in seg.split())
            else:
                break
            segs.pop(0)
        if locality is None and len(segs) == 1 and (tm := _TYPED_CITY.match(segs[0])):
            city, _, street = tm.group(1).partition(' ')  # «г. Казань ул. Баумана»
            if street:
                locality, segs = 'г ' + _cap(city), [street]
        return region, locality, ' '.join(segs)


# ---------- DaData ----------

def _num(v: Any, kind: type) -> Any:
    try:
        return kind(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def from_dadata(s: dict[str, Any]) -> AddressCandidate | None:
    """Подсказка DaData → кандидат; без house_fias_id — None (дом не подтверждён)."""
    d = s.get('data') or {}
    if not d.get('house_fias_id'):
        return None
    c = AddressCandidate(
        full_text='', region=d.get('region_with_type'),
        locality=d.get('settlement_with_type') or d.get('city_with_type'),
        street=d.get('street_with_type'), house=d.get('house'),
        block=canon_block(d.get('block_type'), d.get('block')), flat=clean_flat(d.get('flat')),
        postal_code=d.get('postal_code'), house_fias_id=d.get('house_fias_id'),
        flat_fias_id=d.get('flat_fias_id'), fias_id=d.get('fias_id'), oktmo=d.get('oktmo'),
        house_cadnum=d.get('house_cadnum'), geo_lat=_num(d.get('geo_lat'), float),
        geo_lon=_num(d.get('geo_lon'), float), house_flat_count=_num(d.get('house_flat_count'), int),
        status='verified_flat' if d.get('flat_fias_id') else 'verified_house', source='dadata', raw=s,
    )
    c.full_text = format_full(c)
    return c


class DadataProvider:
    def __init__(self, api_key: str, *, timeout: float = 3.0, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            timeout=timeout, transport=transport,
            headers={'Authorization': f'Token {api_key}', 'Accept': 'application/json'},
        )

    async def suggest(self, query: str, count: int = MAX_RESULTS) -> list[AddressCandidate]:
        r = await self._client.post(DADATA_URL, json={'query': query, 'count': count})
        r.raise_for_status()
        return [c for s in r.json().get('suggestions') or [] if (c := from_dadata(s))]

    async def aclose(self) -> None:
        await self._client.aclose()


# ---------- сервис ----------

class AddressService:
    def __init__(self, api_key: str | None = None, *, timeout: float = 3.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.local = LocalParser()
        self.dadata = DadataProvider(api_key, timeout=timeout, transport=transport) if api_key else None

    @property
    def verified(self) -> bool:
        """True — адреса сверяются с ФИАС через DaData; False — демо-режим (локальный разбор)."""
        return self.dadata is not None

    def parse_local(self, text: str) -> list[AddressCandidate]:
        """Локальный разбор (для «Сохранить как есть», когда DaData дом не нашла)."""
        return self.local.parse((text or '').strip()[:MAX_QUERY])

    async def suggest(self, text: str) -> list[AddressCandidate]:
        """≤ 5 кандидатов; никогда не бросает: сбой DaData → локальный разбор."""
        text = (text or '').strip()[:MAX_QUERY]
        if not text:
            return []
        if self.dadata is None:
            return self.local.parse(text)
        try:
            cands = await self.dadata.suggest(text)
        except Exception as e:  # сеть, таймаут, 4xx/5xx, битый JSON
            status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else '-'
            log.warning('dadata suggest failed: %s status=%s; fallback to local parser', type(e).__name__, status)
            return self.local.parse(text)
        flat = self.local.flat_of(text) or next((c.flat for c in self.local.parse(text)), None)
        out: dict[str, AddressCandidate] = {}
        for c in cands:
            if flat and not c.flat:
                c = c.with_flat(flat)
            out.setdefault(norm_key(c), c)
        return list(out.values())[:MAX_RESULTS]

    async def aclose(self) -> None:
        if self.dadata:
            await self.dadata.aclose()


def get_address_service(settings: Any = None) -> AddressService:
    key = getattr(settings, 'dadata_api_key', None) or getattr(settings, 'DADATA_API_KEY', None) or os.getenv('DADATA_API_KEY') or None
    return AddressService(key.strip() if key else None)
