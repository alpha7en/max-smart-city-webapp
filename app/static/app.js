/**
 * MAX Smart City Housing Mini-App Client.
 * High-precision De-AI Industrial architecture interfacing with MAX Bridge and backend API.
 * Supports all functional domains:
 * 1. Meters (Water, Electricity T1/T2, Heat, Gas) with tactile mechanical odometer
 * 2. Arshin Anti-Fraud Green Security Shield (102-FZ)
 * 3. GOST R 56042-2014 Billing & Account 40821 SBP Split (103-FZ)
 * 4. Dispatcher Tickets & SLAs (PP RF No. 40) + Guest Tenant Access without ESIA
 * 5. UK Inspector ARM Digital Acts with GPS & SHA-256 (63-FZ)
 */

// Application State
let appState = {
  meters: [],
  selectedMeterId: "meter-khvs-1",
  currentTab: "tab-meters",
  currentFilter: "all",
  activeRole: "resident", // 'resident' or 'inspector_uk'
  torchActive: false,
  billTotal: 4850.50,
  rollerBlackDigits: [0, 0, 1, 4, 2],
  rollerRedDigits: [7, 8, 9],
  previousReading: 138.0,
  tariffRate: 54.80,
  inspectorPhotoBase64: null,
  cameraStream: null,
  profile: null
};

// Initialization
document.addEventListener("DOMContentLoaded", () => {
  initMaxBridge();
  checkUrlLaunchParameters();
  initTabs();
  initRoleSwitcher();
  initViewfinderModal();
  initFilterChips();
  initArshinPresets();
  initTicketModal();
  initInspectorArm();
  initProfileAndAddressManagement();
  updateProfileUI(getDefaultProfile());
  loadMeters();
  loadTickets();
  loadProfile();
  bindGeneralEventHandlers();
});

// ----------------- Check URL Parameters for Role -----------------

function checkUrlLaunchParameters() {
  try {
    const urlParams = new URLSearchParams(window.location.search);
    const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ''));
    const roleParam = urlParams.get('role') || urlParams.get('mode') || urlParams.get('startapp') ||
                      hashParams.get('role') || hashParams.get('mode') || hashParams.get('startapp');
    
    if (roleParam === 'inspector' || roleParam === 'inspector_uk') {
      setRole("inspector_uk");
    } else {
      setRole("resident");
    }
  } catch (e) {
    console.warn("URL parameter parse notice:", e);
  }
}

// ----------------- MAX Bridge SDK & API Transport Wrapper -----------------

function getInitData() {
  if (window.WebApp && window.WebApp.initData) {
    return window.WebApp.initData;
  }
  const urlParams = new URLSearchParams(window.location.search);
  const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ''));
  return urlParams.get('initData') || urlParams.get('init_data') || hashParams.get('initData') || '';
}

async function apiFetch(url, options = {}) {
  const opts = { ...options };
  opts.headers = { ...opts.headers };
  const initData = getInitData();
  if (initData) {
    opts.headers["X-Init-Data"] = initData;
  }
  if (typeof window !== "undefined" && window.API_BASE && url.startsWith("/")) {
    url = window.API_BASE.replace(/\/$/, "") + url;
  }
  return fetch(url, opts);
}

function initMaxBridge() {
  const statusEl = document.getElementById("bridge-status");
  if (window.WebApp) {
    if (statusEl) {
      statusEl.textContent = "MAX Bridge";
      statusEl.className = "badge-status online";
      statusEl.style.display = "inline-flex";
    }

    if (window.WebApp.initDataUnsafe && window.WebApp.initDataUnsafe.user) {
      const u = window.WebApp.initDataUnsafe.user;
      const fullName = [u.first_name, u.last_name].filter(Boolean).join(" ") || u.username || "Иван Иванов";
      const roleTitle = document.getElementById("role-title-text");
      if (roleTitle && appState.activeRole !== "inspector_uk") {
        roleTitle.textContent = fullName;
      }
      const userEl = document.getElementById("role-badge");
      if (userEl) userEl.title = `Авторизован: ${fullName} (ID: ${u.id})`;
    }

    if (window.WebApp.BackButton) {
      window.WebApp.BackButton.onClick(() => {
        if (appState.activeRole === "inspector_uk") {
          switchTab("tab-inspector");
        } else {
          switchTab("tab-meters");
        }
        window.WebApp.BackButton.hide();
      });
    }

    if (typeof window.WebApp.enableClosingConfirmation === "function") {
      try { window.WebApp.enableClosingConfirmation(); } catch (e) {}
    }

    if (typeof window.WebApp.ready === "function") {
      try { window.WebApp.ready(); } catch (e) {}
    }
    if (typeof window.WebApp.expand === "function") {
      try { window.WebApp.expand(); } catch (e) {}
    }

    if (window.WebApp.colorScheme === "light") {
      document.body.classList.add("light-theme");
    }
  } else {
    if (statusEl) {
      statusEl.textContent = "Web Browser";
      statusEl.className = "badge-status";
    }
  }
}

function haptic(type = "light") {
  if (window.WebApp && window.WebApp.HapticFeedback) {
    if (type === "success" || type === "error" || type === "warning") {
      window.WebApp.HapticFeedback.notificationOccurred(type);
    } else if (type === "selection") {
      window.WebApp.HapticFeedback.selectionChanged();
    } else {
      window.WebApp.HapticFeedback.impactOccurred(type);
    }
  }
}

let toastTimeoutId = null;
function showToast(message) {
  const t = document.getElementById("toast");
  if (!t) return;
  t.textContent = message;
  t.classList.add("show");
  if (toastTimeoutId) {
    clearTimeout(toastTimeoutId);
  }
  toastTimeoutId = setTimeout(() => {
    t.classList.remove("show");
    toastTimeoutId = null;
  }, 2800);
}

// ----------------- Role Switcher & Isolation -----------------

function setRole(newRole) {
  appState.activeRole = newRole;
  document.body.setAttribute("data-role", newRole === "inspector_uk" ? "inspector" : "resident");

  const badge = document.getElementById("role-badge");
  const roleTitle = document.getElementById("role-title-text");

  if (newRole === "inspector_uk") {
    if (badge) badge.textContent = "ОБХОДЧИК";
    if (roleTitle) roleTitle.textContent = "Контролер УК";
    switchTab("tab-inspector");
  } else {
    // 1. Profile Name (first field)
    let displayName = "Иван Иванов";
    if (appState.profile) {
      displayName = [appState.profile.first_name, appState.profile.last_name].filter(Boolean).join(" ") || appState.profile.first_name || "Иван Иванов";
    } else if (window.WebApp && window.WebApp.initDataUnsafe && window.WebApp.initDataUnsafe.user) {
      const u = window.WebApp.initDataUnsafe.user;
      displayName = [u.first_name, u.last_name].filter(Boolean).join(" ") || u.username || "Иван Иванов";
    }
    if (roleTitle) roleTitle.textContent = displayName;

    // 2. Status Badge (second field: СОБСТВЕННИК or АРЕНДАТОР)
    let statusText = "СОБСТВЕННИК";
    if (appState.profile && appState.profile.properties) {
      const activeProp = appState.profile.properties.find(p => p.is_active) || appState.profile.properties[0];
      if (activeProp && (activeProp.role === "tenant" || activeProp.role === "guest")) {
        statusText = "АРЕНДАТОР";
      }
    }
    if (badge) badge.textContent = statusText;

    if (appState.currentTab === "tab-inspector") {
      switchTab("tab-meters");
    }
  }
}

