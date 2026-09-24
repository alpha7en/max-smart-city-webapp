"""Адреса: модель кандидата, ключ дедупликации, короткие подписи. Чистые функции, без I/O."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Literal

Status = Literal['verified_flat', 'verified_house', 'unverified']
Source = Literal['dadata', 'local']


@dataclass
class AddressCandidate:
    full_text: str
    region: str | None
    locality: str | None
    street: str | None
    house: str | None
    block: str | None          # канонично: "к1" (корпус), "с5" (строение), "лит А"; без типа = корпус
    flat: str | None
    postal_code: str | None = None
    house_fias_id: str | None = None
    flat_fias_id: str | None = None
    fias_id: str | None = None
    oktmo: str | None = None
    house_cadnum: str | None = None
    geo_lat: float | None = None
    geo_lon: float | None = None
    house_flat_count: int | None = None
    status: Status = 'unverified'
    source: Source = 'local'
    raw: dict | None = None

    def with_flat(self, flat: str | None) -> AddressCandidate:
        flat = clean_flat(flat)
        if flat == self.flat:
            return replace(self)
        c = replace(
            self, flat=flat, flat_fias_id=None,
            fias_id=self.house_fias_id or (None if self.flat_fias_id else self.fias_id),
            status='verified_house' if self.house_fias_id else 'unverified',
        )
        c.full_text = format_full(c)
        return c

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AddressCandidate:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


# ---------- словари типов ----------

# тип улицы → (краткая форма для подписей)
STREET_TYPES: dict[str, str] = {
    'ул': 'ул.', 'улица': 'ул.',
    'пр-кт': 'пр-кт', 'проспект': 'пр-кт', 'пр-т': 'пр-кт', 'просп': 'пр-кт',
    'пер': 'пер.', 'переулок': 'пер.',
    'ш': 'ш.', 'шоссе': 'ш.',
    'наб': 'наб.', 'набережная': 'наб.',
    'б-р': 'б-р', 'бульвар': 'б-р', 'бул': 'б-р',
    'пл': 'пл.', 'площадь': 'пл.',
    'проезд': 'пр-д', 'пр-д': 'пр-д',
    'туп': 'туп.', 'тупик': 'туп.',
    'аллея': 'ал.', 'ал': 'ал.',
    'мкр': 'мкр', 'микрорайон': 'мкр',
    'линия': 'линия', 'тракт': 'тракт',
}
# сокращения, которым в полном адресе нужна точка
_DOTTED = {'г', 'ул', 'пер', 'ш', 'наб', 'пл', 'обл', 'пос', 'п', 'с', 'д', 'туп', 'ал', 'респ', 'рп', 'пгт', 'дер', 'ст'}
_LOCALITY_TYPES = {'г', 'город', 'пос', 'поселок', 'п', 'пгт', 'рп', 'с', 'село', 'д', 'дер', 'деревня', 'ст', 'станица', 'х', 'хутор'}
_REGION_TYPES = {'обл', 'область', 'край', 'респ', 'республика', 'ао', 'аобл'}
# что выкидываем при нормализации ключа
_NORM_DROP = (
    {'ул', 'улица', 'пр-кт', 'проспект', 'пер', 'переулок', 'ш', 'шоссе', 'наб', 'набережная', 'бульвар', 'б-р',
     'д', 'дом', 'к', 'корп', 'корпус', 'стр', 'строение', 'кв', 'квартира'}
    | _LOCALITY_TYPES | _REGION_TYPES
)
_ADJ_END = re.compile(r'(ий|ый|ой|ая|яя|ое|ее)$')
_WORD = re.compile(r'[0-9a-zа-я]+(?:-[0-9a-zа-я]+)*')
_BLOCK = re.compile(r'^(корпус|корп|к|строение|стр|с|литера|литер|лит)\.?\s*(.+)$', re.I)
_BLOCK_KIND = {'к': 'к', 'корп': 'к', 'корпус': 'к', 'с': 'с', 'стр': 'с', 'строение': 'с',
               'лит': 'лит', 'литер': 'лит', 'литера': 'лит'}
_CITY_SHORT = {'москва': 'Мск', 'санкт-петербург': 'СПб'}


def _low(s: str) -> str:
    return s.lower().replace('ё', 'е')


def clean_flat(flat: str | None) -> str | None:
    """«кв. 32» → «32»; пусто → None."""
    if flat is None:
        return None
    f = re.sub(r'^(кв|квартира)\.?\s*', '', str(flat).strip(), flags=re.I).strip()
    return f or None


def block_parts(block: str | None) -> tuple[str, str] | None:
    """Блок → (вид, номер): вид ∈ {'к','с','лит'} или '' для неизвестного."""
    if not block or not block.strip():
        return None
    b = block.strip()
    m = _BLOCK.match(b)
    if m:
        return _BLOCK_KIND[m.group(1).lower()], m.group(2).strip()
    return ('к', b) if b[0].isdigit() else ('', b)


def canon_block(kind: str | None, num: str | None) -> str | None:
    if not num or not str(num).strip():
        return None
    num = str(num).strip()
    k = _BLOCK_KIND.get(_low(kind or '').rstrip('.'), 'к' if num[0].isdigit() else '')
    if k == 'лит':
        return f'лит {num.upper()}'
    return f'{k}{num}' if k else num


def house_short(c: AddressCandidate) -> str:
    """«47к1», «119с5», «5А», «10/2»."""
    h = (c.house or '').strip()
    bp = block_parts(c.block)
    if not bp:
        return h
    kind, num = bp
    if kind == 'лит':
        return f'{h}{num}'
    return f'{h}{kind}{num}' if kind else f'{h} {num}'


def _split_type(value: str | None, types: dict[str, str] | set[str]) -> tuple[str | None, str]:
    """«ул Арбат» → ('ул', 'Арбат'); «Ленинский пр-кт» → ('пр-кт', 'Ленинский')."""
    if not value:
        return None, ''
    kind, rest = None, []
    for tok in value.split():
        key = _low(tok).rstrip('.')
        if kind is None and key in types:
            kind = key
        else:
            rest.append(tok)
    return kind, ' '.join(rest)


def street_parts(street: str | None) -> tuple[str | None, str]:
    """(краткий тип или None, название)."""
    kind, name = _split_type(street, STREET_TYPES)
    return (STREET_TYPES[kind] if kind else None), name


def _street_disp(kind: str | None, name: str) -> str:
    if not kind or not name:
        return name
    last = _low(name.split()[-1])
    return f'{name} {kind}' if _ADJ_END.search(last) else f'{kind} {name}'


def locality_name(c: AddressCandidate) -> str:
    """Название населённого пункта без типа («г Москва» → «Москва»)."""
    src = c.locality or c.region or ''
    return _split_type(src, _LOCALITY_TYPES | _REGION_TYPES)[1] or src


def city_short(name: str) -> str:
    low = _low(name)
    if low in _CITY_SHORT:
        return _CITY_SHORT[low]
    return name if len(name) <= 7 else name[:6] + '.'


# ---------- ключ дедупликации ----------

def _norm(s: str | None) -> str:
    toks = [t for t in _WORD.findall(_low(s or '')) if t not in _NORM_DROP]
    return ''.join(sorted(t.replace('-', '') for t in toks))


def _norm_house(s: str | None) -> str:
    s = re.sub(r'^\s*(д|дом)\b\.?', '', _low(s or ''))
    return re.sub(r'[^0-9a-zа-я/]', '', s)


def _norm_block(block: str | None) -> str:
    bp = block_parts(block)
    return _low(bp[0] + re.sub(r'[\s.]', '', bp[1])) if bp else ''


def norm_flat(flat: str | None) -> str:
    f = re.sub(r'[^0-9a-zа-я/]', '', _low(clean_flat(flat) or ''))
    return f.lstrip('0') or ('0' if f else '')


def norm_key(c: AddressCandidate) -> str:
    """h:{house_fias_id}|f:{flat} или t:{регион|город|улица|дом|корпус}|f:{flat}."""
    flat = norm_flat(c.flat)
    if c.house_fias_id:
        return f'h:{c.house_fias_id.lower()}|f:{flat}'
    house, block = c.house, c.block
    if house and not block:
        house, block = split_house(house)
    parts = [_norm(c.region), _norm(c.locality), _norm(c.street), _norm_house(house), _norm_block(block)]
    return 't:' + '|'.join(parts) + f'|f:{flat}'


_HOUSE_SPLIT = re.compile(
    r'^\s*(?:(?:д|дом)\.?\s*)?(\d{1,4}(?:\s*/\s*\d{1,4})?(?:\s*[а-яa-z](?![а-яa-z\d]))?)'
    r'(?:\s*,?\s*(корпус|корп|к|строение|стр|с|литера|литер|лит)\.?\s*(\d{1,3}[а-яa-z]?|[а-яa-z]))?\s*$', re.I)


def split_house(text: str) -> tuple[str, str | None]:
    """«47к1» → ('47', 'к1'); «д. 5А» → ('5А', None); не распознали → (text, None)."""
    m = _HOUSE_SPLIT.match(text or '')
    if not m:
        return text, None
    house = re.sub(r'\s+', '', m.group(1)).upper()
    return house, canon_block(m.group(2), m.group(3))


# ---------- форматирование ----------

def _dotted(value: str) -> str:
    return ' '.join(t + '.' if _low(t) in _DOTTED else t for t in value.split())


def format_full(c: AddressCandidate) -> str:
    """«г. Москва, ул. Арбат, д. 47, корп. 1, кв. 32»."""
    out: list[str] = []
    if c.region and (not c.locality or _norm(c.region) != _norm(c.locality)):
        out.append(_dotted(c.region))
    if c.locality:
        out.append(_dotted(c.locality))
    if c.street:
        out.append(_dotted(c.street))
    if c.house:
        out.append(f'д. {c.house}')
    bp = block_parts(c.block)
    if bp:
        kind, num = bp
        out.append({'к': f'корп. {num}', 'с': f'стр. {num}', 'лит': f'лит. {num}'}.get(kind, num))
    if c.flat:
        out.append(f'кв. {c.flat}')
    return ', '.join(out)


def _truncate(s: str, n: int) -> str:
    """Обрезать до n символов с «…» (n включает «…»)."""
    if len(s) <= n:
        return s
    return s[:max(n - 1, 1)].rstrip(' ,.-') + '…' if n >= 2 else ''


def _fit(prefix: str, street: str, house: str, flat: str | None, suffix: str, max_len: int,
         street_short: str | None = None) -> str:
    """Собрать подпись; дом и квартиру не режем никогда."""
    def build(st: str, fl: str) -> str:
        core = f'{st} {house}'.strip() if st else house
        return f'{prefix}{core}{fl}{suffix}'

    fl = f', кв {flat}' if flat else ''
    s = build(street, fl)
    if len(s) <= max_len:
        return s
    if flat:
        fl = f', кв{flat}'
        s = build(street, fl)
        if len(s) <= max_len:
            return s
    if street_short is not None and street_short != street:
        street = street_short
        s = build(street, fl)
        if len(s) <= max_len:
            return s
    room = max_len - len(build('', fl)) - 1  # место под улицу с пробелом
    if room >= 2:
        return build(_truncate(street, room), fl)
    return build('', fl)


def _parts(c: AddressCandidate) -> dict[str, Any]:
    kind, name = street_parts(c.street)
    return {
        'loc': (_norm(c.region), _norm(c.locality)),
        'city': locality_name(c),
        'kind': kind, 'name': name, 'name_key': _norm(name),
        'house': house_short(c), 'flat': c.flat,
    }


def short_labels(cands: list[AddressCandidate], max_len: int = 24) -> list[str]:
    """Подписи для всего набора адресов пользователя: «Арбат 47к1, кв 32», «Мск, Арбат 47к1, кв 32»."""
    ps = [_parts(c) for c in cands]
    multi_city = len({p['loc'] for p in ps}) >= 2
    kinds: dict[tuple, set] = defaultdict(set)
    for p in ps:
        kinds[(p['loc'], p['name_key'])].add(p['kind'])

    def label(i: int, city: bool, typed: bool, suffix: str = '') -> str:
        p = ps[i]
        name = p['name'] or ('' if city else p['city'])
        street = _street_disp(p['kind'], name) if typed else name
        prefix = f"{city_short(p['city'])}, " if city and p['city'] else ''
        return _fit(prefix, street, p['house'], p['flat'], suffix, max_len, street_short=name)

    city = [multi_city] * len(ps)
    typed = [len(kinds[(p['loc'], p['name_key'])]) > 1 for p in ps]
    labels = [label(i, city[i], typed[i]) for i in range(len(ps))]

    def dup_groups() -> list[list[int]]:
        g: dict[str, list[int]] = defaultdict(list)
        for i, s in enumerate(labels):
            g[s].append(i)
        return [ix for ix in g.values() if len(ix) > 1]

    for ix in dup_groups():  # коллизия: уточняем тем, чем адреса реально различаются
        by_city = len({ps[i]['loc'] for i in ix}) > 1
        by_type = len({ps[i]['kind'] for i in ix}) > 1
        for i in ix:
            city[i], typed[i] = city[i] or by_city, typed[i] or by_type
            labels[i] = label(i, city[i], typed[i])
    for ix in dup_groups():  # не помогло (обрезка, одинаковые подписи) — нумеруем
        for n, i in enumerate(ix[1:], start=2):
            labels[i] = label(i, city[i], typed[i], f' ({n})')
    return labels


def button_text(c: AddressCandidate, max_len: int = 40) -> str:
    """Текст кнопки выбора варианта: «Москва, ул. Арбат 47к1, кв 32» (без региона, ≤ 40)."""
    kind, name = street_parts(c.street)
    ltype, lname = _split_type(c.locality, _LOCALITY_TYPES)
    city_full = (lname if ltype in (None, 'г', 'город') else _dotted(c.locality or '')) if c.locality else ''
    house = house_short(c)
    tail = f', кв {c.flat}' if c.flat else ''

    def build(city: str, street: str) -> str:
        body = ' '.join(x for x in (street, house) if x)
        return (f'{city}, {body}' if city and body else city or body) + tail

    street_full = _street_disp(kind, name) if name else ''
    city_abbr = city_short(lname) if lname else ''
    for city, street in ((city_full, street_full), (city_abbr, street_full), (city_abbr, name)):
        s = build(city, street)
        if len(s) <= max_len:
            return s
    for city in (city_abbr, ''):
        room = max_len - len(build(city, '')) - 1  # пробел между улицей и домом
        if name and room >= 2:
            return build(city, _truncate(name, room))
    return build('', '')
