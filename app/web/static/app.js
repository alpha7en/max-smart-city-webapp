/* Мини-приложение «ЖКХ» для MAX. Vanilla JS, без сборки.
   Контракт API: SPEC §6. Все запросы несут заголовок X-Max-Init-Data. */
'use strict';
(function () {
  const W = window.WebApp || null;
  try { if (W && W.ready) W.ready(); } catch (e) { /* мост MAX недоступен */ }

  const qs = new URLSearchParams(location.search);
  const INIT = (W && W.initData) || '';
  const DEV_USER = qs.get('dev_user') || ''; // только для локальной разработки (DEV_AUTH=true на сервере)
  const metaBase = (document.querySelector('meta[name="api-base"]') || {}).content || '';
  const API = (qs.get('api') || metaBase).trim().replace(/\/+$/, '');

  const $view = document.getElementById('view');
  const $title = document.getElementById('title');
  const $back = document.getElementById('back');
  const nativeBack = !!(W && W.BackButton && typeof W.BackButton.show === 'function');

  // ---------- тема ----------
  function applyTheme() {
    if (W && /^(dark|light)$/.test(W.colorScheme)) document.documentElement.dataset.theme = W.colorScheme;
  }
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
    camera: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    check: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16 10"/>',
    alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5v.01"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    chat: '<path d="M5 5h14v10H9l-4 4z"/>',
  };
  function icon(name, size) {
    const s = size || 22;
    const span = h('span', { class: 'ic', 'aria-hidden': 'true' });
    span.innerHTML = '<svg width="' + s + '" height="' + s + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + SVG[name] + '</svg>';
    return span;
  }
  function btn(text, onclick, cls) {
    return h('button', { class: 'btn' + (cls ? ' ' + cls : ''), type: 'button', onclick }, text);
  }
  function setBusy(b, text) { b._orig = Array.from(b.childNodes); b.disabled = true; b.replaceChildren(h('span', { class: 'spin' }), text); }
  function unBusy(b) { if (b._orig) b.replaceChildren(...b._orig); b.disabled = false; }
  function note(box, kind, text, retry) {
    box.replaceChildren();
    if (!text) return box.classList.add('hide');
    box.className = 'note ' + kind;
    box.append(text);
    if (retry) box.append(btn('Повторить', retry, 'sec2'));
  }
  let toastTimer = 0;
  function toast(text) {
    let t = document.querySelector('.toast');
    if (!t) { t = h('div', { class: 'toast', role: 'status' }); document.body.append(t); }
    t.textContent = text;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.remove(), 3000);
  }
  function haptic(type) {
    try { if (W && W.HapticFeedback) W.HapticFeedback.notificationOccurred(type); } catch (e) { /* нет вибрации */ }
  }
  function closeApp() {
    try { if (W && W.close) return W.close(); } catch (e) { /* ниже подсказка */ }
    toast('Закройте это окно, чтобы вернуться в чат');
  }

  // ---------- диалог ----------
  let dlg = null;
  let dlgDone = null;
  function closeDialog() { if (dlg) dlg.remove(); dlg = null; dlgDone = null; }
  function ask(o) {
    return new Promise(resolve => {
      closeDialog();
      const done = v => { closeDialog(); resolve(v); };
      dlgDone = done;
      dlg = h('div', { class: 'dlg', role: 'dialog', 'aria-modal': 'true', onclick: e => { if (e.target === dlg) done(false); } },
        h('div', { class: 'card' },
          h('h2', {}, o.title),
          o.text && h('p', {}, o.text),
          btn(o.ok, () => done(true)),
          o.cancel && btn(o.cancel, () => done(false), 'ghost')));
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
      throw new ApiError(0, 'network', 'Не удалось связаться с сервером');
    } finally {
      clearTimeout(timer);
    }
    if (!res.ok) {
      const code = (data && data.code) || 'http_' + res.status;
      const fallback = CODE_MSG[code] || STATUS_MSG[res.status] ||
        (res.status >= 500 ? STATUS_MSG[500] : 'Не получилось выполнить запрос. Попробуйте ещё раз.');
      throw new ApiError(res.status, code, code === 'no_access' ? CODE_MSG.no_access : (data && data.message) || fallback);
    }
    if (!data) throw new ApiError(res.status, 'bad_response', 'Сервер ответил непонятно. Попробуйте ещё раз.');
    return data;
  }

  // ---------- форматирование ----------
  const KEYS = ['t1', 't2', 't3'];
  const TARIFFS = { 1: ['Показание'], 2: ['Т1 · день', 'Т2 · ночь'], 3: ['Т1 · пик', 'Т2 · ночь', 'Т3 · полупик'] };
  const MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
  const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  const SOURCES = { photo: 'по фото', photo_edited: 'по фото, исправлено', manual: 'вручную', miniapp: 'в приложении' };

  const tariffCount = m => Math.min(3, Math.max(1, +m.tariffs || 1));
  const decimals = m => (m && m.type === 'electricity' ? 2 : 3);
  // Значения в ответах API — числа в единицах счётчика (123.456) или строки, как ввёл пользователь.
  function fmtNum(v, m) {
    if (v == null || v === '') return '—';
    if (typeof v === 'string') return v.trim().replace('.', ',');
    return Number(v).toFixed(decimals(m)).replace('.', ',');
  }
  function fmtValues(m, vals) {
    const v = vals || {};
    const n = tariffCount(m);
    const unit = m.unit ? ' ' + m.unit : '';
    if (n === 1) return fmtNum(v.t1, m) + unit;
    return KEYS.slice(0, n).map((k, i) => 'Т' + (i + 1) + ' ' + fmtNum(v[k], m)).join(' · ') + unit;
  }
  function parseDate(s) {
    const r = /^(\d{4})-(\d{2})(?:-(\d{2}))?/.exec(s || '');
    return r ? { y: +r[1], mo: +r[2], d: r[3] ? +r[3] : 0 } : null;
  }
  function fmtDate(s) {
    const p = parseDate(s);
    if (!p || !p.d) return s || '';
    return p.d + ' ' + MONTHS_GEN[p.mo - 1] + (p.y !== new Date().getFullYear() ? ' ' + p.y : '');
  }
  function fmtPeriod(s) {
    const p = parseDate(s);
    if (!p) return s || '';
    const n = MONTHS[p.mo - 1];
    return n[0].toUpperCase() + n.slice(1) + ' ' + p.y;
  }
  function fmtDay(s) {
    const p = parseDate(s);
    return p && p.d ? String(p.d).padStart(2, '0') + '.' + String(p.mo).padStart(2, '0') : '';
  }
  const meterTitle = m => [m.type_label, m.address_label].filter(Boolean).join(' · ');
  const oneAddress = ms => new Set(ms.map(m => m.address_label)).size === 1;
  const normSerial = s => String(s || '').replace(/[\s-]/g, '').toLowerCase();

  // ---------- навигация ----------
  const stack = [];
  let renderId = 0;
  let me = null;

  function go(name, params, replace) { if (replace) stack.pop(); stack.push({ name, params: params || {} }); render(); }
  function back() {
    if (dlgDone) return dlgDone(false);
    if (stack.length > 1) { stack.pop(); render(); }
  }
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
    $view.replaceChildren(...nodes.flat().filter(Boolean));
  }
  function skeleton() {
    const sk = hgt => h('div', { class: 'sk', style: 'height:' + hgt + 'px;margin-bottom:12px' });
    return h('div', { 'aria-busy': 'true', 'aria-label': 'Загружаем' }, sk(92), sk(54), sk(64), sk(64));
  }
  function errorCard(err, retry) {
    const net = err.status === 0;
    const title = net ? 'Не удалось связаться с сервером'
      : err.status === 403 ? 'Нет доступа' : err.status === 404 ? 'Не нашли' : 'Что-то пошло не так';
    return h('div', { class: 'card state bad' }, icon('alert', 36), h('h2', {}, title),
      h('p', { class: 'muted' }, net ? 'Проверьте интернет и попробуйте ещё раз.' : err.message),
      retry && btn('Повторить', retry));
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
    setScreen('ЖКХ', h('div', { class: 'card state' }, icon('chat', 36),
      h('h2', {}, 'Откройте мини-приложение из чата с ботом в MAX'),
      h('p', { class: 'muted' }, 'Так мы узнаем, что это вы, и покажем ваши счётчики.')));
  }

  // ---------- экран: главная ----------
  const urgentKind = u => {
    const k = String(u.kind || '');
    return /verif/.test(k) ? 'verification' : /bill|pay/.test(k) ? 'bill' : 'submit';
  };
  function openUrgent(u) {
    const kind = urgentKind(u);
    if (kind === 'submit') return go('submit');
    if (kind === 'verification') {
      return ask({
        title: 'Запись на поверку появится скоро',
        text: 'Пока можно поверить счётчик самостоятельно: позвоните в УК или аккредитованную организацию. Дату после поверки внесите в «Мои счётчики» в чате.',
        ok: 'Понятно',
      });
    }
    return ask({ title: 'Оплата появится скоро', text: 'Сейчас оплатить можно по квитанции в банке. Счёт здесь — демо.', ok: 'Понятно' });
  }
  function dashBlocks(lines, skip) {
    const blocks = [[]];
    for (const raw of lines) {
      const l = String(raw || '').trim();
      if (!l) { if (blocks[blocks.length - 1].length) blocks.push([]); continue; }
      if (l === skip || /мини-приложени|счётчиков пока нет/i.test(l)) continue;
      blocks[blocks.length - 1].push(l);
    }
    return blocks.filter(b => b.length).map(b => h('p', {}, b.map((l, i) => [i ? h('br') : null, l])));
  }
  function meterItem(m) {
    const done = !!m.submitted_this_period;
    return h('button', { class: 'item', type: 'button', onclick: () => go('meter', { id: m.id }) },
      h('div', { class: 'grow' },
        h('div', { class: 't' }, meterTitle(m)),
        h('div', { class: 'muted small' }, m.last ? 'Последнее: ' + fmtValues(m, m.last.values) : 'Показаний пока нет')),
      h('span', { class: 'pill ' + (done ? 'ok' : 'no') }, done ? 'подано' : 'не подано'),
      h('span', { class: 'chev' }, icon('chev', 18)));
  }
  function screenHome() {
    load('ЖКХ', () => api('/api/me'), d => { me = d; drawHome(d); });
  }
  function drawHome(d) {
    if (!d.registered) {
      return setScreen('ЖКХ', h('div', { class: 'card state' }, icon('chat', 36),
        h('h2', {}, 'Продолжите регистрацию в чате с ботом — это займёт минуту'),
        h('p', { class: 'muted' }, 'Нужны имя, телефон и адрес. Потом здесь появятся ваши счётчики.'),
        btn('Вернуться в чат', closeApp)));
    }
    const meters = d.meters || [];
    const dash = d.dashboard || {};
    const u = dash.urgent;
    const uSubmit = u && urgentKind(u) === 'submit';
    const blocks = dashBlocks(dash.lines || [], u && u.text);
    const pending = (d.addresses || []).filter(a => a.access === 'pending');
    setScreen('ЖКХ',
      u && !uSubmit && h('button', { class: 'card urgent', type: 'button', onclick: () => openUrgent(u) },
        icon('clock', 26), h('div', { class: 'grow' }, h('b', {}, u.text)), h('span', { class: 'chev' }, icon('chev', 18))),
      pending.map(a => h('div', { class: 'note warn' },
        'Доступ к адресу «' + a.label + '» ждёт подтверждения собственника.')),
      blocks.length && h('div', { class: 'card' }, blocks, meters.length > 0 &&
        h('p', { class: 'muted small' }, 'Сроки подачи в демо смоделированы: с 15 по 25 число.')),
      meters.length && btn(uSubmit ? u.text : 'Подать показания', () => go('submit'), 'big'),
      h('div', { class: 'sec' }, 'Счётчики'),
      meters.length
        ? h('div', { class: 'list' }, meters.map(meterItem))
        : h('div', { class: 'card' },
          h('p', {}, 'Счётчиков пока нет — пришлите фото счётчика в чат, и мы его добавим.'),
          btn('Вернуться в чат', closeApp, 'sec2')));
  }

  // ---------- экран: подать показания ----------
  function screenSubmit(p) {
    if (me && me.meters) return drawSubmit(p);
    load('Подать показания', () => api('/api/me'), d => { me = d; drawSubmit(p); });
  }
  function drawSubmit(p) {
    const meters = (me && me.meters) || [];
    if (!meters.length) {
      return setScreen('Подать показания', h('div', { class: 'card state' }, icon('camera', 36),
        h('h2', {}, 'Счётчиков пока нет'),
        h('p', { class: 'muted' }, 'Пришлите фото счётчика в чат, и мы его добавим.'),
        btn('Вернуться в чат', closeApp)));
    }
    let sel = meters.find(m => String(m.id) === String(p.meterId)) ||
      meters.find(m => !m.submitted_this_period) || meters[0];
    let usedStub = false;
    let inputs = {};
    const formBox = h('div');
    const recBox = h('div', { class: 'hide' });
    const errBox = h('div', { class: 'hide' });
    const file = h('input', { type: 'file', accept: 'image/*', capture: 'environment', class: 'hide' });
    const photoBtn = btn([icon('camera', 20), 'Сфотографировать'], () => file.click(), 'sec2');
    const sendBtn = btn('Отправить', () => send({}));
    file.addEventListener('change', () => { if (file.files && file.files[0]) recognize(file.files[0]); });

    const chips = meters.length > 1 && h('div', { class: 'chips', role: 'tablist' }, meters.map(m =>
      h('button', {
        class: 'chip' + (m === sel ? ' on' : ''), type: 'button', role: 'tab', 'aria-selected': String(m === sel),
        onclick: e => {
          sel = m;
          chips.querySelectorAll('.chip').forEach(c => { c.classList.remove('on'); c.setAttribute('aria-selected', 'false'); });
          e.currentTarget.classList.add('on');
          e.currentTarget.setAttribute('aria-selected', 'true');
          drawForm();
        },
      }, oneAddress(meters) ? m.type_label : meterTitle(m))));

    function drawForm() {
      usedStub = false;
      note(recBox);
      note(errBox);
      const n = tariffCount(sel);
      const labels = TARIFFS[n];
      inputs = {};
      const fields = KEYS.slice(0, n).map((k, i) => {
        const inp = h('input', {
          id: 'v-' + k, inputmode: 'decimal', autocomplete: 'off', enterkeyhint: 'done',
          placeholder: sel.type === 'electricity' ? 'например, 1234,56' : 'например, 123,456',
          oninput: () => inp.classList.remove('err'),
        });
        inputs[k] = inp;
        return h('div', { class: 'field' }, h('label', { for: 'v-' + k }, labels[i]),
          h('div', { class: 'inp' }, inp, sel.unit && h('span', {}, sel.unit)));
      });
      const last = sel.last;
      formBox.replaceChildren(h('div', { class: 'card' },
        h('h2', {}, meterTitle(sel)),
        h('p', { class: 'muted' }, last
          ? 'В прошлый раз: ' + fmtValues(sel, last.values) + (last.created_at ? ', ' + fmtDay(last.created_at) : '')
          : 'Это первое показание по счётчику.'),
        fields));
    }

    async function recognize(f) {
      const meterAtStart = sel;
      note(recBox);
      setBusy(photoBtn, 'Смотрим на фото…');
      const fd = new FormData();
      fd.append('meter_id', String(sel.id));
      fd.append('file', f, f.name || 'photo.jpg');
      try {
        const r = await api('/api/recognize', { method: 'POST', form: fd, timeout: 30000 });
        if (meterAtStart !== sel) return;
        const vals = r.values || {};
        const keys = KEYS.slice(0, tariffCount(sel));
        const got = keys.filter(k => vals[k] != null && vals[k] !== '');
        if (!got.length || (typeof r.confidence === 'number' && r.confidence < 0.5)) {
          haptic('error');
          return note(recBox, 'bad', 'Не получилось разобрать цифры — так бывает из-за бликов или съёмки под углом. Попробуйте переснять или введите показание вручную.');
        }
        got.forEach(k => { inputs[k].value = fmtNum(vals[k], sel); inputs[k].classList.remove('err'); });
        usedStub = !!r.stub;
        const parts = [];
        if (r.stub) parts.push('Демо-распознавание: значение подставлено, проверьте.');
        else if (typeof r.confidence === 'number' && r.confidence < 0.8) parts.push('Проверьте цифры внимательно.');
        else parts.push('Цифры подставили — проверьте и отправьте.');
        if (r.serial && sel.serial && normSerial(r.serial) !== normSerial(sel.serial)) {
          parts.push('На фото номер ' + r.serial + ', а у счётчика ' + sel.serial + '. Проверьте, тот ли это счётчик.');
        }
        note(recBox, parts.length > 1 || r.stub ? 'warn' : 'info', parts.join(' '));
      } catch (e) {
        haptic('error');
        note(recBox, 'bad', e.status === 0 ? 'Не удалось связаться с сервером. Фото не распознано.' : e.message,
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
        if (!/^\d+([.,]\d+)?$/.test(v)) { inputs[k].classList.add('err'); bad = bad || inputs[k]; }
        values[k] = v;
      }
      if (bad) {
        haptic('error');
        note(errBox, 'bad', 'Введите число, например 123,456');
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
          const title = 'Прирост необычно большой. Всё верно?';
          const yes = await ask({ title, text: e.message !== title && e.message, ok: 'Да, всё верно', cancel: 'Исправить' });
          if (yes) return send(Object.assign({}, extra, { confirm: true }));
          return inputs[keys[0]].focus();
        }
        if (e.status === 409 && e.code === 'already_submitted') {
          const yes = await ask({
            title: e.message || 'За этот месяц уже передано показание. Заменить?',
            ok: 'Заменить', cancel: 'Оставить старое',
          });
          if (yes) return send(Object.assign({}, extra, { replace: true }));
          return;
        }
        haptic('error');
        if (e.status === 422) {
          if (e.code === 'bad_format' || e.code === 'less_than_previous') keys.forEach(k => inputs[k].classList.add('err'));
          return note(errBox, 'bad', e.message);
        }
        note(errBox, 'bad', e.status === 0 ? 'Не удалось связаться с сервером. Показание пока не отправлено.' : e.message,
          () => send(extra));
      } finally {
        unBusy(sendBtn);
      }
    }

    drawForm();
    setScreen('Подать показания', chips, formBox, recBox, errBox, file, photoBtn, sendBtn);
  }

  // ---------- экран: результат ----------
  function screenResult(p) {
    const m = p.meter;
    const next = ((me && me.meters) || []).find(x => !x.submitted_this_period && x !== m);
    setScreen('Готово',
      h('div', { class: 'card state ok' }, icon('check', 40),
        h('h2', {}, 'Готово! Записали'),
        h('p', {}, meterTitle(m)),
        h('p', { style: 'font-size:22px;font-weight:650' }, fmtValues(m, p.values)),
        p.status === 'flagged' && h('p', { class: 'note warn' }, 'Прирост больше обычного — мы отметили показание для проверки.'),
        p.stub && h('p', { class: 'muted small' }, 'Значение подставлено демо-распознаванием.'),
        h('p', { class: 'muted small' }, 'Мы также отправили подтверждение в чат. Передача в управляющую компанию в MVP смоделирована.')),
      next && btn('Подать ещё: ' + next.type_label, () => go('submit', { meterId: next.id }, true)),
      btn('На главную', toHome, next ? 'sec2' : ''));
  }

  // ---------- экран: счётчик ----------
  function screenMeter(p) {
    load('Счётчик', () => api('/api/meters/' + encodeURIComponent(p.id)), d => drawMeter(d.meter ? Object.assign({}, d.meter, { history: d.history }) : d));
  }
  function kv(k, v) { return h('div', { class: 'kv' }, h('span', {}, k), h('span', {}, v)); }
  function drawMeter(m) {
    const hist = (m.history || []).slice(0, 12);
    setScreen('Счётчик',
      h('div', { class: 'card' },
        h('h2', {}, meterTitle(m)),
        kv('Серийный номер', m.serial || 'не указан'),
        kv('Поверка', m.verification_due ? 'до ' + fmtDate(m.verification_due) : 'дата не указана'),
        tariffCount(m) > 1 && kv('Тарифов', String(tariffCount(m)))),
      !m.verification_due && h('p', { class: 'muted small', style: 'margin:-4px 4px 12px' },
        'Дату поверки можно указать в чате, в разделе «Мои счётчики».'),
      btn('Подать показания', () => go('submit', { meterId: m.id })),
      h('div', { class: 'sec' }, 'История'),
      hist.length
        ? h('div', { class: 'list' }, hist.map(r => h('div', { class: 'item' },
          h('div', { class: 'grow' },
            h('div', { class: 't' }, fmtPeriod(r.period)),
            h('div', {}, fmtValues(m, r.values)),
            h('div', { class: 'muted small' }, [fmtDay(r.created_at), SOURCES[r.source]].filter(Boolean).join(' · '))),
          r.status === 'flagged' && h('span', { class: 'pill no' }, 'большой прирост'))))
        : h('div', { class: 'card muted' }, 'Показаний пока нет.'));
  }

  const SCREENS = { home: screenHome, submit: screenSubmit, result: screenResult, meter: screenMeter };

  // ---------- старт ----------
  if (!INIT && !DEV_USER) screenOutside();
  else if (!API && /\.github\.io$/.test(location.hostname)) {
    // Статика на GitHub Pages, а адрес бэкенда не задан — не ходим на github.io/api.
    setScreen('ЖКХ', h('div', { class: 'card state bad' }, icon('alert', 36),
      h('h2', {}, 'Сервис временно недоступен'),
      h('p', { class: 'muted' }, 'Мы уже чиним. Попробуйте открыть мини-приложение чуть позже.'),
      btn('Повторить', () => location.reload())));
  } else toHome();
})();