function initRoleSwitcher() {
  const badge = document.getElementById("role-badge");
  if (badge) {
    badge.addEventListener("click", () => {
      haptic("medium");
      if (appState.activeRole === "resident") {
        setRole("inspector_uk");
        showToast("Режим: АРМ Обходчика УК");
      } else {
        setRole("resident");
        showToast("Режим: Личный кабинет");
      }
    });
  }

  const switchBackBtn = document.getElementById("btn-switch-to-resident");
  if (switchBackBtn) {
    switchBackBtn.addEventListener("click", () => {
      haptic("medium");
      setRole("resident");
      showToast("Возврат в личный кабинет");
    });
  }
}

// ----------------- Tab Navigation -----------------

function initTabs() {
  const buttons = document.querySelectorAll(".tab-btn");
  buttons.forEach(btn => {
    btn.addEventListener("click", () => {
      const tabId = btn.getAttribute("data-tab");
      switchTab(tabId);
      haptic("light");
    });
  });
}

function switchTab(tabId) {
  appState.currentTab = tabId;
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.getAttribute("data-tab") === tabId));
  document.querySelectorAll(".tab-content").forEach(c => c.classList.toggle("active", c.id === tabId));

  if (window.WebApp && window.WebApp.BackButton) {
    if (tabId === "tab-meters") {
      window.WebApp.BackButton.hide();
    } else {
      window.WebApp.BackButton.show();
    }
  }
}

// ----------------- Filter Chips for Meters -----------------

function initFilterChips() {
  const chips = document.querySelectorAll(".meter-filter-chips .chip");
  chips.forEach(chip => {
    chip.addEventListener("click", () => {
      chips.forEach(c => c.classList.remove("active"));
      chip.classList.add("active");
      appState.currentFilter = chip.getAttribute("data-filter");
      haptic("light");
      renderMetersList(appState.meters);
    });
  });
}

// ----------------- Default Demo Fixtures & Data Loading -----------------

function getDefaultProfile() {
  let name = "Иван";
  let lastName = "Иванов";
  let username = "ivan_ivanov";
  if (window.WebApp && window.WebApp.initDataUnsafe && window.WebApp.initDataUnsafe.user) {
    const u = window.WebApp.initDataUnsafe.user;
    if (u.first_name) name = u.first_name;
    if (u.last_name) lastName = u.last_name;
    if (u.username) username = u.username;
  }
  return {
    user_id: 100456,
    first_name: name,
    last_name: lastName,
    username: username,
    phone: "+7 (999) 123-45-67",
    is_verified: true,
    active_property_id: "prop-flat-42-15",
    properties: [
      {
        id: "prop-flat-42-15",
        address: "г. Москва, ул. Ленина, д. 42, кв. 15",
        els: "1004567890",
        management_company: "ООО УК Столица-Сервис",
        is_active: true,
        role: "owner"
      },
      {
        id: "prop-dacha-8",
        address: "Московская обл., д. Барвиха, д. 8",
        els: "2008891024",
        management_company: "ТСЖ Рублево-Сервис",
        is_active: false,
        role: "tenant"
      }
    ]
  };
}

function getDefaultDemoMeters() {
  return [
    {
      id: "meter-khvs-1",
      meter_type: "cold_water",
      serial_number: "2809142",
      name: "ХВС (Холодная вода)",
      installation_place: "Санузел",
      last_reading_value: 142.0,
      last_reading_date: "2026-08-20",
      verification_date_valid_until: "2029-10-18",
      unit: "м³",
      decimal_digits: 3
    },
    {
      id: "meter-gvs-1",
      meter_type: "hot_water",
      serial_number: "3910844",
      name: "ГВС (Горячая вода)",
      installation_place: "Санузел",
      last_reading_value: 98.0,
      last_reading_date: "2026-08-20",
      verification_date_valid_until: "2028-04-12",
      unit: "м³",
      decimal_digits: 3
    },
    {
      id: "meter-el-1",
      meter_type: "electricity_multi",
      serial_number: "01458291",
      name: "Электроэнергия (Меркурий 208)",
      installation_place: "Щит на площадке",
      last_reading_value: 1840.0,
      last_reading_date: "2026-08-20",
      verification_date_valid_until: "2032-11-05",
      unit: "кВт*ч",
      decimal_digits: 1
    },
    {
      id: "meter-heat-1",
      meter_type: "heat",
      serial_number: "7741209",
      name: "Отопление (Теплосчетчик)",
      installation_place: "Коридор",
      last_reading_value: 14.2,
      last_reading_date: "2026-08-20",
      verification_date_valid_until: "2027-09-30",
      unit: "Гкал",
      decimal_digits: 2
    },
    {
      id: "meter-gas-1",
      meter_type: "gas",
      serial_number: "5540912",
      name: "Газоснабжение (ВК-G4)",
      installation_place: "Кухня",
      last_reading_value: 340.0,
      last_reading_date: "2026-08-20",
      verification_date_valid_until: "2030-06-15",
      unit: "м³",
      decimal_digits: 3
    }
  ];
}

function getDefaultDemoTickets() {
  return [
    {
      id: "TCK-8821",
      category: "Сантехника / Водоснабжение",
      description: "Подтекает вводной кран ХВС в санузле",
      priority: "urgent",
      status: "in_progress",
      assigned_master: "Сергеев В. А.",
      sla_hours: 24,
      created_at: "2026-09-21T09:30:00"
    },
    {
      id: "TCK-8819",
      category: "Электрика",
      description: "Замена автоматического выключателя в щитке",
      priority: "planned",
      status: "new",
      assigned_master: "Дежурный электрик",
      sla_hours: 72,
      created_at: "2026-09-20T14:15:00"
    }
  ];
}

async function loadMeters() {
  try {
    const res = await apiFetch("/api/meters");
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data) && data.length > 0) {
        appState.meters = data;
        renderMetersList(data);
        return;
      }
    }
  } catch (err) {
    console.warn("loadMeters notice:", err);
  }
  appState.meters = getDefaultDemoMeters();
  renderMetersList(appState.meters);
}

function renderMetersList(meters) {
  const container = document.getElementById("meters-container");
  if (!container) return;
  container.innerHTML = "";

  const filtered = meters.filter(m => {
    if (appState.currentFilter === "all") return true;
    return m.meter_type === appState.currentFilter;
  });

  if (filtered.length === 0) {
    container.innerHTML = `<div class="card-surface text-muted" style="text-align:center; padding: 20px;">Нет приборов учета выбранного типа</div>`;
    return;
  }

  filtered.forEach(m => {
    const card = document.createElement("div");
    card.className = "meter-card";
    card.innerHTML = `
      <div class="meter-info-col">
        <h4>${m.name}</h4>
        <div class="meter-sub-info font-mono">№ ${m.serial_number} • ${m.installation_place}</div>
        <div class="meter-sub-info text-success font-mono">Поверка до: ${m.verification_date_valid_until}</div>
      </div>
      <div class="meter-reading-col">
        <span class="meter-val-num font-mono">${m.last_reading_value}</span>
        <span class="text-muted text-xs font-mono">${m.unit}</span>
      </div>
    `;
    card.addEventListener("click", () => {
      appState.selectedMeterId = m.id;
      openCameraViewfinder(m);
    });
    container.appendChild(card);
  });
}

async function loadTickets() {
  try {
    const res = await apiFetch("/api/tickets");
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data) && data.length > 0) {
        renderTicketsList(data);
        return;
      }
    }
  } catch (err) {
    console.warn("loadTickets notice:", err);
  }
  renderTicketsList(getDefaultDemoTickets());
}

