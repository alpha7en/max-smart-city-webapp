/* Мини-приложение «ЖКХ» для MAX. Vanilla JS, без сборки.
   Контракт API: SPEC §6. Все запросы несут заголовок X-Max-Init-Data.
   Иконки — inline SVG, пути нарисованы по мотивам набора Lucide (ISC License, https://lucide.dev). */
'use strict';
(function () {
  const W = window.WebApp || null;
  try { if (W && W.ready) W.ready(); } catch (e) { /* мост MAX недоступен */ }

  const qs = new URLSearchParams(location.search);
  const INIT = (W && W.initData) || '';
  const DEV_USER = qs.get('dev_user') || ''; // только для локальной разработки (DEV_AUTH=true на сервере)
  const metaBase = (document.querySelector('meta[name="api-base"]') || {}).content || '';
  // ?api= — только локальный бэкенд: иначе initData ушёл бы на чужой хост.
  function localApi(s) {
    try { const u = new URL(s); return /^https?:$/.test(u.protocol) && /^(localhost|127\.0\.0\.1)$/.test(u.hostname) ? u.origin + u.pathname : ''; } catch (e) { return ''; }
  }
  const API = (localApi(qs.get('api') || '') || metaBase).trim().replace(/\/+$/, '');

  const $view = document.getElementById('view');
  const $title = document.getElementById('title');
  const $eb = document.getElementById('eb');
  const $ava = document.getElementById('ava');
  const $back = document.getElementById('back');
  const $bar = document.getElementById('bar');
  const nativeBack = !!(W && W.BackButton && typeof W.BackButton.show === 'function');

  // ---------- тема ----------
  const applyTheme = () => { if (W && /^(dark|light)$/.test(W.colorScheme)) document.documentElement.dataset.theme = W.colorScheme; };
  applyTheme();
  try { if (W && W.onEvent) W.onEvent('themeChanged', applyTheme); } catch (e) { /* нет событий темы */ }

  // ---------- утилиты DOM ----------
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids.flat(Infinity)) if (c != null && c !== false) el.append(c.nodeType ? c : String(c));
    return el;
  }
  const SVG = {
    chev: '<path d="M9 6l6 6-6 6"/>',
    camera: '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3.2"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    alert: '<circle cx="12" cy="12" r="9.5"/><path d="M12 7.5v5.5M12 16.5v.01"/>',
    clock: '<circle cx="12" cy="12" r="9.5"/><path d="M12 7v5l3 2"/>',
    chat: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22z"/>',
    shield: '<path d="M20 13c0 5-3.5 7.5-7.7 9a1 1 0 0 1-.6 0C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.2-2.7a1.2 1.2 0 0 1 1.6 0C14.5 3.8 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    receipt: '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    hash: '<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18"/>',
    lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    wifi: '<path d="M12 20h.01M8.5 16.4a5 5 0 0 1 7 0M5 12.9a10 10 0 0 1 5.2-2.7M19 12.9a10 10 0 0 0-2.2-1.7M2 8.8a15 15 0 0 1 4.2-2.6M22 8.8A15 15 0 0 0 10.7 5M2 2l20 20"/>',
    drop: '<path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5S12.5 5.5 12 3c-.5 2.5-2 4.9-4 6.5S5 13 5 15a7 7 0 0 0 7 7z"/>',
    zap: '<path d="M13 2 4 14h8l-1 8 9-12h-8z"/>',
    flame: '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.4-.5-2-1-3-1.1-2.1-.2-4.1 2-6 .5 2.5 2 4.9 4 6.5s3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.2.4-2.3 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>',
    therm: '<path d="M14 4v10.5a4 4 0 1 1-4 0V4a2 2 0 0 1 4 0z"/><path d="M12 18v-6"/>',
    gauge: '<path d="m12 14 4-4"/><path d="M3.3 19a10 10 0 1 1 17.4 0z"/>',
    home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9v11a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    trash: '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    image: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
    users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.8 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    send: '<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4z"/>',
    userPlus: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M19 8v6M22 11h-6"/>',
    userCog: '<path d="M10 15H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><circle cx="18" cy="15" r="3"/><path d="m21.7 16.4-.9-.3M15.2 13.9l-.9-.3M16.6 18.7l.3-.9M19.1 12.2l.3-.9M19.6 18.7l-.4-1M16.8 12.3l-.4-1M14.3 16.6l1-.4M20.7 13.8l1-.4"/>',
    // связка ключей: кольцо сверху; левый ключ (круглая головка) отклонён влево, правый (прямоугольная) — чуть вправо
    keys: '<circle cx="13.4" cy="5" r="3.3" stroke-width="1.3"/><g transform="rotate(38 9.9 9.5)"><circle cx="9.9" cy="9.5" r="2.4"/><path d="M9.9 11.9v8.6M9.9 17.3H8.2M9.9 20h-1.9"/></g><g transform="rotate(18 15.2 10.2)"><rect x="13.1" y="8.1" width="4.2" height="4.2" rx="1.2"/><path d="M15.2 12.3v9M15.2 18h-1.7M15.2 20.8h-1.9"/></g>',  // связка: тонкое кольцо сквозь головки двух ключей
    copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    phone: '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/>',
  };
  function icon(name, s) {
    const span = h('span', { class: 'ic', 'aria-hidden': 'true' });
    span.innerHTML = '<svg width="' + (s || 22) + '" height="' + (s || 22) + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + SVG[name] + '</svg>';
    return span;
  }
  const ibox = (name, tone, size, cls) => h('span', { class: 'ib t-' + tone + (cls ? ' ' + cls : '') }, icon(name, size || 18));
  // Тип счётчика → иконка и цвет.
  const KINDS = [[/gas/, 'flame', 'gas'], [/heat/, 'therm', 'heat'], [/elec/, 'zap', 'elec'], [/hot/, 'drop', 'hot'], [/water|cold/, 'drop', 'cold'], [/$/, 'gauge', 'other']];
  const kind = m => KINDS.find(k => k[0].test(String((m && m.type) || ''))).slice(1);
  const meterIcon = (m, size, cls) => { const k = kind(m); return ibox(k[0], k[1], size || 22, 'mi duo' + (cls ? ' ' + cls : '')); };

  function btn(text, onclick, cls) {
    return h('button', { class: 'btn' + (cls ? ' ' + cls : ''), type: 'button', onclick }, text);
  }
  function setBusy(b, text) { b._orig = Array.from(b.childNodes); b.disabled = true; b.replaceChildren(h('span', { class: 'spin' }), text); }
  function unBusy(b) { if (b._orig) b.replaceChildren(...b._orig); b.disabled = false; }
  const NOTE_IC = { info: ['check', 'ok'], warn: ['alert', 'warn'], bad: ['alert', 'bad'] };
  function note(box, kind, text, retry) {
    box.replaceChildren();
    if ((box.hidden = !text)) return;
    const k = NOTE_IC[kind] || NOTE_IC.info;
    box.className = 'note t-' + k[1];
    box.append(icon(k[0], 18), h('span', { class: 'grow' }, text));
    if (retry) box.append(h('button', { class: 'link', type: 'button', onclick: retry }, 'Повторить'));
  }
  let toastTimer = 0;
  function toast(text) {
    let t = document.querySelector('.toast');
    if (!t) { t = h('div', { class: 'toast', role: 'status' }); document.body.append(t); }
    t.textContent = text; clearTimeout(toastTimer); toastTimer = setTimeout(() => t.remove(), 3000);
  }
  function haptic(type) {
    try { if (W && W.HapticFeedback) W.HapticFeedback.notificationOccurred(type); } catch (e) { /* нет вибрации */ }
  }
  function hapticTick() {
    try { if (W && W.HapticFeedback && W.HapticFeedback.selectionChanged) W.HapticFeedback.selectionChanged(); } catch (e) { /* нет вибрации */ }
  }
  function closeApp() {
    try { if (W && W.close) return W.close(); } catch (e) { /* ниже подсказка */ }
    toast('Закройте это окно, чтобы вернуться в чат');
  }
  // Действие в чате: диплинк max.ru/<бот>?start=<payload> — бот откроет нужный экран или сценарий.
  // Нет моста или имени бота — просто возвращаемся в чат.
  function openChat(payload) {
    const u = String((me && me.bot_username) || '').replace(/^@/, '');
    if (u && /^[\w-]+$/.test(payload) && W && typeof W.openMaxLink === 'function') {
      try { return W.openMaxLink('https://max.ru/' + encodeURIComponent(u) + '?start=' + payload); } catch (e) { /* ниже — закрываем */ }
    }
    closeApp();
  }

  // ---------- диалог (нижний лист) ----------
  let dlg = null, dlgDone = null;
  function closeDialog() { if (dlg) dlg.remove(); dlg = null; dlgDone = null; }
  function ask(o) {
    return new Promise(resolve => {
      closeDialog();
      const done = v => { closeDialog(); resolve(v); };
      dlgDone = done;
      dlg = h('div', { class: 'dlg', role: 'dialog', 'aria-modal': 'true', onclick: e => { if (e.target === dlg) done(false); } },
        h('div', { class: 'sheet' },
          ibox(o.icon || 'alert', o.tone || 'accent', 28, 'si'),
          h('h2', {}, o.title),
          o.text && h('p', {}, o.text),
          h('div', { class: 'acts' }, btn(o.ok, () => done(true), o.okCls), o.cancel && btn(o.cancel, () => done(false), 'ghost'))));
      document.body.append(dlg);
    });
  }

  // ---------- API ----------
  const CODE_MSG = {
    no_access: 'Нет доступа к этому адресу. Попросите собственника открыть доступ в боте.',
    too_large: 'Фото слишком большое. Снимите ещё раз или введите показание вручную.',
    not_image: 'Это не похоже на фото. Сфотографируйте счётчик ещё раз.',
    limit: 'Неоткрытых ссылок уже слишком много. Отмените ненужную и создайте новую.',
    not_owner: 'Делиться можно только своими адресами.',
    empty: 'Выберите хотя бы один адрес.',
    invite_used: 'Эту ссылку уже приняли — отменить её нельзя. Закройте доступ человеку в списке.',
    no_username: 'Сейчас не получилось создать ссылку. Попробуйте чуть позже.',
  };
  const STATUS_MSG = {
    401: 'Не получилось вас узнать. Закройте мини-приложение и откройте его снова из чата.',
    403: CODE_MSG.no_access,
    404: 'Не нашли — возможно, данные уже изменились. Вернитесь на главную.',
    500: 'Что-то пошло не так на нашей стороне. Ваши данные на месте — попробуйте ещё раз.',
  };
  class ApiError extends Error {
    constructor(status, code, message) { super(message); this.status = status; this.code = code; }
  }
  async function api(path, opt) {
    const o = opt || {};
    const headers = { 'X-Max-Init-Data': INIT };
    if (DEV_USER) headers['X-Dev-User'] = DEV_USER;
    let body = o.form;
    if (o.json) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(o.json); }
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), o.timeout || 15000);
    let res;
    let data = null;
    try {
      res = await fetch(API + path, { method: o.method || 'GET', headers, body, signal: ctl.signal });
      try { data = await res.json(); } catch (e) { data = null; }
    } catch (e) {
      throw new ApiError(0, 'network', 'Нет связи с сервером');
    } finally {
      clearTimeout(timer);
    }
    if (!res.ok) {
      const code = (data && data.code) || 'http_' + res.status;
      const fallback = CODE_MSG[code] || STATUS_MSG[res.status] ||
        (res.status >= 500 ? STATUS_MSG[500] : 'Не получилось выполнить запрос. Попробуйте ещё раз.');
      throw new ApiError(res.status, code, code === 'no_access' ? CODE_MSG.no_access : fixDays((data && data.message) || fallback));
    }
    if (!data) throw new ApiError(res.status, 'bad_response', 'Сервер ответил непонятно. Попробуйте ещё раз.');
    return data;
  }

  // ---------- форматирование ----------
  const KEYS = ['t1', 't2', 't3'];
  const TARIFFS = { 1: [''], 2: ['Т1 · день', 'Т2 · ночь'], 3: ['Т1 · пик', 'Т2 · ночь', 'Т3 · полупик'] };
  const MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
  const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  const MONTHS_SHORT = ['янв.', 'февр.', 'марта', 'апр.', 'мая', 'июня', 'июля', 'авг.', 'сент.', 'окт.', 'нояб.', 'дек.'];
  const SOURCES = { photo: 'по фото', photo_edited: 'по фото, исправлено', manual: 'вручную', miniapp: 'в приложении' };

  // Русская плюрализация: 1 день, 2 дня, 5 дней; остался/осталось.
  const plural = (n, f) => { const a = Math.abs(n) % 100, b = a % 10; return f[a > 10 && a < 20 ? 2 : b === 1 ? 0 : b >= 2 && b <= 4 ? 1 : 2]; };
  const DAYS = ['день', 'дня', 'дней'];
  const days = n => n + ' ' + plural(n, DAYS);
  const leftWord = n => plural(n, ['остался', 'осталось', 'осталось']);
  // Тексты сервера приходят как «осталось 1 дн.» — приводим к нормальной речи.
  function fixDays(s) {
    return String(s || '').replace(/(?:(осталось|остался)\s+)?(\d+) дн\./g, (m, w, n) => (w ? leftWord(+n) + ' ' : '') + days(+n));
  }

  const tariffCount = m => Math.min(3, Math.max(1, +m.tariffs || 1));
  const decimals = m => (m && m.type === 'electricity' ? 2 : 3);
  const toNum = v => (v == null || v === '' || isNaN(+String(v).replace(',', '.')) ? null : +String(v).replace(',', '.'));
  // Значения в ответах API — числа в единицах счётчика (123.456) или строки, как ввёл пользователь.
  function fmtNum(v, m) {
    if (v == null || v === '') return '—';
    if (typeof v === 'string') return v.trim().replace('.', ',');
    return Number(v).toFixed(decimals(m)).replace(/\.?0+$/, '').replace('.', ',');  // без несуществующих нулей
  }
  const parts = (v, m) => { const s = fmtNum(v, m), j = s.indexOf(','); return j < 0 ? [s, ''] : [s.slice(0, j), s.slice(j)]; };
  // Показание «приборным» шрифтом: целая часть + дробная другим цветом, как на барабане счётчика.
  function num(m, vals, col) {
    const v = vals || {};
    const n = tariffCount(m);
    return h('span', { class: 'num' + (col && n > 1 ? ' col' : '') },
      KEYS.slice(0, n).map((k, i) => {
        const p = parts(v[k], m);
        return h('span', { class: 'n' }, n > 1 && h('small', {}, 'Т' + (i + 1)), p[0], h('span', { class: 'f' }, p[1]),
          (col || i === n - 1) && m.unit && h('span', { class: 'u' }, m.unit));
      }));
  }
  function parseDate(s) {
    const r = /^(\d{4})-(\d{2})(?:-(\d{2}))?/.exec(s || '');
    return r ? { y: +r[1], mo: +r[2], d: r[3] ? +r[3] : 0 } : null;
  }
  const today = () => { const t = new Date(); return Date.UTC(t.getFullYear(), t.getMonth(), t.getDate()); };
  const daysUntil = s => { const p = parseDate(s); return p && p.d ? Math.round((Date.UTC(p.y, p.mo - 1, p.d) - today()) / 864e5) : null; };
  function fmtDate(s) {
    const p = parseDate(s);
    if (!p || !p.d) return s || '';
    return p.d + ' ' + MONTHS_GEN[p.mo - 1] + (p.y !== new Date().getFullYear() ? ' ' + p.y : '');
  }
  const shortDate = s => String(s || '').replace(/(\d+) ([а-яё]+)/, (m, d, mo) => d + ' ' + (MONTHS_SHORT[MONTHS_GEN.indexOf(mo)] || mo));
  function fmtPeriod(s) {
    const p = parseDate(s);
    if (!p) return s || '';
    const n = MONTHS[p.mo - 1];
    return n[0].toUpperCase() + n.slice(1) + ' ' + p.y;
  }
  const fmtDay = s => { const p = parseDate(s); return p && p.d ? String(p.d).padStart(2, '0') + '.' + String(p.mo).padStart(2, '0') : ''; };
  // Полный адрес — в тексте; короткий (address_label, label) — только на кнопках. В шапке полный, обрезается по ширине.
  const mAddr = m => m.address_full || m.address_label;
  const aFull = a => a.full_text || a.label;
  const meterTitle = m => [m.type_label, mAddr(m)].filter(Boolean).join(' · ');
  const oneAddress = ms => new Set(ms.map(m => m.address_label)).size === 1;
  const initials = n => String(n || '').trim().split(/\s+/).slice(0, 2).map(w => w[0] || '').join('').toUpperCase() || '?';
  // Аватар: инициалы, поверх — фото профиля MAX, когда загрузится. Фото нет или не загрузилось — остаются инициалы.
  const avaKids = u => {
    const txt = initials(u && u.full_name), url = u && u.photo_url;
    return url ? [txt, h('img', { src: url, alt: '', referrerpolicy: 'no-referrer', decoding: 'async',
      onload: e => e.target.classList.add('on'), onerror: e => e.target.remove() })] : [txt];
  };
  const fmtPhone = p => { const r = /^\+7(\d{3})(\d{3})(\d{2})(\d{2})$/.exec(p || ''); return r ? '+7 ' + r[1] + ' ' + r[2] + '-' + r[3] + '-' + r[4] : p || '—'; };
  const ADDR_WORDS = ['адрес', 'адреса', 'адресов'];
  const METER_WORDS = ['счётчик', 'счётчика', 'счётчиков'];

  // ---------- адреса ----------
  // Ключ адреса — id из /api/me (старый сервер без id — подпись). Выбор живёт до закрытия: при каждом входе — все адреса.
  const mKey = m => String(m.address_id != null ? m.address_id : m.address_label);
  const aKey = a => String(a.id != null ? a.id : a.label);
  let addrSel = '';
  function setAddr(k) { addrSel = k; }
  const addrList = d => (d && d.addresses) || [];
  const grantedAddrs = d => addrList(d).filter(a => a.access === 'granted');
  // Выбранный адрес: только если доступных адресов больше одного и он среди них; '' — все адреса.
  function curAddr(d) {
    const g = grantedAddrs(d);
    return g.length > 1 && g.some(a => aKey(a) === addrSel) ? addrSel : '';
  }
  const visMeters = d => { const k = curAddr(d), ms = (d && d.meters) || []; return k ? ms.filter(m => mKey(m) === k) : ms; };
  const normSerial = s => String(s || '').replace(/[\s-]/g, '').toLowerCase();

  // ---------- навигация ----------
  const stack = [];
  let renderId = 0, me = null;
  // meStale: доступ менялся (ссылка, закрыть, убрать) — экраны, которые рисуются из кэша me, перечитают /api/me.
  let meStale = false;
  const setMe = d => { me = d; meStale = false; };

  function go(name, params, replace) { if (replace) stack.pop(); stack.push({ name, params: params || {} }); render(); }
  function back() { if (dlgDone) return dlgDone(false); if (stack.length > 1) { stack.pop(); render(); } }
  function toHome() { stack.length = 0; go('home'); }
  function syncBack() {
    const can = stack.length > 1;
    if (!nativeBack) return $back.classList.toggle('on', can);
    try { if (can) W.BackButton.show(); else W.BackButton.hide(); } catch (e) { /* ignore */ }
  }
  if (nativeBack) { try { W.BackButton.onClick(back); } catch (e) { /* ignore */ } }
  $back.addEventListener('click', back);

  // Начали вводить показание — MAX переспросит, прежде чем закрыть окно (enableClosingConfirmation).
  let guarded = false;
  function guardClose(on) {
    if (on === guarded) return;
    guarded = on;
    try { if (W) W[on ? 'enableClosingConfirmation' : 'disableClosingConfirmation'](); } catch (e) { /* старый клиент */ }
  }
  $view.addEventListener('input', () => {
    const cur = stack[stack.length - 1];
    guardClose(!!cur && cur.name === 'submit' && Array.from($view.querySelectorAll('input')).some(i => i.type !== 'file' && i.value.trim()));
  });

  function render() {
    renderId++; closeDialog(); guardClose(false); syncBack(); window.scrollTo(0, 0);
    const t = document.querySelector('.toast'); if (t) t.remove();  // итог действия — только на своём экране
    const cur = stack[stack.length - 1];
    SCREENS[cur.name](cur.params);
  }
  function setScreen(title, ...nodes) {
    $title.textContent = title;
    $eb.hidden = true;
    $ava.hidden = true;
    setBar();
    $view.replaceChildren(...nodes.flat(Infinity).filter(Boolean));
  }
  function setBar(...buttons) {
    const b = buttons.filter(Boolean);
    $bar.replaceChildren(...b);
    document.body.classList.toggle('has-bar', !($bar.hidden = !b.length));
  }
  // Скелетон повторяет форму экрана: главная (карточка срока, плитки, список) или списки доступа.
  function skeleton(kind) {
    const sk = cls => h('div', { class: 'sk ' + cls });
    return h('div', { class: 'stack', 'aria-busy': 'true', 'aria-label': 'Загружаем' }, kind === 'list'
      ? [sk('s4'), sk('s5'), sk('s4'), sk('s2')]
      : [sk('s1'), h('div', { class: 'tiles' }, sk('s2'), sk('s2')), sk('s3')]);
  }
  function stateCard(ic, tone, title, text, ...buttons) {
    return h('div', { class: 'state' }, ibox(ic, tone, 30, 'si'), h('h2', {}, title), text && h('p', {}, text),
      buttons.length > 0 && h('div', { class: 'acts' }, buttons));
  }
  function errorCard(err, retry) {
    const r = retry && btn('Повторить', retry);
    if (err.status === 0) return stateCard('wifi', 'bad', 'Нет связи с сервером', 'Проверьте интернет и повторите.', r);
    if (err.status === 403) return stateCard('lock', 'warn', 'Нет доступа', 'Попросите собственника открыть доступ в боте.', r);
    return stateCard('alert', 'bad', err.status === 404 ? 'Не нашли' : 'Что-то пошло не так', err.message, r);
  }
  async function load(title, fetcher, draw, sk) {
    const my = renderId;
    setScreen(title, skeleton(sk));
    try {
      const d = await fetcher();
      if (my === renderId) draw(d);
    } catch (e) {
      if (my !== renderId) return;
      haptic('error');
      setScreen(title, errorCard(e, () => load(title, fetcher, draw, sk)));
    }
  }

  // ---------- экран: вне MAX ----------
  function screenOutside() {
    setScreen('ЖКХ', stateCard('chat', 'accent', 'Откройте мини-приложение из чата с ботом в MAX',
      'Так мы узнаем вас и покажем ваши счётчики.'));
  }

  // ---------- экран: главная ----------
  // Главная рисуется из структурированных полей dashboard (/api/me): window, bill, verification, pending,
  // submitted/total. Строки dashboard.lines — текст для бота; здесь они только запасной вариант,
  // если сервер старый и структуры нет.
  function openUrgent(u) {
    if (u.kind === 'submit') return go('submit');
    if (u.kind === 'verification') {
      return ask({ icon: 'shield', tone: 'warn', title: 'Запись на поверку появится скоро', ok: 'Понятно',
        text: 'Пока позвоните в УК или аккредитованную организацию. Дату после поверки внесите в «Мои счётчики» в чате.' });
    }
    return ask({ icon: 'receipt', title: 'Оплата появится скоро', text: 'Сейчас оплатить можно по квитанции в банке. Счёт здесь — демо.', ok: 'Понятно' });
  }
  function hero(meters, dash) {
    const total = meters.length;
    const done = meters.filter(m => m.submitted_this_period).length;
    const all = done === total;
    const w = dash.window || null;
    const left = w && w.open && w.days_left != null ? +w.days_left : null;
    let eb = 'Показания';
    let big = [h('b', {}, done), h('span', {}, 'из ' + total)];
    if (left != null) {
      eb = 'Подача до ' + fmtDate(w.to);
      big = left <= 0 ? [h('b', { class: 'w' }, 'Сегодня'), h('i', {}, 'последний день')]
        : [h('b', {}, left), h('span', {}, plural(left, DAYS)), h('i', {}, leftWord(left))];
    } else if (w && w.next_from) {
      eb = 'Следующая подача';
      big = [h('b', { class: 'w' }, 'с ' + shortDate(fmtDate(w.next_from)))];
    }
    const hot = !all && left != null && left <= 3;
    return h('section', { class: 'hero' + (hot ? ' hot' : '') },
      h('div', { class: 'eb' }, h('span', {}, eb), hot && h('span', { class: 'tag' }, 'срочно')),
      h('div', { class: 'big' }, big),
      h('div', { class: 'prog' },
        h('div', { class: 'segs' }, meters.slice(0, 12).map(m => h('i', { class: m.submitted_this_period ? 'on' : '' }))),
        h('span', {}, all ? [icon('check', 16), 'всё подано'] : 'подано ' + done + ' из ' + total)),
      btn([icon('camera', 20), 'Подать показания'], () => go('submit'), 'white'));
  }
  function tile(ic, tone, label, value, sub, onclick) {
    return h('button', { class: 'tile t-' + tone, type: 'button', onclick },
      h('span', { class: 'th' }, ibox(ic, tone, 16), label, h('span', { class: 'chev' }, icon('chev', 16))),
      h('b', { class: 'v' }, value), sub && h('small', {}, sub));
  }
  // Выбран адрес → поверка и счёт только этого адреса (счета — из dashboard.bills, поверки — из счётчиков).
  function tiles(meters, dash, key) {
    const out = [], u = dash.urgent;
    let v = dash.verification, b = dash.bill;
    if (key) {
      if (v && !meters.some(m => m.id === v.meter_id)) v = null;
      if (!v) {
        const near = meters.map(m => ({ m, n: daysUntil(m.verification_due) })).filter(x => x.n != null && x.n <= 60).sort((x, y) => x.n - y.n)[0];
        v = near && { meter_id: near.m.id, due: near.m.verification_due, days_left: near.n };
      }
      b = (dash.bills || []).filter(x => String(x.address_id) === key)[0] || null;
    }
    if (v) {
      const n = +v.days_left, m = meters.find(x => x.id === v.meter_id);
      out.push(tile('shield', n < 0 ? 'bad' : n <= 30 ? 'warn' : 'ok', 'Поверка', n < 0 ? 'просрочена' : days(n),
        (m ? m.type_label : v.meter_label) + ' · ' + shortDate(fmtDate(v.due)), () => openUrgent({ kind: 'verification' })));
    }
    if (b) {
      const late = +b.days_left < 0;
      out.push(tile('receipt', late ? 'bad' : u && u.kind === 'bill' && dash.bill && b.id === dash.bill.id ? 'warn' : 'accent', 'Счёт', b.amount_text,
        (late ? 'срок был ' : 'до ') + shortDate(fmtDate(b.due)) + (b.demo ? ' · демо' : ''), () => openUrgent({ kind: 'bill' })));
    }
    return out.length > 0 && h('div', { class: 'tiles' }, out);
  }
  // Старый сервер без структуры: показываем его строки как есть (кроме сноски, срочного и доступа — они есть отдельно).
  function legacyLines(dash) {
    if (dash.window !== undefined) return [];
    return (dash.lines || []).map(l => String(l || '').trim())
      .filter(l => l && !/мини-приложени|^Доступ /i.test(l) && !(dash.urgent && l === dash.urgent.text)).map(fixDays);
  }
  function meterItem(m, showAddr) {
    return h('div', { class: 'item' },
      h('button', { class: 'item-main', type: 'button', onclick: () => go('meter', { id: m.id }) },
        meterIcon(m),
        h('span', { class: 'grow' }, h('b', {}, m.type_label),
          m.last ? num(m, m.last.values) : h('small', {}, 'показаний пока нет'),
          showAddr && h('small', {}, mAddr(m)))),
      m.submitted_this_period
        ? h('span', { class: 'st' }, icon('check', 15), 'подано')
        : h('button', { class: 'mini', type: 'button', onclick: () => go('submit', { meterId: m.id }) }, 'Подать'));
  }
  const addMeterItem = () => h('div', { class: 'item' },
    h('button', { class: 'item-main add', type: 'button', onclick: () => openChat('add_meter') },
      ibox('plus', 'accent', 20, 'mi'), h('span', { class: 'grow' }, h('b', {}, 'Добавить счётчик'), h('small', {}, 'в чате с ботом'))));
  const section = (title, count) => h('div', { class: 'sech' }, h('span', {}, title), count != null && h('small', {}, count));
  const dayNum = s => { const p = parseDate(s); return p && p.d; };
  function footnote(dash) {
    const w = dash.window;
    const win = w ? 'подача с ' + dayNum(w.from) + ' по ' + dayNum(w.to) + ' число' : 'подача с 15 по 25 число';
    return 'Демо: ' + win + (!w || (dash.bill && dash.bill.demo) ? ', счёт смоделирован' : '');
  }
  function screenHome() {
    load('ЖКХ', () => api('/api/me'), d => { setMe(d); drawHome(d); });
  }
  const emptyMeters = () => stateCard('camera', 'accent', 'Счётчиков пока нет',
    'Добавьте счётчик в чате или просто пришлите туда его фото.', btn([icon('plus', 20), 'Добавить счётчик'], () => openChat('add_meter')));
  function drawHome(d) {
    if (!d.registered) {
      return setScreen('ЖКХ', stateCard('chat', 'accent', 'Продолжите регистрацию в чате с ботом',
        'Это займёт минуту: имя, телефон и адрес.', btn('Вернуться в чат', closeApp)));
    }
    const all = d.meters || [], dash = d.dashboard || {}, key = curAddr(d), meters = visMeters(d);
    let pending = key ? [] : dash.pending || addrList(d).filter(a => a.access === 'pending');
    const legacy = legacyLines(dash);
    const waitOnly = !grantedAddrs(d).length && pending.length > 0;  // жилец ждёт доступа — добавлять счётчик рано
    const top = meters.length ? hero(meters, dash) : waitOnly
      ? stateCard('clock', 'warn', 'Ждём одобрения собственника', 'Когда собственник откроет доступ к адресу ' + aFull(pending[0]) + ', здесь появятся счётчики.',
        btn('Напомнить в чате', () => openChat('profile')))
      : emptyMeters();
    if (waitOnly) pending = pending.slice(1);
    // Все адреса сразу — счётчики группами по адресу, без повтора адреса в каждой строке.
    const groups = [];
    if (!key && !oneAddress(all)) meters.forEach(m => { const g = groups.find(x => x.k === mKey(m)); if (g) g.ms.push(m); else groups.push({ k: mKey(m), label: mAddr(m), ms: [m] }); });
    else groups.push({ ms: meters });
    setScreen('ЖКХ',
      top,
      tiles(meters, dash, key),
      pending.map(a => h('div', { class: 'row' }, ibox('clock', 'warn', 18),
        h('span', { class: 'grow' }, h('b', {}, aFull(a)), h('small', {}, 'Доступ ждёт подтверждения собственника')))),
      legacy.length > 0 && h('div', { class: 'row' }, ibox('alert', 'accent', 18),
        h('span', { class: 'grow' }, legacy.map(l => h('small', {}, l)))),
      meters.length > 0 && [section('Счётчики', meters.length),
        groups.map((g, i) => [g.label && h('p', { class: 'grp' }, g.label),
          h('div', { class: 'list' }, g.ms.map(m => meterItem(m)), i === groups.length - 1 && addMeterItem())]),
        h('p', { class: 'foot' }, footnote(dash))]);
    homeHeader(d, key);
  }
  // Шапка главной: над заголовком «ЖКХ», заголовок — адрес. Адресов больше одного — заголовок открывает выбор.
  function homeHeader(d, key) {
    const list = addrList(d), g = grantedAddrs(d);
    $ava.hidden = false;
    $ava.replaceChildren(...avaKids(d.user));
    if (!list.length) return;
    const cur = g.find(a => aKey(a) === key);
    $eb.hidden = false;
    $eb.textContent = list.length > 1 ? 'ЖКХ · ' + list.length + ' ' + plural(list.length, ADDR_WORDS) : 'ЖКХ';
    const label = cur ? aFull(cur) : g.length > 1 ? 'Все адреса' : aFull(g[0] || list[0]);
    if (list.length < 2) return void ($title.textContent = aFull(list[0]));  // один адрес — заголовок, не кнопка
    $title.replaceChildren(h('button', { class: 'addr', type: 'button', onclick: () => go('addr') },
      h('span', {}, label), icon('down', 22)));
  }
  // Выбор адреса и доступ — отдельные экраны, а не шторки: так советует гайдлайн MAX
  // (шторка спорит со свайпом, который закрывает всё мини-приложение), и работает системная кнопка «Назад».
  function optScreen(title, rows) { setScreen(title, h('div', { class: 'card opts choose' }, rows)); }
  function screenAddr() {
    if (!me) return toHome();
    if (meStale) return load('Адрес', () => api('/api/me'), d => { setMe(d); drawAddr(); }, 'list');
    drawAddr();
  }
  function drawAddr() {
    const d = me, g = grantedAddrs(d), key = curAddr(d), ms = d.meters || [];
    const stat = list => {
      if (!list.length) return 'счётчиков пока нет';
      const left = list.filter(m => !m.submitted_this_period).length;
      return list.length + ' ' + plural(list.length, METER_WORDS) + (left ? ', не подано ' + left : ', всё подано');
    };
    const pick = k => () => { setAddr(k); toHome(); };
    const opt = (ic, tone, title, sub, on, onclick) => h('button', { class: 'opt' + (on ? ' on' : ''), type: 'button', 'aria-pressed': String(!!on), onclick },
      ibox(ic, tone, 18), h('span', { class: 'grow' }, h('b', {}, title), sub && h('small', {}, sub)), on && icon('check', 20));
    // Строка + правая колонка 44px: галочка у «Все адреса», у адресов — кнопка доступа (рядом, не внутри строки выбора).
    const wrap = (o, on, trail) => h('div', { class: 'optw' + (on ? ' on' : '') }, o, trail || h('span', { class: 'slot', 'aria-hidden': 'true' }));
    optScreen('Адрес', [
      g.length > 1 && wrap(opt('home', 'accent', 'Все адреса', stat(ms), false, pick('')), !key,
        !key && h('span', { class: 'slot', 'aria-hidden': 'true' }, icon('check', 20))),
      g.map(a => {
        const on = g.length < 2 || aKey(a) === key;
        return wrap(opt('home', 'accent', aFull(a), stat(ms.filter(m => mKey(m) === aKey(a))), on && g.length > 1, g.length > 1 ? pick(aKey(a)) : back),
          on && g.length > 1, shareBtn(d, a) || keyBtn(a));
      }),
      addrList(d).filter(a => a.access !== 'granted').map(a => opt('clock', a.access === 'pending' ? 'warn' : 'bad', aFull(a),
        a.access === 'pending' ? 'ждёт одобрения собственника' : 'собственник не открыл доступ', false, () => go('profile', {}, true))),
      h('button', { class: 'opt add', type: 'button', onclick: () => openChat('add_address') },
        ibox('plus', 'accent', 18), h('span', { class: 'grow' }, h('b', {}, 'Добавить адрес'), h('small', {}, 'в чате с ботом'))),
    ]);
  }

  // ---------- экран: профиль ----------
  // Свой адрес — без метки; общий — «доступ от Анны И.»; ждёт или закрыт — плашка статуса.
  const ROLE = { pending: ['ждёт одобрения', 'warn'], denied: ['нет доступа', 'bad'] };
  function screenProfile() {
    load('Профиль', () => api('/api/me'), d => { setMe(d); drawProfile(d); });
  }
  function drawProfile(d) {
    if (!d.registered) return drawHome(d);
    const u = d.user || {}, list = addrList(d);
    const waiting = list.some(a => a.access !== 'granted');
    const action = (ic, title, payload) => h('div', { class: 'item' },
      h('button', { class: 'item-main', type: 'button', onclick: () => openChat(payload) },
        ibox(ic, 'accent', 18), h('span', { class: 'grow' }, h('b', {}, title)), h('span', { class: 'chev' }, icon('chev', 18))));
    setScreen('Профиль',
      h('div', { class: 'card who-card' }, h('span', { class: 'ava big', 'aria-hidden': 'true' }, avaKids(u)),
        h('span', { class: 'grow' }, h('h2', {}, u.full_name || '—'),
          h('span', { class: 'ph-line' }, h('span', { class: 'tel' }, fmtPhone(u.phone)),
            u.phone_verified ? h('span', { class: 'pill t-ok' }, icon('check', 13), 'из MAX') : h('small', {}, 'указан вручную')))),
      section('Адреса', list.length || null),
      h('div', { class: 'list' }, list.map(a => {
        const r = a.access !== 'granted' && (ROLE[a.access] || ROLE.pending);
        return h('div', { class: 'arow' }, ibox('home', r ? r[1] : 'accent', 18),
          h('span', { class: 'grow' }, h('b', {}, aFull(a)),
            a.verified === false && isOwned(a) && h('small', {}, 'не сверен с ФИАС')),
          r && h('span', { class: 'pill t-' + r[1] }, r[0]), shareBtn(d, a) || keyBtn(a));
      })),
      waiting && h('div', { class: 'note t-warn' }, icon('clock', 18),
        h('span', { class: 'grow' }, 'Пока собственник не откроет доступ, показания по этому адресу не передать.'),
        h('button', { class: 'link', type: 'button', onclick: () => openChat('profile') }, 'Запросить')),
      list.some(isOwned) && shareCard(d),
      h('div', { class: 'list acts-list' }, action('plus', 'Добавить адрес', 'add_address'), action('phone', 'Изменить телефон', 'phone')),
      h('p', { class: 'foot' }, 'Изменения вносим в чате с ботом: там проверим адрес и номер. Права по адресам в демо смоделированы.'),
      h('button', { class: 'btn ghost danger', type: 'button', onclick: () => openChat('delete_data') }, 'Удалить мои данные'));
  }

  // ---------- общий доступ ----------
  // Делятся адресом: собственник открывает доступ семье или арендатору одноразовой ссылкой.
  // Контракт: GET /api/shares {owned, invites, received, limits}; POST /api/shares/invites; DELETE — см. change().
  const PEOPLE_INS = ['человеком', 'людьми', 'людьми'];
  const ADDR_DAT = ['адресу', 'адресам', 'адресам'];
  const isOwned = a => a.access === 'granted' && a.role === 'owner';
  const isShared = a => a.access === 'granted' && !!a.role && a.role !== 'owner';
  // «доступ от Анны И.»: API отдаёт имя в именительном («Анна И.»), склоняем первое слово (или берём owner_gen).
  const GEN_EXC = { 'Павел': 'Павла', 'Пётр': 'Петра', 'Лев': 'Льва', 'Любовь': 'Любови' };
  const genName = n => String(n || '').replace(/^[А-ЯЁ][а-яё]+/, w => GEN_EXC[w] ||
    (/[гкхжшчщ]а$/.test(w) ? w.slice(0, -1) + 'и' : /а$/.test(w) ? w.slice(0, -1) + 'ы' : /я$/.test(w) ? w.slice(0, -1) + 'и'
      : /[йь]$/.test(w) ? w.slice(0, -1) + 'я' : /[бвгджзклмнпрстфхцчшщ]$/.test(w) ? w + 'а' : w));
  const fromLine = a => (a.owner_gen || a.owner ? 'доступ от ' + (a.owner_gen || genName(a.owner)) : 'общий доступ');
  const sharedCount = a => (a.shared_count != null ? +a.shared_count : (a.members || []).filter(m => m.access === 'granted').length);
  const sid = a => String(a.address_id != null ? a.address_id : a.id);
  // Полный адрес по id (в приглашении сервер отдаёт только короткую подпись для кнопок).
  const fullById = id => { const a = addrList(me).find(x => String(x.id) === String(id)); return a ? aFull(a) : ''; };
  const fulls = as => (as || []).map(a => a.full_text || fullById(a.address_id) || a.label);
  const labels = as => fulls(as).join('; ');
  // Подзаголовок плашки — одна строка о том, что сейчас с доступом.
  function shareSub(d) {
    const open = addrList(d).filter(a => isOwned(a) && sharedCount(a) > 0);
    if (open.length === 1) { const n = sharedCount(open[0]); return 'Вы делитесь с ' + n + ' ' + plural(n, PEOPLE_INS); }
    if (open.length > 1) return 'Открыт доступ к ' + open.length + ' ' + plural(open.length, ADDR_DAT);
    return 'с семьёй или арендаторами';
  }
  // Быстрая кнопка у своего адреса: «человек+» — сразу ссылка на этот адрес; «человек-шестерёнка» — к управлению его доступом.
  const PEOPLE_WORDS = ['человек', 'человека', 'человек'];
  const ownedList = d => addrList(d).filter(isOwned).map(a => ({ address_id: a.id, label: a.label, full_text: a.full_text, members: [] }));
  function shareBtn(d, a) {
    if (!isOwned(a)) return null;
    const n = sharedCount(a), busy = n > 0 || +a.invites_count > 0;
    const label = busy ? 'Доступ к ' + a.label + ': ' + (n ? n + ' ' + plural(n, PEOPLE_WORDS) : 'есть неоткрытая ссылка') : 'Поделиться доступом к ' + a.label;
    return h('button', { class: 'ibtn', type: 'button', 'aria-label': label, title: label, onclick: e => {
      e.stopPropagation(); hapticTick();
      if (busy) go('share', { focus: aKey(a) }); else go('share_new', { addrs: ownedList(d), sel: [aKey(a)] });
    } }, icon(busy ? 'userCog' : 'userPlus', 21));
  }
  // У общего адреса — ключи: страница «кто открыл доступ» с одной кнопкой «Выйти».
  // Закрытый (denied) и ждущий (pending) общий адрес — тоже с ключами: убрать его у себя можно только там.
  const isGuest = a => !!a.role && a.role !== 'owner';
  function keyBtn(a) {
    if (!isGuest(a)) return null;
    const label = 'Общий адрес ' + a.label + (a.owner ? ': доступ ' + opened(a) + ' ' + a.owner : '');
    return h('button', { class: 'ibtn', type: 'button', 'aria-label': label, title: label, onclick: e => {
      e.stopPropagation(); hapticTick(); go('shared', { id: aKey(a) });
    } }, icon('keys', 22));
  }
  const shareCard = d => h('div', { class: 'list' }, h('div', { class: 'item' },
    h('button', { class: 'item-main', type: 'button', onclick: () => go('share') },
      ibox('users', 'accent', 22, 'mi'), h('span', { class: 'grow' },
        h('b', {}, 'Поделиться доступом'), h('small', {}, shareSub(d))),
      h('span', { class: 'chev' }, icon('chev', 18)))));

  function screenShare(p) {
    load('Общий доступ', () => Promise.all([api('/api/shares'), api('/api/me')]).then(r => { setMe(r[1]); return r[0]; }), s => drawShare(s, p.focus), 'list');
  }
  function drawShare(s, focus) {
    const owned = s.owned || [], invites = s.invites || [], received = s.received || [];
    // Свои адреса: из /api/shares, а если сервер отдал только адреса с людьми — добираем из /api/me.
    const mine = owned.slice();
    addrList(me).filter(isOwned).forEach(a => { if (!mine.some(o => sid(o) === sid(a))) mine.push({ address_id: a.id, label: a.label, full_text: a.full_text, members: [] }); });
    const withPeople = owned.filter(o => (o.members || []).length);
    const people = new Set(withPeople.flatMap(o => o.members.map(m => (m.member_id != null ? m.member_id : m.name)))).size;
    const limit = s.limits && s.limits.active_invites;
    const winOpen = !!(me && me.dashboard && me.dashboard.window && me.dashboard.window.open);
    const member = (o, m) => {
      const ok = !!m.submitted_this_period;
      return h('div', { class: 'arow' }, h('span', { class: 'ava sm', 'aria-hidden': 'true' }, initials(m.name)),
        h('span', { class: 'grow' }, h('b', {}, m.name),
          h('small', { class: 'sl t-' + (ok ? 'ok' : winOpen ? 'warn' : 'other') }, h('i', { class: 'sd' }),
            ok ? 'подано ' + fmtDay(m.last_submitted_at) : 'показаний за месяц нет')),
        h('button', { class: 'mini q', type: 'button', 'aria-label': 'Закрыть доступ: ' + m.name, onclick: () => closeMember(o, m) }, 'Закрыть'));
    };
    const invCard = inv => h('div', { class: 'card inv', 'data-addrs': (inv.addresses || []).map(sid).join(',') },
      h('div', { class: 'ih' }, ibox('link', 'accent', 18),
        h('span', { class: 'grow' }, fulls(inv.addresses).map(t => h('b', {}, t)),
          h('small', {}, 'Ссылка ещё не открыта · до ' + fmtDate(inv.expires_at)))),
      h('div', { class: 'ia' },
        h('button', { class: 'mini', type: 'button', onclick: () => sendLink(inv) }, icon('send', 16), 'Отправить ссылку'),
        h('button', { class: 'link q', type: 'button', onclick: () => cancelInvite(inv) }, 'Отменить')));
    const got = received.length > 0 && [section('Вам открыли доступ', received.length),
      h('div', { class: 'list' }, received.map(a => h('div', { class: 'arow' }, ibox('home', 'accent', 18),
        h('span', { class: 'grow' }, h('b', {}, aFull(a)), h('small', {}, fromLine(a) + (a.since ? ' · с\u00a0' + shortDate(fmtDate(a.since)).replace(' ', '\u00a0') : ''))),
        h('button', { class: 'mini q', type: 'button', 'aria-label': 'Выйти из общего доступа: ' + a.label, onclick: () => dropReceived(a) }, 'Выйти'))))];
    let top;
    if (!mine.length) {
      // Делиться нечем: только общие адреса или адресов нет вовсе.
      top = received.length
        ? h('div', { class: 'row' }, ibox('home', 'accent', 18), h('span', { class: 'grow' },
          h('small', {}, 'Делиться можно своими адресами. Сейчас у вас только общие.')))
        : stateCard('home', 'accent', 'Сначала добавьте свой адрес', 'Делиться можно своими адресами: семья или арендатор будут передавать по ним показания.',
          btn([icon('plus', 20), 'Добавить адрес'], () => openChat('add_address')));
    } else if (!withPeople.length && !invites.length) {
      top = stateCard('users', 'accent', 'Доступ к квартире', 'Откройте доступ семье или арендатору — они передадут показания, а вы увидите, что всё подано.',
        btn([icon('plus', 20), 'Поделиться доступом'], () => go('share_new', { addrs: mine })));
    } else {
      top = [section('Вы делитесь', people || null),
        withPeople.length
          ? withPeople.map(o => [h('p', { class: 'grp', 'data-addr': sid(o) }, aFull(o)), h('div', { class: 'list' }, o.members.map(m => member(o, m)))])
          : h('p', { class: 'lead' }, 'Пока ни с кем. Человек появится здесь, когда откроет ссылку.'),
        invites.length > 0 && [section('Неоткрытые ссылки', invites.length + (limit ? ' из ' + limit : '')), invites.map(invCard)]];
    }
    setScreen('Общий доступ', top, !mine.length && got);  // общие адреса — у ключей в профиле и листе адресов
    // Пришли с кнопки у адреса — показываем людей этого адреса (или его приглашение) и коротко подсвечиваем.
    const g = focus && ($view.querySelector('.grp[data-addr="' + focus + '"]') ||
      Array.from($view.querySelectorAll('.inv')).find(x => x.dataset.addrs.split(',').includes(focus)));
    if (g) {
      const t = g.classList.contains('grp') ? g.nextElementSibling : g;
      g.scrollIntoView({ block: 'center' });
      t.classList.add('hl');
    }
    if (mine.length && (withPeople.length || invites.length)) {
      setBar(btn([icon('plus', 20), 'Поделиться доступом'], () => {
        if (limit && invites.length >= limit) {
          haptic('error');
          return ask({ icon: 'link', tone: 'warn', title: 'Уже ' + invites.length + ' ' + plural(invites.length, ['неоткрытая ссылка', 'неоткрытые ссылки', 'неоткрытых ссылок']), ok: 'Понятно',
            text: 'Их ещё никто не открыл. Отмените ненужную, чтобы создать новую.' });
        }
        go('share_new', { addrs: mine });
      }));
    }
  }

  // Общий адрес у получателя: кто и когда открыл доступ; «Выйти» — с подтверждением.
  // Род: owner_female с сервера (по отчеству), иначе по имени: «Анна» → открыла, «Никита» → открыл; «Саша» — «открыл(а)».
  const MALE_A = /^(Никита|Илья|Фома|Кузьма|Лука|Савва|Данила|Гаврила|Святослава?|Миша|Гоша|Паша|Дима|Лёша|Алёша|Сеня|Ваня|Вова|Петя|Коля|Толя|Юра|Слава)$/;
  const BOTH = /^(Саша|Женя|Валя|Шура|Слава)$/;
  const nb = s => String(s || '').replace(/ /g, '\u00a0');  // «Анна И.» не разрывать переносом
  function opened(a) {
    if (a.owner_female === true) return 'открыла';
    if (a.owner_female === false) return 'открыл';
    const w = String(a.owner || '').split(/\s+/)[0];
    return BOTH.test(w) ? 'открыл(а)' : /[ая]$/.test(w) && !MALE_A.test(w) ? 'открыла' : w ? 'открыл' : 'открыл(а)';
  }
  function screenShared(p) {
    const a = addrList(me).find(x => aKey(x) === p.id && isGuest(x));
    if (!a) return toHome();
    const addr = h('b', {}, aFull(a));
    const line = a.access === 'granted' ? ['Доступ к адресу ', addr, ' вам ' + opened(a) + ' ' + nb(a.owner || 'собственник').replace(/\.?$/, '.')]
      : a.access === 'pending' ? ['Ждём, когда собственник откроет вам доступ к адресу ', addr, '.']
        : ['Доступ к адресу ', addr, ' закрыт. Показания, которые вы передали, сохранены.'];
    setScreen('Общий адрес',
      h('div', { class: 'state' }, ibox('keys', a.access === 'granted' ? 'accent' : 'warn', 34, 'si'),
        h('p', { class: 'who-open' }, line),
        a.access === 'granted' && a.since && h('p', { class: 'foot' }, 'С ' + fmtDate(a.since)),
        h('div', { class: 'acts' }, btn('Выйти', () => dropReceived(Object.assign({ address_id: a.id }, a), true), 'del'))),
      a.access === 'granted' && h('p', { class: 'foot' }, 'Передавать показания по этому адресу можно, пока доступ открыт. Менять адрес может только тот, кто открыл доступ.'));
  }

  // Выбор адресов для приглашения: карточки-чекбоксы, «Все адреса», внизу — «Создать ссылку».
  function screenShareNew(p) {
    const list = p.addrs || [];
    if (!list.length) return back();
    const sel = new Set(p.sel || (list.length === 1 ? [sid(list[0])] : []));
    const errBox = h('div', { hidden: true });
    const make = btn([icon('link', 20), 'Создать ссылку'], create);
    const box = h('div', { class: 'card opts choose' });
    const row = (title, sub, on, onclick, all) => h('button', { class: 'opt' + (on ? ' on' : '') + (all ? ' all' : ''), type: 'button', role: 'checkbox', 'aria-checked': String(on), onclick },
      h('span', { class: 'cb' }, icon('check', 16)), h('span', { class: 'grow' }, h('b', {}, title), sub && h('small', {}, sub)));
    function paint() {
      p.sel = Array.from(sel);
      box.replaceChildren(...[
        list.length > 1 && row('Все адреса', list.length + ' ' + plural(list.length, ADDR_WORDS), sel.size === list.length, () => {
          if (sel.size === list.length) sel.clear(); else list.forEach(a => sel.add(sid(a)));
          hapticTick(); note(errBox); paint();
        }, true),
        list.length > 1 && h('div', { class: 'sep' }),
        ...list.map(a => {
          return row(a.full_text || a.label, null, sel.has(sid(a)), () => { if (!sel.delete(sid(a))) sel.add(sid(a)); hapticTick(); note(errBox); paint(); });
        })].filter(Boolean));
      make.disabled = !sel.size;
    }
    async function create() {
      if (!sel.size) return;
      note(errBox);
      setBusy(make, 'Создаём ссылку…');
      try {
        const chosen = list.filter(a => sel.has(sid(a)));
        const r = await api('/api/shares/invites', { method: 'POST', json: { address_ids: chosen.map(a => a.address_id) } });
        haptic('success');
        meStale = true;
        go('share_link', { inv: Object.assign({ addresses: chosen }, r) }, true);
      } catch (e) {
        haptic('error');
        unBusy(make);
        note(errBox, e.status === 409 ? 'warn' : 'bad', e.status === 0 ? 'Нет связи с сервером. Ссылка не создана.' : e.message,
          e.status === 0 || e.status >= 500 ? create : null);
      }
    }
    paint();
    setScreen('Выберите адреса', h('p', { class: 'lead' }, 'Человек сможет передавать показания по этим адресам. Менять адрес сможете только вы.'), box, errBox);
    setBar(make);
  }

  // Ссылка готова: отправить в чат MAX (shareMaxContent) или скопировать.
  const canShareMax = () => !!(W && typeof W.shareMaxContent === 'function');
  function screenShareLink(p) {
    const inv = p.inv;
    if (!inv || !inv.url) return back();
    const box = h('button', { class: 'lnk', type: 'button', 'aria-label': 'Скопировать ссылку', onclick: () => copyLink(inv.url, box) }, inv.url.replace(/^https:\/\//, ''));
    setScreen('Новая ссылка',
      h('div', { class: 'state' }, ibox('link', 'ok', 30, 'si'), h('h2', {}, 'Ссылка готова'),
        h('p', {}, 'Отправьте её тому, с кем делитесь. Сработает один раз, действует до ' + fmtDate(inv.expires_at) + '.'),
        box, h('p', { class: 'foot' }, 'Доступ к: ' + labels(inv.addresses)),
        h('div', { class: 'acts' },
          canShareMax() && btn([icon('send', 20), 'Отправить в MAX'], () => sendLink(inv)),
          btn([icon('copy', 20), 'Скопировать ссылку'], () => copyLink(inv.url, box), canShareMax() ? 'sec' : ''),
          btn('Готово', back, 'ghost'))));
  }
  function shareText(inv) {
    const t = inv.share_text || 'Открываю вам доступ к адресу ' + labels(inv.addresses) + ' в боте ЖКХ: сможете передавать показания. Ссылка сработает один раз.';
    return t.replace(inv.url, '').trim();
  }
  async function sendLink(inv) {
    if (canShareMax()) {
      try {
        const r = await W.shareMaxContent({ text: shareText(inv), link: inv.url });
        if (r && r.status === 'shared') { haptic('success'); toast('Ссылку отправили'); }
        return;
      } catch (e) { /* клиент не смог — копируем */ }
    }
    copyLink(inv.url);
  }
  async function copyLink(url, el) {
    let ok = false;
    try { await navigator.clipboard.writeText(url); ok = true; } catch (e) {
      const t = h('textarea', { readonly: true, class: 'offscreen' });
      t.value = url; document.body.append(t); t.select();
      try { ok = document.execCommand('copy'); } catch (e2) { ok = false; }
      t.remove();
    }
    if (ok) { haptic('success'); return toast('Ссылка скопирована. Вставьте её в чат с тем, с кем делитесь'); }
    if (el) { const r = document.createRange(); r.selectNodeContents(el); const sl = getSelection(); sl.removeAllRanges(); sl.addRange(r); }
    toast('Не получилось скопировать. Ссылка выделена — скопируйте её вручную');
  }
  // Удаления — с подтверждением в нижнем листе; после — свежий список и тост.
  async function change(path, done, leave) {
    try {
      await api(path, { method: 'DELETE' });
      meStale = true;
      haptic('success');
      if (leave) { stack.pop(); if (stack.length) render(); else toHome(); } else render();
      toast(done);
    } catch (e) {
      haptic('error');
      if (e.status === 404 || e.status === 409) meStale = true;
      // 404 — уже нет, 409 — ссылку успели принять: показываем причину и свежий список.
      if (e.status === 404 || e.status === 409) { render(); toast(e.status === 409 ? e.message : 'Список уже изменился — показываем актуальный'); } else toast(e.status === 0 ? 'Нет связи с сервером. Проверьте интернет и повторите.' : e.message);
    }
  }
  const enc = encodeURIComponent;
  async function closeMember(o, m) {
    const yes = await ask({ icon: 'lock', tone: 'bad', title: 'Закрыть доступ?', ok: 'Закрыть доступ', okCls: 'del', cancel: 'Отмена',
      text: m.name + ' больше не сможет передавать показания по адресу ' + aFull(o) + '. Мы сообщим об этом в чате.' });
    if (yes) change('/api/shares/' + enc(o.address_id) + '/members/' + enc(m.member_id), 'Доступ закрыт');
  }
  async function cancelInvite(inv) {
    const yes = await ask({ icon: 'link', tone: 'bad', title: 'Отменить ссылку?', ok: 'Отменить ссылку', okCls: 'del', cancel: 'Не отменять',
      text: 'Ссылка перестанет работать, даже если вы её уже отправили.' });
    if (yes) change('/api/shares/invites/' + enc(inv.id), 'Ссылка отменена');
  }
  async function dropReceived(a, leave) {
    const yes = await ask({ icon: 'keys', tone: 'bad', title: 'Выйти из общего доступа?', ok: 'Выйти', okCls: 'del', cancel: 'Отмена',
      text: 'Адрес ' + aFull(a) + ' пропадёт только у вас' + (a.owner && a.access !== 'denied' ? ', ' + nb(a.owner) + ' получит уведомление' : '') +
        '. Переданные показания сохранятся.' });
    if (yes) change('/api/shares/received/' + enc(a.address_id), 'Вы вышли из общего доступа', leave);
  }

  // ---------- экран: подать показания ----------
  function screenSubmit(p) {
    if (me && me.meters && !meStale) return drawSubmit(p);
    load('Подать показания', () => api('/api/me'), d => { setMe(d); drawSubmit(p); });
  }
  function drawSubmit(p) {
    const all = (me && me.meters) || [];
    if (!all.length) return setScreen('Подать показания', emptyMeters());
    // Выбран адрес на главной — подаём по нему; счётчик другого адреса (из «Счётчика») тоже покажем.
    const vis = visMeters(me);
    const meters = vis.some(m => String(m.id) === String(p.meterId)) || p.meterId == null ? vis : all;
    let sel = meters.find(m => String(m.id) === String(p.meterId)) ||
      meters.find(m => !m.submitted_this_period) || meters[0];
    let usedStub = false, usedPhoto = false;
    let inputs = {};
    const formBox = h('div');
    // Серийный номер: у счётчика без номера показание с фото принимаем только с номером (сервер: serial_required).
    const serialInp = h('input', { id: 'v-serial', class: 'serial-in', autocomplete: 'off', maxlength: '32',
      enterkeyhint: 'done', placeholder: 'Например, 01234567', 'aria-label': 'Серийный номер счётчика' });
    const serialHint = h('small', {});
    const serialBox = h('div', { class: 'field serial', hidden: true },
      h('label', { for: 'v-serial' }, 'Серийный номер'), serialInp, serialHint);
    serialInp.addEventListener('input', () => serialBox.classList.remove('err'));
    const recBox = h('div', { hidden: true }), errBox = h('div', { hidden: true });
    const file = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true });
    const gallery = h('input', { type: 'file', accept: 'image/*', hidden: true });
    const photoBtn = btn([icon('camera', 20), 'Сфотографировать'], () => file.click(), 'sec');
    const galBtn = h('button', { class: 'btn sec sq', type: 'button', 'aria-label': 'Выбрать фото из галереи', title: 'Из галереи', onclick: () => gallery.click() }, icon('image', 22));
    const sendBtn = btn('Отправить', () => send({}));
    [file, gallery].forEach(f => f.addEventListener('change', () => { if (f.files && f.files[0]) recognize(f.files[0]); }));
    const markErr = (inp, on) => inp.closest('.tablo').classList.toggle('err', on !== false);

    const one = oneAddress(meters);
    const picker = meters.length > 1 && h('div', { class: 'pick', role: 'tablist' }, meters.map(m =>
      h('button', {
        class: 'chip' + (m === sel ? ' on' : ''), type: 'button', role: 'tab', 'aria-selected': String(m === sel),
        onclick: e => {
          sel = m;
          picker.querySelectorAll('.chip').forEach(c => { c.classList.toggle('on', c === e.currentTarget); c.setAttribute('aria-selected', String(c === e.currentTarget)); });
          drawForm();
        },
      }, meterIcon(m, 20), h('b', {}, m.type_label), !one && h('small', {}, m.address_label),
      m.submitted_this_period ? h('span', { class: 'badge', title: 'подано' }, icon('check', 12)) : h('span', { class: 'dot', title: 'не подано' }))));

    function drawForm() {
      usedStub = false; usedPhoto = false;
      serialBox.hidden = true; serialInp.value = ''; serialBox.classList.remove('err');
      note(recBox);
      note(errBox);
      const n = tariffCount(sel);
      const labels = TARIFFS[n];
      const last = sel.last;
      inputs = {};
      const fields = KEYS.slice(0, n).map((k, i) => {
        const prev = last && last.values ? last.values[k] : null;
        const mir = h('span', { class: 'mir', 'aria-hidden': 'true' });
        const dl = h('span', { class: 'dl' });
        const inp = h('input', {
          id: 'v-' + k, inputmode: 'decimal', autocomplete: 'off', enterkeyhint: 'done', maxlength: '12',
          placeholder: sel.type === 'electricity' ? '0,00' : '0,000',
          'aria-label': (labels[i] || 'Показание') + (sel.unit ? ', ' + sel.unit : ''),
        });
        const paint = () => {
          const s = inp.value, j = s.search(/[.,]/);
          mir.replaceChildren(j < 0 ? s : s.slice(0, j), h('span', { class: 'f' }, j < 0 ? '' : s.slice(j)));
          mir.parentNode.classList.toggle('sm', s.length > 9);
          mir.scrollLeft = inp.scrollLeft;
          const a = toNum(s.trim()), b = toNum(prev);
          dl.textContent = a != null && b != null ? (a < b ? '−' : '+') + Math.abs(a - b).toFixed(decimals(sel)).replace('.', ',') : '';
          dl.classList.toggle('neg', a != null && b != null && a < b);
        };
        inp.addEventListener('input', () => { markErr(inp, false); paint(); });
        inp.addEventListener('scroll', () => { mir.scrollLeft = inp.scrollLeft; });
        inputs[k] = inp;
        const pp = parts(prev, sel);
        return h('div', { class: 'field' },
          h('label', { class: 'tablo', for: 'v-' + k },
            h('span', { class: 'tl' }, h('span', {}, labels[i]), sel.unit && h('span', {}, sel.unit)),
            h('span', { class: 'win' }, mir, inp)),
          h('div', { class: 'meta' },
            h('span', {}, prev != null ? ['было ', h('span', { class: 'mono' }, pp[0] + pp[1])] : 'первое показание'), dl));
      });
      const sub = [!one && mAddr(sel), sel.serial && 'номер ' + sel.serial,
        last && last.created_at && 'прошлое ' + fmtDay(last.created_at)].filter(Boolean).join(' · ');
      formBox.replaceChildren(h('div', { class: 'card panel' },
        h('div', { class: 'ph' }, meterIcon(sel, 22), h('span', { class: 'grow' }, h('b', {}, sel.type_label), sub && h('small', {}, sub))),
        h('div', { class: 'shoot' }, photoBtn, galBtn), fields, serialBox));
    }

    async function recognize(f) {
      const meterAtStart = sel;
      note(recBox);
      setBusy(photoBtn, 'Смотрим на фото…');
      const fd = new FormData();
      fd.append('meter_id', String(sel.id)); fd.append('file', f, f.name || 'photo.jpg');
      try {
        const r = await api('/api/recognize', { method: 'POST', form: fd, timeout: 30000 });
        if (meterAtStart !== sel) return;
        const vals = r.values || {};
        const keys = KEYS.slice(0, tariffCount(sel));
        const got = keys.filter(k => vals[k] != null && vals[k] !== '');
        if (r.readable === false || !got.length || (typeof r.confidence === 'number' && r.confidence < 0.5)) {
          haptic('error');
          return note(recBox, 'bad', r.message || 'Не разобрали цифры. Переснимите прямо, без бликов, или введите вручную.');
        }
        // Как прочитали (texts: «2168»), а не число с дописанными нулями («2168,00»).
        const texts = r.texts || {};
        got.forEach(k => { inputs[k].value = texts[k] || fmtNum(vals[k], sel); inputs[k].dispatchEvent(new Event('input')); });
        usedPhoto = true;
        if (!sel.serial) {
          serialBox.hidden = false;
          serialInp.value = r.serial || '';
          serialHint.textContent = r.serial ? 'С фото. Проверьте и сохраните вместе с показанием.'
            : 'Не разобрали на фото — он нужен, чтобы не перепутать счётчики. Он на корпусе, рядом со штрихкодом.';
          serialBox.classList.toggle('err', !r.serial);
        }
        // Тариф, которого нет на фото, не оставляем со старым значением: очищаем и просим ввести.
        const missing = keys.filter(k => !got.includes(k));
        missing.forEach(k => { inputs[k].value = ''; inputs[k].dispatchEvent(new Event('input')); markErr(inputs[k]); });
        usedStub = !!r.stub;
        const parts2 = [];
        if (r.stub) parts2.push('Демо-распознавание. Проверьте цифры.');
        else if (typeof r.confidence === 'number' && r.confidence < 0.8) parts2.push('Проверьте цифры внимательно.');
        else parts2.push('Цифры с фото. Проверьте и отправьте.');
        if (r.message) parts2.push(r.message);
        else if (missing.length) parts2.push(missing.map(k => 'Т' + k.slice(1)).join(', ') + ': на фото не видно — введите вручную.');
        if (r.serial_note) parts2.push(r.serial_note);
        if (r.serial_required && !sel.serial) parts2.push('Введите серийный номер счётчика.');
        else if (r.serial_mismatch === undefined && r.serial && sel.serial && normSerial(r.serial) !== normSerial(sel.serial)) {
          parts2.push('Номер на фото — ' + r.serial + ', у счётчика — ' + sel.serial + '. Проверьте, тот ли счётчик выбран.');
        }
        note(recBox, parts2.length > 1 || r.stub || missing.length ? 'warn' : 'info', parts2.join(' '));
        if (missing.length) inputs[missing[0]].focus();
      } catch (e) {
        haptic('error');
        note(recBox, 'bad', e.status === 0 ? 'Нет связи с сервером. Фото не распознано.' : e.message,
          e.status === 0 || e.status >= 500 ? () => recognize(f) : null);
      } finally {
        unBusy(photoBtn);
        file.value = ''; gallery.value = '';
      }
    }

    async function send(extra) {
      note(errBox);
      const keys = KEYS.slice(0, tariffCount(sel));
      const values = {};
      let bad = null;
      const empty = [];
      for (const k of keys) {
        const v = inputs[k].value.trim();
        if (!v) empty.push(k);
        if (!/^\d+([.,]\d+)?$/.test(v)) { markErr(inputs[k]); bad = bad || inputs[k]; }
        values[k] = v;
      }
      if (bad) {
        haptic('error');
        const ex = sel.type === 'electricity' ? '1234,56' : '123,456';
        const what = keys.length > 1 ? ' ' + empty.map(k => 'Т' + k.slice(1)).join(', ') : '';
        note(errBox, 'bad', empty.length ? 'Заполните показание' + what + ', например ' + ex : 'Введите число, например ' + ex);
        return bad.focus();
      }
      const serial = serialBox.hidden ? '' : serialInp.value.trim();
      if (usedPhoto && !sel.serial && !serial) {
        haptic('error');
        serialBox.classList.add('err');
        note(errBox, 'bad', 'Введите серийный номер счётчика — он на корпусе, рядом со штрихкодом.');
        return serialInp.focus();
      }
      const meter = sel;
      const body = Object.assign({ meter_id: meter.id, values, source: usedPhoto ? 'photo' : 'manual' }, serial ? { serial } : {}, extra);
      setBusy(sendBtn, 'Отправляем…');
      try {
        const r = await api('/api/readings', { method: 'POST', json: body });
        haptic('success');
        if (r.serial) meter.serial = r.serial;
        meter.submitted_this_period = true;
        meter.last = { period: (r.reading && r.reading.period) || '', values, created_at: new Date().toISOString() };
        go('result', { meter, values, status: r.status, stub: usedStub }, true);
      } catch (e) {
        if (e.status === 409 && e.code === 'needs_confirm') {
          const d = keys.reduce((a, k) => a + (toNum(values[k]) || 0) - (toNum(meter.last && meter.last.values && meter.last.values[k]) || 0), 0);
          const yes = await ask({
            icon: 'alert', tone: 'warn', title: 'Прирост больше обычного. Всё верно?', ok: 'Да, всё верно', cancel: 'Исправить',
            text: meter.last ? '+' + String(+d.toFixed(decimals(meter))).replace('.', ',') + ' ' + (meter.unit || '') + ' с прошлого раза' : e.message,
          });
          if (yes) return send(Object.assign({}, extra, { confirm: true }));
          return inputs[keys[0]].focus();
        }
        if (e.status === 409 && e.code === 'already_submitted') {
          const yes = await ask({ icon: 'clock', title: 'Уже подано в этом месяце', ok: 'Заменить', cancel: 'Оставить старое',
            text: e.message || 'За этот месяц уже передано показание. Заменить?' });
          return yes ? send(Object.assign({}, extra, { replace: true })) : undefined;
        }
        haptic('error');
        if (e.status === 422) {
          if (e.code === 'bad_format' || e.code === 'less_than_previous') keys.forEach(k => markErr(inputs[k]));
          if (/^serial_/.test(e.code || '')) { serialBox.hidden = false; serialBox.classList.add('err'); serialInp.focus(); }
          return note(errBox, 'bad', e.message);
        }
        note(errBox, 'bad', e.status === 0 ? 'Нет связи с сервером. Показание не отправлено.' : e.message,
          () => send(extra));
      } finally {
        unBusy(sendBtn);
      }
    }

    drawForm();
    setScreen('Подать показания', picker, formBox, recBox, errBox, file, gallery);
    setBar(sendBtn);
  }

  // ---------- экран: результат ----------
  function screenResult(p) {
    const m = p.meter;
    const next = visMeters(me).find(x => !x.submitted_this_period && x !== m);
    setScreen('Готово',
      h('div', { class: 'state done' }, ibox('check', 'ok', 34, 'si'),
        h('h2', {}, 'Записали'),
        h('p', { class: 'who' }, meterIcon(m, 16), meterTitle(m)),
        h('div', { class: 'tablo ro' }, num(m, p.values, true)),
        p.status === 'flagged' && h('span', { class: 'pill t-warn' }, icon('alert', 15), 'большой прирост — проверим'),
        h('p', { class: 'foot' }, (p.stub ? 'Цифры — демо-распознавание. ' : '') + 'Подтверждение отправили в чат. Передача в УК — демо.')),
      next && btn([meterIcon(next, 18), 'Подать: ' + next.type_label], () => go('submit', { meterId: next.id }, true)),
      btn('На главную', toHome, next ? 'sec' : ''));
  }

  // ---------- экран: счётчик ----------
  function screenMeter(p) {
    load('Счётчик', () => api('/api/meters/' + encodeURIComponent(p.id)), d => drawMeter(d.meter ? Object.assign({}, d.meter, { history: d.history }) : d));
  }
  const SHORT = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
  function chart(m, hist) {
    const tot = r => KEYS.slice(0, tariffCount(m)).reduce((s, k) => s + (toNum(r.values && r.values[k]) || 0), 0);
    const rows = hist.slice().reverse();
    const d = rows.slice(1).map((r, i) => ({ r, v: Math.max(0, tot(r) - tot(rows[i])) }));
    const max = Math.max(...d.map(x => x.v)) || 1, lastV = d[d.length - 1].v;
    return h('div', { class: 'card chart t-' + kind(m)[1] },
      h('div', { class: 'ch' }, h('span', {}, 'Расход по месяцам'),
        h('b', { class: 'mono' }, String(+lastV.toFixed(decimals(m))).replace('.', ','), h('small', {}, ' ' + (m.unit || '')))),
      h('div', { class: 'bars' }, d.map((x, i) => {
        const pr = parseDate(x.r.period);
        return h('div', { class: 'bk' + (i === d.length - 1 ? ' now' : '') + (x.r.status === 'flagged' ? ' fl' : '') },
          h('i', { style: 'height:' + Math.max(6, Math.round(x.v / max * 100)) + '%' }), h('span', {}, pr ? SHORT[pr.mo - 1] : ''));
      })));
  }
  // Откуда срок поверки: ФГИС «Аршин» (ссылка на карточку), паспорт или модель.
  function verifSource(m) {
    const src = m.verification_source === 'arshin' ? (m.arshin_demo ? 'демо-данные ФГИС' : 'по данным ФГИС «Аршин»')
      : m.verification_source === 'user' ? 'из паспорта' : m.verification_source === 'model' ? 'ориентировочно' : null;
    if (!src) return null;
    return h('small', { class: 'src' }, m.arshin_url ? h('a', { href: m.arshin_url, target: '_blank', rel: 'noopener' }, src) : src);
  }
  function drawMeter(m) {
    const hist = (m.history || []).slice(0, 12);
    const vd = daysUntil(m.verification_due), n = tariffCount(m);
    const vt = vd == null ? 'accent' : vd < 0 ? 'bad' : vd <= 60 ? 'warn' : 'ok';
    setScreen('Счётчик',
      h('div', { class: 'card mhead' }, meterIcon(m, 28),
        h('span', { class: 'grow' }, h('h2', {}, m.type_label), h('small', {}, mAddr(m)))),
      h('div', { class: 'tiles' },
        h('div', { class: 'tile t-' + vt }, h('span', { class: 'th' }, ibox('shield', vt, 16), 'Поверка'),
          h('b', { class: 'v' }, m.verification_due ? fmtDate(m.verification_due) : 'не указана'),
          h('small', {}, vd == null ? 'есть в паспорте' : vd < 0 ? 'просрочена' : vd <= 60 ? leftWord(vd) + ' ' + days(vd) : 'в порядке'),
          m.verification_due && verifSource(m)),
        h('div', { class: 'tile t-accent' },
          h('span', { class: 'th' }, ibox('hash', 'accent', 16), 'Номер'),
          h('b', { class: 'v mono' }, m.serial || '—'),
          h('small', {}, n > 1 ? n + ' ' + plural(n, ['тариф', 'тарифа', 'тарифов']) : m.serial ? 'на корпусе' : 'не указан'))),
      hist.length > 2 && chart(m, hist),
      section('История', hist.length || null),
      hist.length
        ? h('div', { class: 'list' }, hist.map(r => h('div', { class: 'hrow' },
          h('span', { class: 'grow' }, h('b', {}, fmtPeriod(r.period)),
            h('small', {}, [fmtDay(r.created_at), SOURCES[r.source]].filter(Boolean).join(' · '))),
          h('span', { class: 'r' }, num(m, r.values, true),
            r.status === 'flagged' && h('span', { class: 'pill t-warn' }, 'большой прирост')))))
        : h('div', { class: 'row' }, ibox('clock', 'accent', 18), h('span', { class: 'grow' }, h('small', {}, 'Показаний пока нет'))),
      h('button', { class: 'btn ghost quiet', type: 'button', onclick: () => deleteMeter(m) }, 'Удалить счётчик'));
    setBar(btn([icon('camera', 20), 'Подать показания'], () => go('submit', { meterId: m.id })));
  }
  // Удаление — не основная функция: тихая кнопка внизу экрана и обязательный вопрос в нижнем листе.
  async function deleteMeter(m) {
    const yes = await ask({ icon: 'trash', tone: 'bad', title: 'Удалить счётчик?', ok: 'Удалить', okCls: 'del', cancel: 'Отмена',
      text: meterTitle(m) + '. Переданные показания сохранятся, но подавать новые по этому счётчику будет нельзя.' });
    if (!yes) return;
    try {
      await api('/api/meters/' + encodeURIComponent(m.id), { method: 'DELETE' });
      haptic('success');
      toHome();
      toast('Счётчик удалён');
    } catch (e) {
      haptic('error');
      if (e.status === 404) { toHome(); toast('Счётчик уже удалён'); } else toast(e.message);
    }
  }

  const SCREENS = { home: screenHome, submit: screenSubmit, result: screenResult, meter: screenMeter, profile: screenProfile, addr: screenAddr,
    share: screenShare, share_new: screenShareNew, share_link: screenShareLink, shared: screenShared };
  $ava.addEventListener('click', () => go('profile'));

  // ---------- старт ----------
  if (!INIT && !DEV_USER) screenOutside();
  else if (!API && /\.github\.io$/.test(location.hostname)) {
    // Статика на GitHub Pages, а адрес бэкенда не задан (MINIAPP_API_BASE) — не ходим на github.io/api.
    // Это не сбой: сервер мини-приложения просто не подключён, а бот в чате работает.
    setScreen('ЖКХ', stateCard('chat', 'accent', 'Сервер мини-приложения не подключён',
      'Бот работает — показания можно передать в чате.',
      btn('Вернуться в чат', closeApp), btn('Повторить', () => location.reload(), 'sec')));
  } else toHome();
})();
