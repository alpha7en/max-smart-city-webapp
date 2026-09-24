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
  const $sub = document.getElementById('sub');
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
  function closeApp() {
    try { if (W && W.close) return W.close(); } catch (e) { /* ниже подсказка */ }
    toast('Закройте это окно, чтобы вернуться в чат');
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
          h('div', { class: 'acts' }, btn(o.ok, () => done(true)), o.cancel && btn(o.cancel, () => done(false), 'ghost'))));
      document.body.append(dlg);
    });
  }

  // ---------- API ----------
  const CODE_MSG = {
    no_access: 'Нет доступа к этому адресу. Попросите собственника открыть доступ в боте.',
    too_large: 'Фото слишком большое. Снимите ещё раз или введите показание вручную.',
    not_image: 'Это не похоже на фото. Сфотографируйте счётчик ещё раз.',
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
    return Number(v).toFixed(decimals(m)).replace('.', ',');
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
  const meterTitle = m => [m.type_label, m.address_label].filter(Boolean).join(' · ');
  const oneAddress = ms => new Set(ms.map(m => m.address_label)).size === 1;
  const normSerial = s => String(s || '').replace(/[\s-]/g, '').toLowerCase();

  // ---------- навигация ----------
  const stack = [];
  let renderId = 0, me = null;

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

  function render() {
    renderId++; closeDialog(); syncBack(); window.scrollTo(0, 0);
    const cur = stack[stack.length - 1];
    SCREENS[cur.name](cur.params);
  }
  function setScreen(title, ...nodes) {
    $title.textContent = title;
    $sub.hidden = true;
    setBar();
    $view.replaceChildren(...nodes.flat().filter(Boolean));
  }
  function setBar(...buttons) {
    const b = buttons.filter(Boolean);
    $bar.replaceChildren(...b);
    document.body.classList.toggle('has-bar', !($bar.hidden = !b.length));
  }
  function skeleton() {
    const sk = cls => h('div', { class: 'sk ' + cls });
    return h('div', { class: 'stack', 'aria-busy': 'true', 'aria-label': 'Загружаем' },
      sk('s1'), h('div', { class: 'tiles' }, sk('s2'), sk('s2')), sk('s3'));
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
  async function load(title, fetcher, draw) {
    const my = renderId;
    setScreen(title, skeleton());
    try {
      const d = await fetcher();
      if (my === renderId) draw(d);
    } catch (e) {
      if (my !== renderId) return;
      haptic('error');
      setScreen(title, errorCard(e, () => load(title, fetcher, draw)));
    }
  }

  // ---------- экран: вне MAX ----------
  function screenOutside() {
    setScreen('ЖКХ', stateCard('chat', 'accent', 'Откройте мини-приложение из чата с ботом в MAX',
      'Так мы узнаем вас и покажем ваши счётчики.'));
  }

  // ---------- экран: главная ----------
  const urgentKind = u => { const k = String(u.kind || ''); return /verif/.test(k) ? 'verification' : /bill|pay/.test(k) ? 'bill' : 'submit'; };
  function openUrgent(u) {
    const k = urgentKind(u);
    if (k === 'submit') return go('submit');
    if (k === 'verification') {
      return ask({ icon: 'shield', tone: 'warn', title: 'Запись на поверку появится скоро', ok: 'Понятно',
        text: 'Пока позвоните в УК или аккредитованную организацию. Дату после поверки внесите в «Мои счётчики» в чате.' });
    }
    return ask({ icon: 'receipt', title: 'Оплата появится скоро', text: 'Сейчас оплатить можно по квитанции в банке. Счёт здесь — демо.', ok: 'Понятно' });
  }
  // Строки дашборда из API разбираем в плитки; «простыню» не показываем.
  function parseDash(lines, urgent) {
    const out = { extra: [] };
    for (const raw of lines) {
      const l = String(raw || '').trim();
      let r;
      if (!l || /мини-приложени|счётчиков пока нет/i.test(l) || (urgent && l === urgent.text)) continue;
      if ((r = /^Показания за (\S+) — до (\d+) ([^\s,]+)(?:, (?:осталось|остался) (\d+) дн)?/.exec(l))) {
        out.due = { day: +r[2], mon: r[3], left: r[4] != null ? +r[4] : null };
      } else if ((r = /^Следующая подача — с (.+)$/.exec(l))) out.next = r[1];
      else if ((r = /^Сч[её]т: (.+?) до (.+?)( \(демо\))?$/.exec(l))) out.bill = { sum: r[1], date: r[2], demo: !!r[3] };
      else if (!/^Поверка|^Счётчиков: \d| — (не )?подано/.test(l)) out.extra.push(fixDays(l));
    }
    return out;
  }
  function hero(meters, info, urg) {
    const done = meters.filter(m => m.submitted_this_period).length;
    const all = done === meters.length;
    const due = info.due;
    let left = due && due.left != null ? due.left : urg && urg.days_left != null ? +urg.days_left : null;
    if (left == null && due) {
      const mo = MONTHS_GEN.indexOf(due.mon);
      if (mo >= 0) left = daysUntil(new Date().getFullYear() + '-' + String(mo + 1).padStart(2, '0') + '-' + String(due.day).padStart(2, '0'));
    }
    let eb = 'Показания';
    let big = [h('b', {}, done), h('span', {}, 'из ' + meters.length)];
    if (left != null) {
      eb = due ? 'Подача до ' + due.day + ' ' + due.mon : 'Подача показаний';
      big = left <= 0 ? [h('b', { class: 'w' }, 'Сегодня'), h('i', {}, 'последний день')]
        : [h('b', {}, left), h('span', {}, plural(left, DAYS)), h('i', {}, leftWord(left))];
    } else if (info.next) {
      eb = 'Следующая подача';
      big = [h('b', { class: 'w' }, 'с ' + shortDate(info.next))];
    }
    const hot = !all && left != null && left <= 3;
    return h('section', { class: 'hero' + (hot ? ' hot' : '') },
      h('div', { class: 'eb' }, h('span', {}, eb), hot && h('span', { class: 'tag' }, 'срочно')),
      h('div', { class: 'big' }, big),
      h('div', { class: 'prog' },
        h('div', { class: 'segs' }, meters.slice(0, 12).map(m => h('i', { class: m.submitted_this_period ? 'on' : '' }))),
        h('span', {}, all ? [icon('check', 16), 'всё подано'] : 'подано ' + done + ' из ' + meters.length)),
      btn([icon('camera', 20), 'Подать показания'], () => go('submit'), 'white'));
  }
  function tile(ic, tone, label, value, sub, onclick) {
    return h('button', { class: 'tile t-' + tone, type: 'button', onclick },
      h('span', { class: 'th' }, ibox(ic, tone, 16), label, h('span', { class: 'chev' }, icon('chev', 16))),
      h('b', { class: 'v' }, value), sub && h('small', {}, sub));
  }
  function tiles(meters, info, u) {
    const uk = u && urgentKind(u);
    const out = [];
    let v = null;
    meters.forEach(m => { const n = daysUntil(m.verification_due); if (n != null && (!v || n < v.n)) v = { n, m }; });
    if (v || uk === 'verification') {
      const n = v ? v.n : +u.days_left;
      const p = v && parseDate(v.m.verification_due);
      out.push(tile('shield', n < 0 ? 'bad' : n <= 30 ? 'warn' : 'ok', 'Поверка',
        n < 0 ? 'просрочена' : n > 365 && p ? MONTHS[p.mo - 1] + ' ' + p.y : days(n),
        v && (v.m.type_label + ' · ' + shortDate(fmtDate(v.m.verification_due))), () => openUrgent({ kind: 'verification' })));
    }
    if (info.bill || uk === 'bill') {
      const b = info.bill;
      out.push(tile('receipt', uk === 'bill' ? 'warn' : 'accent', 'Счёт', b ? b.sum : days(+u.days_left),
        b && ('до ' + shortDate(b.date) + (b.demo ? ' · демо' : '')), () => openUrgent({ kind: 'bill' })));
    }
    return out.length > 0 && h('div', { class: 'tiles' }, out);
  }
  function meterItem(m, showAddr) {
    return h('div', { class: 'item' },
      h('button', { class: 'item-main', type: 'button', onclick: () => go('meter', { id: m.id }) },
        meterIcon(m),
        h('span', { class: 'grow' }, h('b', {}, m.type_label),
          m.last ? num(m, m.last.values) : h('small', {}, 'показаний пока нет'),
          showAddr && h('small', {}, m.address_label))),
      m.submitted_this_period
        ? h('span', { class: 'st' }, icon('check', 15), 'подано')
        : h('button', { class: 'mini', type: 'button', onclick: () => go('submit', { meterId: m.id }) }, 'Подать'));
  }
  const section = (title, count) => h('div', { class: 'sech' }, h('span', {}, title), count != null && h('small', {}, count));
  function screenHome() {
    load('ЖКХ', () => api('/api/me'), d => { me = d; drawHome(d); });
  }
  function drawHome(d) {
    if (!d.registered) {
      return setScreen('ЖКХ', stateCard('chat', 'accent', 'Продолжите регистрацию в чате с ботом',
        'Это займёт минуту: имя, телефон и адрес.', btn('Вернуться в чат', closeApp)));
    }
    const meters = d.meters || [], dash = d.dashboard || {}, u = dash.urgent || null;
    const info = parseDash(dash.lines || [], u);
    const pending = (d.addresses || []).filter(a => a.access === 'pending');
    const addrs = [...new Set((d.addresses || []).filter(a => a.access !== 'pending').map(a => a.label)
      .concat(meters.map(m => m.address_label)).filter(Boolean))];
    setScreen('ЖКХ',
      meters.length
        ? hero(meters, info, u && urgentKind(u) === 'submit' && u)
        : stateCard('camera', 'accent', 'Счётчиков пока нет', 'Пришлите фото счётчика в чат — мы его добавим.',
          btn('Вернуться в чат', closeApp)),
      tiles(meters, info, u),
      pending.map(a => h('div', { class: 'row' }, ibox('clock', 'warn', 18),
        h('span', { class: 'grow' }, h('b', {}, a.label), h('small', {}, 'Доступ ждёт подтверждения собственника')))),
      info.extra.length > 0 && h('div', { class: 'row' }, ibox('alert', 'accent', 18),
        h('span', { class: 'grow' }, info.extra.map(l => h('small', {}, l)))),
      meters.length > 0 && [section('Счётчики', meters.length),
        h('div', { class: 'list' }, meters.map(m => meterItem(m, !oneAddress(meters)))),
        h('p', { class: 'foot' }, 'Демо: подача с 15 по 25 число, счёт смоделирован')]);
    if (addrs.length) {
      $sub.hidden = false;
      $sub.textContent = addrs.length === 1 ? addrs[0] : addrs.length + ' ' + plural(addrs.length, ['адрес', 'адреса', 'адресов']);
    }
  }

  // ---------- экран: подать показания ----------
  function screenSubmit(p) {
    if (me && me.meters) return drawSubmit(p);
    load('Подать показания', () => api('/api/me'), d => { me = d; drawSubmit(p); });
  }
  function drawSubmit(p) {
    const meters = (me && me.meters) || [];
    if (!meters.length) {
      return setScreen('Подать показания', stateCard('camera', 'accent', 'Счётчиков пока нет',
        'Пришлите фото счётчика в чат — мы его добавим.', btn('Вернуться в чат', closeApp)));
    }
    let sel = meters.find(m => String(m.id) === String(p.meterId)) ||
      meters.find(m => !m.submitted_this_period) || meters[0];
    let usedStub = false;
    let inputs = {};
    const formBox = h('div');
    const recBox = h('div', { hidden: true }), errBox = h('div', { hidden: true });
    const file = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true });
    const photoBtn = btn([icon('camera', 20), 'Сфотографировать'], () => file.click(), 'sec');
    const sendBtn = btn('Отправить', () => send({}));
    file.addEventListener('change', () => { if (file.files && file.files[0]) recognize(file.files[0]); });
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
      usedStub = false;
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
      const sub = [!one && sel.address_label, sel.serial && '№ ' + sel.serial,
        last && last.created_at && 'прошлое ' + fmtDay(last.created_at)].filter(Boolean).join(' · ');
      formBox.replaceChildren(h('div', { class: 'card panel' },
        h('div', { class: 'ph' }, meterIcon(sel, 22), h('span', { class: 'grow' }, h('b', {}, sel.type_label), sub && h('small', {}, sub))),
        photoBtn, fields));
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
        if (!got.length || (typeof r.confidence === 'number' && r.confidence < 0.5)) {
          haptic('error');
          return note(recBox, 'bad', 'Не разобрали цифры. Переснимите прямо, без бликов, или введите вручную.');
        }
        got.forEach(k => { inputs[k].value = fmtNum(vals[k], sel); inputs[k].dispatchEvent(new Event('input')); });
        usedStub = !!r.stub;
        const parts2 = [];
        if (r.stub) parts2.push('Демо-распознавание. Проверьте цифры.');
        else if (typeof r.confidence === 'number' && r.confidence < 0.8) parts2.push('Проверьте цифры внимательно.');
        else parts2.push('Цифры с фото. Проверьте и отправьте.');
        if (r.serial && sel.serial && normSerial(r.serial) !== normSerial(sel.serial)) {
          parts2.push('На фото № ' + r.serial + ', у счётчика № ' + sel.serial + '. Тот ли это счётчик?');
        }
        note(recBox, parts2.length > 1 || r.stub ? 'warn' : 'info', parts2.join(' '));
      } catch (e) {
        haptic('error');
        note(recBox, 'bad', e.status === 0 ? 'Нет связи с сервером. Фото не распознано.' : e.message,
          e.status === 0 || e.status >= 500 ? () => recognize(f) : null);
      } finally {
        unBusy(photoBtn);
        file.value = '';
      }
    }

    async function send(extra) {
      note(errBox);
      const keys = KEYS.slice(0, tariffCount(sel));
      const values = {};
      let bad = null;
      for (const k of keys) {
        const v = inputs[k].value.trim();
        if (!/^\d+([.,]\d+)?$/.test(v)) { markErr(inputs[k]); bad = bad || inputs[k]; }
        values[k] = v;
      }
      if (bad) {
        haptic('error');
        note(errBox, 'bad', 'Введите число, например ' + (sel.type === 'electricity' ? '1234,56' : '123,456'));
        return bad.focus();
      }
      const meter = sel;
      setBusy(sendBtn, 'Отправляем…');
      try {
        const r = await api('/api/readings', { method: 'POST', json: Object.assign({ meter_id: meter.id, values }, extra) });
        haptic('success');
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
          return note(errBox, 'bad', e.message);
        }
        note(errBox, 'bad', e.status === 0 ? 'Нет связи с сервером. Показание не отправлено.' : e.message,
          () => send(extra));
      } finally {
        unBusy(sendBtn);
      }
    }

    drawForm();
    setScreen('Подать показания', picker, formBox, recBox, errBox, file);
    setBar(sendBtn);
  }

  // ---------- экран: результат ----------
  function screenResult(p) {
    const m = p.meter;
    const next = ((me && me.meters) || []).find(x => !x.submitted_this_period && x !== m);
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
  function drawMeter(m) {
    const hist = (m.history || []).slice(0, 12);
    const vd = daysUntil(m.verification_due), n = tariffCount(m);
    const vt = vd == null ? 'accent' : vd < 0 ? 'bad' : vd <= 60 ? 'warn' : 'ok';
    setScreen('Счётчик',
      h('div', { class: 'card mhead' }, meterIcon(m, 28),
        h('span', { class: 'grow' }, h('h2', {}, m.type_label), h('small', {}, m.address_label))),
      h('div', { class: 'tiles' },
        h('div', { class: 'tile t-' + vt }, h('span', { class: 'th' }, ibox('shield', vt, 16), 'Поверка'),
          h('b', { class: 'v' }, m.verification_due ? fmtDate(m.verification_due) : 'не указана'),
          h('small', {}, vd == null ? 'укажите в чате' : vd < 0 ? 'просрочена' : vd <= 60 ? leftWord(vd) + ' ' + days(vd) : 'в порядке')),
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
        : h('div', { class: 'row' }, ibox('clock', 'accent', 18), h('span', { class: 'grow' }, h('small', {}, 'Показаний пока нет'))));
    setBar(btn([icon('camera', 20), 'Подать показания'], () => go('submit', { meterId: m.id })));
  }

  const SCREENS = { home: screenHome, submit: screenSubmit, result: screenResult, meter: screenMeter };

  // ---------- старт ----------
  if (!INIT && !DEV_USER) screenOutside();
  else if (!API && /\.github\.io$/.test(location.hostname)) {
    // Статика на GitHub Pages, а адрес бэкенда не задан — не ходим на github.io/api.
    setScreen('ЖКХ', stateCard('alert', 'bad', 'Сервис временно недоступен', 'Уже чиним. Попробуйте чуть позже.',
      btn('Повторить', () => location.reload())));
  } else toHome();
})();