function renderTicketsList(tickets) {
  const container = document.getElementById("tickets-container");
  if (!container) return;
  container.innerHTML = "";
  tickets.forEach(t => {
    const item = document.createElement("div");
    item.className = "card-surface";
    const prioColor = t.priority === "emergency" ? "text-danger" : (t.priority === "urgent" ? "text-warning" : "text-accent");
    const prioLabel = t.priority === "emergency" ? "Аварийная (30 мин)" : (t.priority === "urgent" ? "Срочная (24 ч)" : "Плановая");
    
    let statusText = "Новая";
    if (t.status === "in_progress") { statusText = "В работе у мастера"; }
    else if (t.status === "resolved") { statusText = "Выполнена"; }

    item.innerHTML = `
      <div class="section-title-wrap">
        <h4 style="color:#fff; font-size:13px;">${t.category} (${t.id})</h4>
        <span class="${prioColor} text-xs font-medium font-mono">${prioLabel}</span>
      </div>
      <p class="text-xs" style="margin: 8px 0; color:var(--text-secondary);">${t.description}</p>
      <div class="text-xs text-muted font-mono">Статус: ${statusText} • Мастер: ${t.assigned_master || "Дежурный слесарь"}</div>
      <div class="text-xs text-muted font-mono">Регламент SLA: ${t.sla_hours} ч по ПП РФ № 40</div>
      <div style="margin-top: 10px;">
        ${t.status !== "resolved" ? `<button type="button" class="btn btn-secondary btn-sm btn-advance-ticket" data-id="${t.id}"><span>Обновить статус</span></button>` : `<span class="text-xs text-success">Заявка закрыта</span>`}
      </div>
    `;

    const advanceBtn = item.querySelector(".btn-advance-ticket");
    if (advanceBtn) {
      advanceBtn.addEventListener("click", async () => {
        haptic("medium");
        const nextStatus = t.status === "new" ? "in_progress" : "resolved";
        try {
          await apiFetch(`/api/tickets/${t.id}/status`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              status: nextStatus,
              comment: nextStatus === "in_progress" ? "Мастер принял заявку в работу" : "Работы завершены, претензий нет"
            })
          });
          showToast(`Статус заявки ${t.id} обновлен!`);
          loadTickets();
        } catch (e) {
          showToast("Ошибка обновления статуса заявки");
        }
      });
    }

    container.appendChild(item);
  });
}

// ----------------- Viewfinder Camera Simulation -----------------

function initViewfinderModal() {
  const modal = document.getElementById("camera-modal");
  const btnOpen = document.getElementById("btn-open-camera");
  const btnClose = document.getElementById("btn-close-viewfinder");
  const btnTorch = document.getElementById("btn-toggle-torch");
  const btnCapture = document.getElementById("btn-capture-frame");
  const btnCycle = document.getElementById("btn-cycle-meter-type");

  if (btnOpen) {
    btnOpen.addEventListener("click", () => {
      openCameraViewfinder();
    });
  }

  if (btnClose) {
    btnClose.addEventListener("click", () => {
      haptic("light");
      modal.style.display = "none";
    });
  }

  if (btnTorch) {
    btnTorch.addEventListener("click", () => {
      appState.torchActive = !appState.torchActive;
      btnTorch.classList.toggle("active", appState.torchActive);
      const frame = document.getElementById("viewfinder-camera-frame");
      if (frame) frame.classList.toggle("torch-active", appState.torchActive);
      haptic("light");
      showToast(appState.torchActive ? "Подсветка включена" : "Подсветка выключена");

      if (appState.cameraStream) {
        const track = appState.cameraStream.getVideoTracks()[0];
        if (track && typeof track.applyConstraints === "function") {
          track.applyConstraints({ advanced: [{ torch: appState.torchActive }] }).catch(() => {});
        }
      }
    });
  }

  if (btnCycle) {
    btnCycle.addEventListener("click", () => {
      haptic("light");
      const types = ["meter-khvs-1", "meter-gvs-1", "meter-el-1", "meter-heat-1"];
      const idx = (types.indexOf(appState.selectedMeterId) + 1) % types.length;
      appState.selectedMeterId = types[idx];
      const curMeter = appState.meters.find(m => m.id === appState.selectedMeterId);
      if (curMeter) updateViewfinderDisplay(curMeter);
    });
  }

  if (btnCapture) {
    btnCapture.addEventListener("click", () => {
      haptic("success");
      modal.style.display = "none";
      showToast("Кадр зафиксирован. Запуск распознавания...");
      triggerAiScanMock();
    });
  }
}

function openCameraViewfinder(meter = null) {
  haptic("medium");
  const modal = document.getElementById("camera-modal");
  if (modal) modal.style.display = "flex";

  if (meter) {
    appState.selectedMeterId = meter.id;
    updateViewfinderDisplay(meter);
  } else {
    const cur = appState.meters.find(m => m.id === appState.selectedMeterId) || appState.meters[0];
    if (cur) updateViewfinderDisplay(cur);
  }
}

function updateViewfinderDisplay(meter) {
  const dial = document.getElementById("viewfinder-dial-preview");
  if (dial && meter) {
    const intPart = Math.floor(meter.last_reading_value);
    const intStr = String(intPart).padStart(5, '0').slice(-5);
    const fracStr = "789";
    dial.innerHTML = `
      <div class="sim-counter-box font-mono">
        <span class="sim-black">${intStr}</span><span class="sim-red">${fracStr}</span>
      </div>
      <span class="sim-serial font-mono">№ ${meter.serial_number}</span>
    `;
  }
}

// ----------------- Mock AI Vision Simulation -----------------

async function triggerAiScanMock() {
  const preview = document.getElementById("ai-scan-result");
  if (preview) preview.style.display = "block";

  const targetMeter = appState.meters.find(m => m.id === appState.selectedMeterId) || appState.meters[0];
  const typeHint = targetMeter ? targetMeter.meter_type : "cold_water";

  try {
    const res = await apiFetch("/api/ai/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_type_hint: typeHint })
    });
    const data = await res.json();

    const titleEl = document.getElementById("ocr-device-title");
    if (titleEl && targetMeter) {
      titleEl.textContent = targetMeter.name;
    }

    const serialEl = document.getElementById("ocr-serial-val");
    if (serialEl) serialEl.textContent = `№ ${data.recognized_serial_number}`;

    const readingEl = document.getElementById("ocr-reading-val");
    if (readingEl) readingEl.textContent = `${data.recognized_reading} ${data.unit || "м³"}`;

    const confEl = document.getElementById("ocr-confidence-val");
    if (confEl) confEl.textContent = `${Math.round(data.confidence * 100)}%`;

    const singleBox = document.getElementById("single-reading-box");
    const multiBox = document.getElementById("multi-tariff-box");
    const dialCaptionBox = document.getElementById("dial-caption-box");

    if (data.device_type === "electricity_multi") {
      if (singleBox) singleBox.style.display = "none";
      if (multiBox) multiBox.style.display = "block";
      const t1El = document.getElementById("input-t1-val");
      const t2El = document.getElementById("input-t2-val");
      if (t1El) t1El.value = data.recognized_reading;
      if (t2El) t2El.value = (data.recognized_reading * 0.42).toFixed(1);
      if (dialCaptionBox) {
        dialCaptionBox.innerHTML = `
          <span class="caption-black">Многотарифный учет Т1 (День) / Т2 (Ночь)</span>
        `;
      }
      const shieldEl = document.getElementById("ocr-shield-active");
      if (shieldEl) shieldEl.textContent = "Активирована (Т1/Т2)";
    } else {
      if (multiBox) multiBox.style.display = "none";
      if (singleBox) singleBox.style.display = "flex";
      const fracVal = Math.round((data.raw_reading_with_fractions - Math.floor(data.recognized_reading)) * 1000) || 789;
      renderRollers(data.recognized_reading, fracVal);
      const manualInput = document.getElementById("input-reading-correct");
      if (manualInput) manualInput.value = data.recognized_reading;
      if (dialCaptionBox) {
        dialCaptionBox.innerHTML = `
          <span class="caption-black">Черные барабаны: целые м³</span>
          <span class="caption-red">Красные барабаны: литры (отсечены)</span>
        `;
      }
      const shieldEl = document.getElementById("ocr-shield-active");
      if (shieldEl) shieldEl.textContent = "Активирована (1000x блок)";
    }

    if (preview) preview.scrollIntoView({ behavior: "smooth" });
    showToast("Цифры и заводской номер считаны!");
  } catch (err) {
    console.error("AI scan failed:", err);
  }
}

// ----------------- Interactive Split Rollers Component -----------------

function renderRollers(readingValue, fractionValue = 789) {
  const intVal = Math.floor(readingValue);
  const intStr = String(intVal).padStart(5, '0').slice(-5);
  const fracStr = String(Math.floor(fractionValue)).padStart(3, '0').slice(-3);

  appState.rollerBlackDigits = intStr.split('').map(Number);
  appState.rollerRedDigits = fracStr.split('').map(Number);

  buildRollerDrums();
  updateCalculationBreakdown();
}

function buildRollerDrums() {
  const blackGroup = document.getElementById("roller-black-group");
  const redGroup = document.getElementById("roller-red-group");
  if (!blackGroup || !redGroup) return;

  blackGroup.innerHTML = "";
  redGroup.innerHTML = "";

  appState.rollerBlackDigits.forEach((digit, idx) => {
    blackGroup.appendChild(createRollerDrum(digit, 'black', idx));
  });

  appState.rollerRedDigits.forEach((digit, idx) => {
    redGroup.appendChild(createRollerDrum(digit, 'red', idx));
  });

  const blackSpan = document.getElementById("ocr-black-digits");
  const redSpan = document.getElementById("ocr-red-digits");
  if (blackSpan) blackSpan.textContent = appState.rollerBlackDigits.join('');
  if (redSpan) redSpan.textContent = appState.rollerRedDigits.join('');
}

function createRollerDrum(digit, type, index) {
  const drum = document.createElement("div");
  drum.className = "roller-drum";
  drum.title = "Колесико мыши или стрелки";

  const btnUp = document.createElement("button");
  btnUp.type = "button";
  btnUp.className = "roller-stepper roller-stepper-up";
  btnUp.textContent = "▲";
  btnUp.setAttribute("aria-label", `Увеличить ${type} ${index}`);
  btnUp.addEventListener("click", () => stepRollerDigit(type, index, 1));

  const digitEl = document.createElement("div");
  digitEl.className = `roller-digit roller-${type}`;
  digitEl.textContent = digit;
  digitEl.addEventListener("click", () => stepRollerDigit(type, index, 1));

  const btnDown = document.createElement("button");
  btnDown.type = "button";
  btnDown.className = "roller-stepper roller-stepper-down";
  btnDown.textContent = "▼";
  btnDown.setAttribute("aria-label", `Уменьшить ${type} ${index}`);
  btnDown.addEventListener("click", () => stepRollerDigit(type, index, -1));

  drum.addEventListener("wheel", (e) => {
    e.preventDefault();
    const delta = e.deltaY < 0 ? 1 : -1;
    stepRollerDigit(type, index, delta);
  }, { passive: false });

  drum.appendChild(btnUp);
  drum.appendChild(digitEl);
  drum.appendChild(btnDown);
  return drum;
}

function stepRollerDigit(type, index, delta) {
  haptic("light");
  if (type === 'black') {
    appState.rollerBlackDigits[index] = (appState.rollerBlackDigits[index] + delta + 10) % 10;
  } else {
    appState.rollerRedDigits[index] = (appState.rollerRedDigits[index] + delta + 10) % 10;
  }

  buildRollerDrums();

  const intPart = parseInt(appState.rollerBlackDigits.join(''), 10);
  const fracPart = parseInt(appState.rollerRedDigits.join(''), 10) / 1000;
  const fullValue = intPart + fracPart;

  const readingInput = document.getElementById("input-reading-correct");
  if (readingInput) readingInput.value = fullValue.toFixed(1);

  const ocrVal = document.getElementById("ocr-reading-val");
  if (ocrVal) {
    const meter = appState.meters.find(m => m.id === appState.selectedMeterId);
    ocrVal.textContent = `${fullValue.toFixed(1)} ${meter ? meter.unit : 'м³'}`;
  }

  updateCalculationBreakdown();
}

function updateCalculationBreakdown() {
  const intPart = parseInt(appState.rollerBlackDigits.join(''), 10);
  const prev = appState.previousReading || 138.0;
  const delta = Math.max(0, intPart - prev);
  const cost = delta * appState.tariffRate;

  const prevEl = document.getElementById("calc-prev-val");
  const deltaEl = document.getElementById("calc-delta-val");
  const costEl = document.getElementById("calc-cost-val");

  if (prevEl) prevEl.textContent = `${prev.toFixed(3)} м³`;
  if (deltaEl) deltaEl.textContent = `+${delta.toFixed(3)} м³`;
  if (costEl) costEl.textContent = `${cost.toFixed(2)} ₽`;
}

// ----------------- Arshin Presets & Anti-Fraud -----------------

function initArshinPresets() {
  document.querySelectorAll(".preset-arshin").forEach(btn => {
    btn.addEventListener("click", () => {
      const serial = btn.getAttribute("data-serial");
      const input = document.getElementById("input-arshin-serial");
      if (input) input.value = serial;
      haptic("light");
      performArshinCheck(serial);
    });
  });
}

async function performArshinCheck(serial) {
  if (!serial) return;
  try {
    const res = await apiFetch(`/api/arshin/check?serial=${encodeURIComponent(serial)}`);
    const data = await res.json();

    const card = document.getElementById("arshin-result-card");
    if (!card) return;
    card.style.display = "block";
    card.classList.remove("shield-verified", "shield-danger", "shield-expired");

    const shieldIcon = document.getElementById("shield-icon");
    const shieldTitle = document.getElementById("shield-title");

    if (data.shield_color === "green" && data.status === "verified") {
      card.classList.add("shield-verified");
      if (shieldIcon) shieldIcon.textContent = "●";
      if (shieldTitle) {
        shieldTitle.textContent = "Поверка действительна (Зеленый Щит)";
        shieldTitle.style.color = "var(--max-success)";
      }
    } else if (data.status === "unregistered") {
      card.classList.add("shield-danger");
      if (shieldIcon) shieldIcon.textContent = "■";
      if (shieldTitle) {
        shieldTitle.textContent = "ПОДДЕЛКА: Прибор не найден в реестре АРШИН!";
        shieldTitle.style.color = "var(--max-danger)";
      }
    } else {
      card.classList.add("shield-expired", "shield-danger");
      if (shieldIcon) shieldIcon.textContent = "▲";
      if (shieldTitle) {
        shieldTitle.textContent = "Срок поверки ИСТЕК — Антифрод-защита активирована!";
        shieldTitle.style.color = "var(--max-danger)";
      }
    }

    const orgEl = document.getElementById("shield-org");
    if (orgEl) orgEl.textContent = `Организация: ${data.organization_name} • Срок: до ${data.valid_until}`;
    
    let msgHtml = data.safety_message.replace(/\n\n/g, '<br/><br/>');
    if (data.is_fraud_warning && data.status === "expired") {
      msgHtml += `
        <div class="shield-fraud-alert">
          <strong>АНТИФРОД-ПРЕДУПРЕЖДЕНИЕ:</strong> Остерегайтесь мошенников! Листовки с угрозами штрафов 50 000 ₽ или навязанной срочной заменой счетчика за 10 000–15 000 ₽ — это обман. Поверка выполняется без снятия пломб через вашу УК по закону № 102-ФЗ.
        </div>
      `;
    }
    const msgEl = document.getElementById("shield-message");
    if (msgEl) msgEl.innerHTML = msgHtml;

    const linkEl = document.getElementById("shield-link");
    if (linkEl) linkEl.href = data.fgis_arshin_url;

    card.scrollIntoView({ behavior: "smooth" });
    showToast("Сверка со ФГИС АРШИН выполнена");
  } catch (err) {
    showToast("Ошибка связи с реестром АРШИН");
  }
}

// ----------------- Tickets Modal & Submission -----------------

function initTicketModal() {
  const modal = document.getElementById("ticket-modal");
  const btnOpen = document.getElementById("btn-show-ticket-modal");
  const btnClose = document.getElementById("btn-close-ticket-modal");
  const btnSubmit = document.getElementById("btn-submit-create-ticket");

  if (btnOpen) {
    btnOpen.addEventListener("click", () => {
      haptic("light");
      modal.style.display = "flex";
    });
  }

  if (btnClose) {
    btnClose.addEventListener("click", () => {
      modal.style.display = "none";
    });
  }

  if (btnSubmit) {
    btnSubmit.addEventListener("click", async () => {
      const cat = document.getElementById("ticket-category-select").value;
      const prio = document.getElementById("ticket-priority-select").value;
      const desc = document.getElementById("ticket-desc-input").value.trim() || "Проверка оборудования";

      try {
        const res = await apiFetch("/api/tickets", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            category: cat,
            priority: prio,
            description: desc,
            address: "ул. Ленина, д. 42, кв. 15"
          })
        });
        const data = await res.json();
        modal.style.display = "none";
        haptic("success");
        showToast(`Заявка ${data.id} создана! Срок регламента: ${data.sla_hours} ч`);
        loadTickets();
      } catch (e) {
        showToast("Ошибка создания заявки");
      }
    });
  }
}

// ----------------- Inspector ARM (АРМ Обходчика УК) -----------------

const INSPECTOR_METERS = {
  "meter-khvs-1": { serial: "2809142", reading: 142.385, name: "ХВС (ИТЭЛМА № 2809142)" },
  "meter-gvs-1": { serial: "3910844", reading: 89.140, name: "ГВС (Бетар № 3910844)" },
  "meter-el-1": { serial: "01458291", reading: 2360.500, name: "Электроэнергия (Меркурий 208 № 01458291)" },
  "meter-heat-1": { serial: "7741209", reading: 12.840, name: "Отопление (Sayany № 7741209)" }
};

async function computeSha256(str) {
  if (window.crypto && window.crypto.subtle) {
    try {
      const buffer = new TextEncoder().encode(str);
      const hash = await window.crypto.subtle.digest("SHA-256", buffer);
      return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, '0')).join('');
    } catch (e) {}
  }
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash) + str.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash).toString(16).padStart(8, '0').repeat(8).slice(0, 64);
}

async function updateInspectorHashPreview() {
  const name = document.getElementById("inspector-name-input")?.value || "";
  const addr = document.getElementById("inspector-address-select")?.value || "";
  const meter = document.getElementById("inspector-meter-select")?.value || "";
  const val = document.getElementById("inspector-reading-input")?.value || "";
  const gps = document.getElementById("inspector-gps-input")?.value || "";
  const photo = appState.inspectorPhotoBase64 || "";
  const payload = `${name}|${addr}|${meter}|${val}|${gps}|${photo ? photo.substring(0, 60) : 'no_photo'}`;
  const hash = await computeSha256(payload);
  const el = document.getElementById("inspector-hash-preview");
  if (el) el.textContent = hash;
}

function initInspectorArm() {
  const meterSelect = document.getElementById("inspector-meter-select");
  const serialInput = document.getElementById("inspector-serial-input");
  const readingInput = document.getElementById("inspector-reading-input");
  const photoInput = document.getElementById("inspector-photo-input");
  const btnPhoto = document.getElementById("btn-inspector-photo");
  const btnSample = document.getElementById("btn-inspector-sample-photo");
  const btnRemovePhoto = document.getElementById("btn-remove-inspector-photo");

  if (meterSelect) {
    meterSelect.addEventListener("change", () => {
      const m = INSPECTOR_METERS[meterSelect.value];
      if (m) {
        if (serialInput) serialInput.value = m.serial;
        if (readingInput) readingInput.value = m.reading;
      }
      updateInspectorHashPreview();
    });
  }

  if (readingInput) readingInput.addEventListener("input", updateInspectorHashPreview);

  if (btnPhoto && photoInput) {
    btnPhoto.addEventListener("click", () => photoInput.click());
    photoInput.addEventListener("change", () => {
      if (photoInput.files && photoInput.files[0]) {
        const reader = new FileReader();
        reader.onload = (e) => {
          appState.inspectorPhotoBase64 = e.target.result;
          const previewImg = document.getElementById("inspector-photo-preview");
          const previewWrap = document.getElementById("inspector-photo-preview-wrap");
          if (previewImg) previewImg.src = e.target.result;
          if (previewWrap) previewWrap.style.display = "block";
          updateInspectorHashPreview();
          showToast("Снимок зафиксирован");
        };
        reader.readAsDataURL(photoInput.files[0]);
      }
    });
  }

  if (btnSample) {
    btnSample.addEventListener("click", () => {
      appState.inspectorPhotoBase64 = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMDAiIGhlaWdodD0iMjAwIj48cmVjdCB3aWR0aD0iMTAwJSIgaGVpZ2h0PSIxMDAlIiBmaWxsPSIjMWUyOTNiIi8+PHRleHQgeD0iNTAiIHk9IjEwMCIgZmlsbD0iI2ZmZiIgZm9udC1zaXplPSIyMCI+TUVRVFlSIDI4MDkxNDI8L3RleHQ+PC9zdmc+";
      const previewImg = document.getElementById("inspector-photo-preview");
      const previewWrap = document.getElementById("inspector-photo-preview-wrap");
      if (previewImg) previewImg.src = appState.inspectorPhotoBase64;
      if (previewWrap) previewWrap.style.display = "block";
      updateInspectorHashPreview();
      showToast("Эталонное фото прикреплено");
    });
  }

  if (btnRemovePhoto) {
    btnRemovePhoto.addEventListener("click", () => {
      appState.inspectorPhotoBase64 = null;
      const previewImg = document.getElementById("inspector-photo-preview");
      const previewWrap = document.getElementById("inspector-photo-preview-wrap");
      if (previewImg) previewImg.src = "";
      if (previewWrap) previewWrap.style.display = "none";
      updateInspectorHashPreview();
    });
  }

  const btnGen = document.getElementById("btn-generate-inspector-act");
  if (!btnGen) return;

  btnGen.addEventListener("click", async () => {
    haptic("success");
    const name = document.getElementById("inspector-name-input").value;
    const addr = document.getElementById("inspector-address-select").value;
    const meter = document.getElementById("inspector-meter-select").value;
    const val = parseFloat(document.getElementById("inspector-reading-input").value);
    const gps = document.getElementById("inspector-gps-input").value;

    try {
      const res = await apiFetch("/api/uk/inspector-act", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meter_id: meter,
          reading_value: val,
          address: addr,
          inspector_name: name,
          gps_coordinates: gps,
          photo_base64: appState.inspectorPhotoBase64
        })
      });
      const data = await res.json();

      const card = document.getElementById("inspector-act-result");
      if (!card) return;
      card.style.display = "block";
      document.getElementById("act-number").textContent = `№ ${data.act_id || data.act_number}`;
      document.getElementById("act-inspector").textContent = data.inspector_name;
      document.getElementById("act-address").textContent = data.address;
      document.getElementById("act-reading").textContent = `${data.reading_value || data.meter_reading} м³`;
      document.getElementById("act-gps").textContent = data.gps || data.gps_coordinates;
      document.getElementById("act-timestamp").textContent = data.timestamp;
      document.getElementById("act-hash").textContent = data.photo_hash_sha256 || data.crypto_hash;
      document.getElementById("act-export-status").textContent = data.export_1c_ready ? "EXPORTED_TO_1C_ZHKH" : data.billing_export_status;
      
      const badge1c = document.getElementById("act-export-badge");
      if (badge1c) badge1c.innerHTML = '<span class="material-symbols-outlined text-[13px]">sync</span> 1С:ЖКХ ГОТОВ';

      const meterInfo = document.getElementById("act-meter-info");
      if (meterInfo) {
        const item = INSPECTOR_METERS[meter];
        meterInfo.textContent = item ? item.name : meter;
      }

      const photoWrap = document.getElementById("act-photo-display-wrap");
      const photoImg = document.getElementById("act-photo-display");
      if (appState.inspectorPhotoBase64 && photoWrap && photoImg) {
        photoImg.src = appState.inspectorPhotoBase64;
        photoWrap.style.display = "block";
      }

      card.scrollIntoView({ behavior: "smooth" });
      showToast("Цифровой акт сформирован для 1С:ЖКХ!");
    } catch (e) {
      showToast("Ошибка генерации цифрового акта");
    }
  });
}

// ----------------- General Event Handlers -----------------

function bindGeneralEventHandlers() {
  const fileInput = document.getElementById("file-meter-input");
  const btnUpload = document.getElementById("btn-upload-file");
  if (btnUpload && fileInput) {
    btnUpload.addEventListener("click", () => {
      fileInput.click();
    });
    fileInput.addEventListener("change", () => {
      if (fileInput.files && fileInput.files[0]) {
        showToast("Файл получен. Запуск распознавания...");
        triggerAiScanMock();
      }
    });
  }

  // Stepper buttons for reading input (from max_1)
  const btnMinus = document.getElementById("btn-reading-minus");
  const btnPlus = document.getElementById("btn-reading-plus");
  const readingInput = document.getElementById("input-reading-correct");

  if (btnMinus && readingInput) {
    btnMinus.addEventListener("click", () => {
      let val = parseFloat(readingInput.value) || 0;
      if (val > 1) {
        readingInput.value = (val - 1).toFixed(1);
        const intPart = Math.floor(val - 1);
        const fracPart = Math.round(((val - 1) - intPart) * 1000);
        renderRollers(intPart, fracPart);
      }
    });
  }

  if (btnPlus && readingInput) {
    btnPlus.addEventListener("click", () => {
      let val = parseFloat(readingInput.value) || 0;
      readingInput.value = (val + 1).toFixed(1);
      const intPart = Math.floor(val + 1);
      const fracPart = Math.round(((val + 1) - intPart) * 1000);
      renderRollers(intPart, fracPart);
    });
  }

  if (readingInput) {
    readingInput.addEventListener("input", (e) => {
      const val = parseFloat(e.target.value) || 0;
      const intPart = Math.floor(val);
      const fracPart = Math.round((val - intPart) * 1000);
      renderRollers(intPart, fracPart);
    });
  }

  // Submit Reading to GIS ZHKH
  const btnSubmitReading = document.getElementById("btn-submit-reading");
  if (btnSubmitReading) {
    btnSubmitReading.addEventListener("click", async () => {
      haptic("success");
      const readingVal = parseFloat(document.getElementById("input-reading-correct").value);
      const meter = appState.meters.find(m => m.id === appState.selectedMeterId);
      const isElectric = meter && meter.meter_type.startsWith("el");
      const t2Val = isElectric ? parseFloat(document.getElementById("input-t2-val").value) : null;

      try {
        const res = await apiFetch("/api/meters/submit", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            meter_id: appState.selectedMeterId,
            reading_value: readingVal,
            reading_value_t2: t2Val,
            submission_channel: "max_miniapp"
          })
        });
        const data = await res.json();
        const feedback = document.getElementById("reading-feedback");
        if (feedback) {
          feedback.style.display = "block";
          if (data.is_valid) {
            feedback.className = "feedback-notification-box alert-success";
            feedback.innerHTML = `<b>Успешно!</b> ${data.message} (Расход: +${data.consumption} ${meter ? meter.unit : 'м³'}). Передано в ГИС ЖКХ.`;
            loadMeters();
          } else {
            feedback.className = "feedback-notification-box alert-danger";
            feedback.innerHTML = `<b>Внимание:</b> ${data.message}`;
          }
        }
        showToast("Показания переданы!");
      } catch (err) {
        showToast("Ошибка отправки данных");
      }
    });
  }

  // Arshin manual check
  const btnCheckArshin = document.getElementById("btn-check-arshin");
  if (btnCheckArshin) {
    btnCheckArshin.addEventListener("click", () => {
      const serial = document.getElementById("input-arshin-serial").value.trim();
      performArshinCheck(serial);
    });
  }

  // GOST QR Scanner via MAX Bridge
  const btnScanQr = document.getElementById("btn-scan-qr-bridge");
  if (btnScanQr) {
    btnScanQr.addEventListener("click", async () => {
      haptic("medium");
      const webapp = window.WebApp;
      if (webapp && typeof webapp.openCodeReader === "function") {
        try {
          const res = webapp.openCodeReader({ text: "Наведите камеру на QR квитанции" }, (qrText) => {
            if (qrText) {
              if (typeof webapp.closeCodeReader === "function") webapp.closeCodeReader();
              parseAndDisplayQr(qrText);
            }
          });
          if (res && typeof res.then === "function") {
            res.then(qrText => { if (qrText) parseAndDisplayQr(qrText); }).catch(() => {});
          }
        } catch (err) {
          console.warn("MAX openCodeReader notice:", err);
          pasteSampleGostQr();
        }
      } else {
        pasteSampleGostQr();
      }
    });
  }

  const btnPasteQr = document.getElementById("btn-paste-sample-qr");
  if (btnPasteQr) {
    btnPasteQr.addEventListener("click", () => {
      haptic("light");
      pasteSampleGostQr();
    });
  }

  // SBP Payment
  const btnPaySbp = document.getElementById("btn-pay-sbp");
  if (btnPaySbp) {
    btnPaySbp.addEventListener("click", () => {
      haptic("success");
      openReceiptModal();
    });
  }

  const btnCloseReceipt = document.getElementById("btn-close-receipt");
  if (btnCloseReceipt) {
    btnCloseReceipt.addEventListener("click", () => {
      document.getElementById("receipt-modal").style.display = "none";
    });
  }
  const btnDoneReceipt = document.getElementById("btn-done-receipt");
  if (btnDoneReceipt) {
    btnDoneReceipt.addEventListener("click", () => {
      document.getElementById("receipt-modal").style.display = "none";
    });
  }

  // Guest link generator
  const btnGuest = document.getElementById("btn-generate-guest-link");
  if (btnGuest) {
    btnGuest.addEventListener("click", async () => {
      haptic("light");
      try {
        const res = await apiFetch("/api/guest/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            property_id: "flat-42-15",
            tenant_name: "Арендатор",
            duration_days: 30
          })
        });
        const data = await res.json();
        const box = document.getElementById("guest-link-result");
        if (box) {
          box.style.display = "block";
          box.innerHTML = `
            <b>Временная ссылка в MAX:</b><br/>
            <span style="color:var(--primary);">${data.direct_max_link}</span><br/><br/>
            <i>Срок действия: 30 дней. Авторизация через ЕСИА не требуется. Доступ к передаче показаний и оплате по СБП.</i>
          `;
        }
        if (navigator.clipboard) {
          navigator.clipboard.writeText(data.direct_max_link).catch(() => {});
        }
        showToast("Гостевая ссылка скопирована!");
      } catch (e) {
        showToast("Ошибка генерации ссылки");
      }
    });
  }
}

function pasteSampleGostQr() {
  const sample = "ST00012|Name=ООО УК ДОМОВОЙ СЕРВИС|PersonalAcc=40702810938000012345|BIC=044525225|CorrespAcc=30101810400000000225|PayeeINN=7701234567|Sum=485050|PersAcc=1004567890|Period=092026";
  parseAndDisplayQr(sample);
}

async function parseAndDisplayQr(qrPayload) {
  try {
    const res = await apiFetch("/api/billing/parse-qr", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ qr_payload: qrPayload })
    });
    const data = await res.json();

    appState.billTotal = data.total_amount_rubles;
    document.getElementById("bill-total-amount").textContent = `${data.total_amount_rubles.toFixed(2)} ₽`;
    document.getElementById("bill-payee-name").textContent = data.recipient_name;
    document.getElementById("bill-pers-acc").textContent = data.personal_account;
    document.getElementById("bill-account-40821").textContent = data.payee_account;

    const list = document.getElementById("split-recipients-list");
    if (list) {
      list.innerHTML = "";
      data.split_details.forEach(item => {
        const row = document.createElement("div");
        row.className = "split-row";
        row.innerHTML = `
          <div class="split-info">
            <span class="split-name">${item.recipient_name}</span>
            <span class="split-sub">Спецсчет: ${item.account_40821} • ${item.purpose}</span>
          </div>
          <span class="split-amount font-mono">${item.amount_rubles.toFixed(2)} ₽</span>
        `;
        list.appendChild(row);
      });
    }

    showToast("Квитанция по ГОСТ Р 56042-2014 считана!");
  } catch (err) {
    showToast("Неверный формат QR-кода");
  }
}

function openReceiptModal() {
  const modal = document.getElementById("receipt-modal");
  const body = document.getElementById("receipt-body");
  const now = new Date().toLocaleString("ru-RU");
  const total = appState.billTotal.toFixed(2);

  if (body) {
    body.innerHTML = `
      <div style="text-align:center; margin-bottom: 14px;">
        <div style="display:inline-flex; align-items:center; justify-content:center; width:44px; height:44px; border-radius:50%; background:rgba(16,185,129,0.15); border:1px solid #10B981; margin-bottom:8px;">
          <span class="material-symbols-outlined" style="color:#10B981; font-size:24px;">bolt</span>
        </div>
        <div style="font-family:var(--font-mono); font-size:11px; font-weight:700; letter-spacing:0.05em; color:#10B981; margin-bottom:4px;">СБП 103-ФЗ</div>
        <h4 style="color:#fff; font-size:14px;">Оплата через Систему Быстрых Платежей</h4>
        <div style="font-size: 24px; font-weight: 700; color: #10b981; margin: 6px 0; font-family:var(--font-mono);">${total} ₽</div>
        <span class="badge-success-tag">ИСПОЛНЕНО</span>
      </div>
      <div style="font-size: 12px; line-height: 1.8; border-top: 1px dashed #333; padding-top: 10px; font-family:var(--font-mono); color:var(--text-secondary);">
        <div><b>Дата и время:</b> ${now}</div>
        <div><b>Лицевой счет:</b> 1004567890 (кв. 15)</div>
        <div><b>Транзакция СБП:</b> <code>NSPK-2026-${Math.floor(Math.random()*900000+100000)}</code></div>
        <div><b>Фискальный признак:</b> <code>ФД-90412 / ФП-182904</code></div>
        <div style="margin-top: 8px; color:#fff;"><b>Прямое расщепление по 103-ФЗ:</b></div>
        <div style="color:var(--text-muted); font-size: 11px; padding-left: 8px;">
          • Водоканал (ХВС): ${(appState.billTotal * 0.25).toFixed(2)} ₽<br/>
          • МОЭК (Отопление): ${(appState.billTotal * 0.45).toFixed(2)} ₽<br/>
          • Мосэнергосбыт: ${(appState.billTotal * 0.15).toFixed(2)} ₽<br/>
          • УК (Содержание): ${(appState.billTotal * 0.15).toFixed(2)} ₽
        </div>
      </div>
    `;
  }
  if (modal) modal.style.display = "flex";
  showToast("Чек платежа СБП сформирован");
}

// ----------------- Profile & Address Management -----------------

function initProfileAndAddressManagement() {
  const addressBtn = document.getElementById("header-address-btn");
  const profileBtn = document.getElementById("header-profile-btn");
  const closeProfileBtn = document.getElementById("btn-close-profile-modal");
  const closeProfileBtn2 = document.getElementById("btn-close-profile-modal-btn");
  const openAddPropBtn = document.getElementById("btn-open-add-address-modal");
  const closeAddPropBtn = document.getElementById("btn-close-add-property-modal");
  const submitAddPropBtn = document.getElementById("btn-submit-new-property");
  const autofillQrBtn = document.getElementById("btn-autofill-property-qr");
  const bindPhoneBtn = document.getElementById("btn-bind-phone");

  if (addressBtn) {
    addressBtn.addEventListener("click", () => {
      haptic("light");
      openProfileModal();
    });
  }

  if (profileBtn) {
    profileBtn.addEventListener("click", () => {
      haptic("light");
      openProfileModal();
    });
  }

  if (closeProfileBtn) {
    closeProfileBtn.addEventListener("click", () => {
      document.getElementById("profile-modal").style.display = "none";
    });
  }

  if (closeProfileBtn2) {
    closeProfileBtn2.addEventListener("click", () => {
      document.getElementById("profile-modal").style.display = "none";
    });
  }

  if (openAddPropBtn) {
    openAddPropBtn.addEventListener("click", () => {
      haptic("light");
      document.getElementById("profile-modal").style.display = "none";
      document.getElementById("add-property-modal").style.display = "flex";
    });
  }

  if (closeAddPropBtn) {
    closeAddPropBtn.addEventListener("click", () => {
      document.getElementById("add-property-modal").style.display = "none";
    });
  }

  if (submitAddPropBtn) {
    submitAddPropBtn.addEventListener("click", () => {
      haptic("medium");
      submitNewProperty();
    });
  }

  if (autofillQrBtn) {
    autofillQrBtn.addEventListener("click", () => {
      haptic("light");
      const addrInput = document.getElementById("new-property-address-input");
      const elsInput = document.getElementById("new-property-els-input");
      const mcInput = document.getElementById("new-property-mc-input");
      if (addrInput && !addrInput.value) addrInput.value = "г. Москва, ул. Ленина, д. 42, кв. 15";
      if (elsInput) elsInput.value = "1004567890";
      if (mcInput) mcInput.value = "ООО УК ДОМОВОЙ СЕРВИС";
      showToast("Данные из квитанции подгружены");
    });
  }

  if (bindPhoneBtn) {
    bindPhoneBtn.addEventListener("click", () => {
      bindUserPhone();
    });
  }
}

function openProfileModal() {
  const modal = document.getElementById("profile-modal");
  if (modal) modal.style.display = "flex";
  if (!appState.profile) {
    loadProfile();
  }
}

async function loadProfile() {
  try {
    const res = await apiFetch("/api/profile");
    if (res.ok) {
      const profile = await res.json();
      appState.profile = profile;
      updateProfileUI(profile);
      return;
    }
  } catch (err) {
    console.warn("loadProfile notice:", err);
  }
  if (!appState.profile) {
    const defaultProf = getDefaultProfile();
    appState.profile = defaultProf;
    updateProfileUI(defaultProf);
  }
}

function updateProfileUI(profile) {
  if (!profile) return;
  appState.profile = profile;

  let activeProp = null;
  if (profile.properties && profile.properties.length > 0) {
    activeProp = profile.properties.find(p => p.is_active) || profile.properties[0];
  }

  const headerAddr = document.getElementById("header-active-address-text");
  if (headerAddr && activeProp) {
    headerAddr.textContent = `${activeProp.address} (ЕЛС ${activeProp.els})`;
  }

  const roleTitle = document.getElementById("role-title-text");
  const roleBadge = document.getElementById("role-badge");
  const usernameEl = document.getElementById("profile-modal-username");
  const profileBadgeLabel = document.getElementById("profile-badge-label");

  // First field: Full Name (e.g. "Иван Иванов")
  const fullName = [profile.first_name, profile.last_name].filter(Boolean).join(" ") || profile.first_name || "Иван Иванов";
  if (roleTitle && appState.activeRole !== "inspector_uk") {
    roleTitle.textContent = fullName;
  }

  // Second field: Status badge (СОБСТВЕННИК or АРЕНДАТОР)
  if (roleBadge && appState.activeRole !== "inspector_uk") {
    const isTenant = activeProp && (activeProp.role === "tenant" || activeProp.role === "guest");
    roleBadge.textContent = isTenant ? "АРЕНДАТОР" : "СОБСТВЕННИК";
  }

  if (usernameEl) usernameEl.textContent = fullName;

  // Header action button label: strictly "Профиль", never overwritten!
  if (profileBadgeLabel) profileBadgeLabel.textContent = "Профиль";

  const phoneEl = document.getElementById("profile-modal-phone");
  const phoneActionWrap = document.getElementById("profile-phone-action-wrap");
  if (phoneEl) {
    if (profile.is_verified && profile.phone) {
      phoneEl.textContent = `Телефон: ${profile.phone}`;
      phoneEl.className = "text-success text-xs font-mono";
      if (phoneActionWrap) {
        phoneActionWrap.innerHTML = '<span class="badge-success-tag">ПОДТВЕРЖДЕН</span>';
      }
    } else {
      phoneEl.textContent = "Телефон не привязан";
      phoneEl.className = "text-muted text-xs font-mono";
      if (phoneActionWrap) {
        phoneActionWrap.innerHTML = '<button type="button" class="btn btn-primary btn-sm" id="btn-bind-phone"><span>Привязать тел.</span></button>';
        const newBtn = document.getElementById("btn-bind-phone");
        if (newBtn) {
          newBtn.addEventListener("click", () => bindUserPhone());
        }
      }
    }
  }

  const container = document.getElementById("profile-addresses-container");
  if (!container) return;
  container.innerHTML = "";

  if (profile.properties && profile.properties.length > 0) {
    profile.properties.forEach(p => {
      const item = document.createElement("div");
      item.className = `profile-address-item ${p.is_active ? 'active' : ''}`;

      let actionHtml = '';
      if (p.is_active) {
        actionHtml = '<span class="badge-success-tag font-mono">АКТИВЕН</span>';
      } else {
        actionHtml = `<button type="button" class="btn btn-secondary btn-sm btn-switch-address" data-prop-id="${p.id}"><span>Выбрать</span></button>`;
      }

      const roleLabel = (p.role === "tenant" || p.role === "guest") ? "Арендатор" : "Собственник";

      item.innerHTML = `
        <div class="profile-address-text">
          <div class="profile-address-title">${p.address}</div>
          <div class="profile-address-sub font-mono">ЕЛС: ${p.els} • ${p.management_company} • ${roleLabel}</div>
        </div>
        ${actionHtml}
      `;
      container.appendChild(item);
    });

    container.querySelectorAll(".btn-switch-address").forEach(btn => {
      btn.addEventListener("click", () => {
        const propId = btn.getAttribute("data-prop-id");
        switchActiveProperty(propId);
      });
    });
  }
}

async function switchActiveProperty(propId) {
  haptic("light");
  try {
    const res = await apiFetch("/api/profile/switch-property", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ property_id: propId })
    });
    if (res.ok) {
      const profile = await res.json();
      appState.profile = profile;
      updateProfileUI(profile);
      showToast("Активный адрес переключен!");
      return;
    }
  } catch (err) {
    console.warn("switchActiveProperty server notice:", err);
  }
  // Client-side fallback if server offline or standalone
  if (appState.profile && appState.profile.properties) {
    appState.profile.properties.forEach(p => p.is_active = (p.id === propId));
    appState.profile.active_property_id = propId;
    updateProfileUI(appState.profile);
    showToast("Активный адрес переключен!");
  }
}

async function submitNewProperty() {
  const addrInput = document.getElementById("new-property-address-input");
  const elsInput = document.getElementById("new-property-els-input");
  const mcInput = document.getElementById("new-property-mc-input");

  const address = addrInput ? addrInput.value.trim() : "";
  const els = elsInput ? elsInput.value.trim() : "";
  const mc = mcInput ? mcInput.value.trim() : "ООО УК Столица-Сервис";

  if (!address) {
    showToast("Укажите адрес помещения");
    return;
  }
  if (!els) {
    showToast("Укажите ЕЛС (10 цифр)");
    return;
  }

  try {
    const res = await apiFetch("/api/profile/add-property", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        address: address,
        els: els,
        management_company: mc
      })
    });
    if (res.ok) {
      const profile = await res.json();
      appState.profile = profile;
      updateProfileUI(profile);
      document.getElementById("add-property-modal").style.display = "none";
      document.getElementById("profile-modal").style.display = "flex";
      showToast("Адрес успешно привязан!");
      if (addrInput) addrInput.value = "";
      if (elsInput) elsInput.value = "";
    } else {
      showToast("Ошибка добавления адреса");
    }
  } catch (err) {
    console.error("submitNewProperty error:", err);
    showToast("Ошибка сохранения адреса");
  }
}

async function bindUserPhone() {
  haptic("medium");
  if (window.WebApp && typeof window.WebApp.requestContact === "function") {
    try {
      window.WebApp.requestContact(async (status, result) => {
        if (status && result) {
          await sendPhoneVerification(result.phone, result.hash, result.auth_date || result.authDate);
        } else {
          await executeFallbackPhoneBinding();
        }
      });
      return;
    } catch (e) {
      console.warn("WebApp.requestContact failed:", e);
    }
  }
  await executeFallbackPhoneBinding();
}

async function executeFallbackPhoneBinding() {
  await sendPhoneVerification("+7 (999) 123-45-67");
}

async function sendPhoneVerification(phone, hash, authDate) {
  try {
    const res = await apiFetch("/api/profile/verify-contact", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        phone: phone,
        hash: hash,
        auth_date: authDate
      })
    });
    if (res.ok) {
      const profile = await res.json();
      appState.profile = profile;
      updateProfileUI(profile);
      showToast("Телефон подтвержден!");
    } else {
      showToast("Ошибка верификации телефона");
    }
  } catch (err) {
    console.error("sendPhoneVerification error:", err);
    showToast("Ошибка связи с сервером");
  }
}
