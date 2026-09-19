/**
 * MAX Smart City Housing Mini-App Client.
 * Interfaces with MAX Bridge (window.WebApp) and backend REST API.
 */

// State
let appState = {
  meters: [],
  selectedMeterId: "meter-khvs-1",
  currentTab: "tab-meters"
};

// DOM Elements
document.addEventListener("DOMContentLoaded", () => {
  initMaxBridge();
  initTabs();
  loadMeters();
  loadTickets();
  bindEventHandlers();
});

function initMaxBridge() {
  const statusEl = document.getElementById("bridge-status");
  if (window.WebApp) {
    statusEl.textContent = "MAX Bridge ✓";
    statusEl.className = "badge-status online";

    // Initialize BackButton
    if (window.WebApp.BackButton) {
      window.WebApp.BackButton.onClick(() => {
        switchTab("tab-meters");
        window.WebApp.BackButton.hide();
      });
    }

    // Enable closing confirmation to protect unsaved readings
    if (typeof window.WebApp.enableClosingConfirmation === "function") {
      window.WebApp.enableClosingConfirmation();
    }
  } else {
    statusEl.textContent = "Web Browser";
    statusEl.className = "badge-status";
  }
}

function haptic(type = "light") {
  if (window.WebApp && window.WebApp.HapticFeedback) {
    window.WebApp.HapticFeedback.impactOccurred(type);
  }
}

function showToast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 3000);
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

// ----------------- Data Loading -----------------

async function loadMeters() {
  try {
    const res = await fetch("/api/meters");
    const data = await res.json();
    appState.meters = data;
    renderMetersList(data);
  } catch (err) {
    console.error("Failed to load meters:", err);
  }
}

function renderMetersList(meters) {
  const container = document.getElementById("meters-container");
  container.innerHTML = "";
  meters.forEach(m => {
    const card = document.createElement("div");
    card.className = "meter-card";
    card.innerHTML = `
      <div class="meter-info-col">
        <h4>${m.name}</h4>
        <div class="meter-sub-info">№ ${m.serial_number} • ${m.installation_place}</div>
        <div class="meter-sub-info text-green">Поверка до: ${m.verification_date_valid_until}</div>
      </div>
      <div class="meter-reading-col">
        <span class="meter-val-num">${m.last_reading_value}</span>
        <span class="text-muted text-sm">${m.unit}</span>
      </div>
    `;
    card.addEventListener("click", () => {
      appState.selectedMeterId = m.id;
      triggerAiScanMock(m);
    });
    container.appendChild(card);
  });
}

async function loadTickets() {
  try {
    const res = await fetch("/api/tickets");
    const data = await res.json();
    renderTicketsList(data);
  } catch (err) {
    console.error("Failed to load tickets:", err);
  }
}

function renderTicketsList(tickets) {
  const container = document.getElementById("tickets-container");
  container.innerHTML = "";
  tickets.forEach(t => {
    const item = document.createElement("div");
    item.className = "card";
    const prioColor = t.priority === "emergency" ? "badge-danger" : (t.priority === "urgent" ? "badge-warning" : "badge-info");
    const prioLabel = t.priority === "emergency" ? "Аварийная (30 мин)" : (t.priority === "urgent" ? "Срочная (24 ч)" : "Плановая");
    
    item.innerHTML = `
      <div class="card-header-with-badge">
        <h4>${t.category} (${t.id})</h4>
        <span class="badge ${prioColor}">${prioLabel}</span>
      </div>
      <p class="text-sm" style="margin: 6px 0;">${t.description}</p>
      <div class="meter-sub-info">Мастер: ${t.assigned_master || "Назначение в процессе"} • Регламент SLA: ${t.sla_hours} ч</div>
    `;
    container.appendChild(item);
  });
}

// ----------------- Event Handlers -----------------

function bindEventHandlers() {
  // 1. Camera Trigger
  const btnCam = document.getElementById("btn-open-camera");
  const fileInput = document.getElementById("file-meter-input");
  btnCam.addEventListener("click", () => {
    haptic("medium");
    fileInput.click();
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files && fileInput.files[0]) {
      showToast("Фото получено. Запуск AI-распознавания...");
      triggerAiScanMock();
    }
  });

  // 2. Submit Reading Confirmation
  document.getElementById("btn-submit-reading").addEventListener("click", async () => {
    haptic("success");
    const readingVal = parseFloat(document.getElementById("input-reading-correct").value);
    try {
      const res = await fetch("/api/meters/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meter_id: appState.selectedMeterId,
          reading_value: readingVal,
          submission_channel: "max_miniapp"
        })
      });
      const data = await res.json();
      const feedback = document.getElementById("reading-feedback");
      feedback.style.display = "block";
      if (data.is_valid) {
        feedback.className = "alert-box alert-success";
        feedback.innerHTML = `<b>Успешно!</b> ${data.message} (Расход: +${data.consumption} м³). Данные синхронизированы с ГИС ЖКХ.`;
        loadMeters();
      } else {
        feedback.className = "alert-box alert-danger";
        feedback.innerHTML = `<b>Ошибка:</b> ${data.message}`;
      }
      showToast("Показания переданы!");
    } catch (err) {
      showToast("Ошибка отправки данных");
    }
  });

  // 3. FGIS Arshin Verification Lookup
  document.getElementById("btn-check-arshin").addEventListener("click", async () => {
    haptic("light");
    const serial = document.getElementById("input-arshin-serial").value.trim();
    if (!serial) return;
    try {
      const res = await fetch(`/api/arshin/check?serial=${encodeURIComponent(serial)}`);
      const data = await res.json();
      
      const card = document.getElementById("arshin-result-card");
      card.style.display = "block";
      document.getElementById("shield-icon").textContent = data.shield_color === "green" ? "🟢" : "🔴";
      document.getElementById("shield-title").textContent = data.is_fraud_warning ? "Поверка ДЕЙСТВИТЕЛЬНА (Антифрод-щит)" : "Требуется поверка";
      document.getElementById("shield-org").textContent = `Организация: ${data.organization_name} • Срок: до ${data.valid_until}`;
      document.getElementById("shield-message").textContent = data.safety_message;
      document.getElementById("shield-link").href = data.fgis_arshin_url;
      showToast("Сверка с ФГИС АРШИН завершена");
    } catch (err) {
      showToast("Ошибка связи с реестром АРШИН");
    }
  });

  // 4. GOST QR Native MAX Camera Scanner
  document.getElementById("btn-scan-qr-bridge").addEventListener("click", async () => {
    haptic("medium");
    if (window.WebApp && typeof window.WebApp.openCodeReader === "function") {
      try {
        const qrString = await window.WebApp.openCodeReader(true);
        if (qrString) {
          parseAndDisplayQr(qrString);
        }
      } catch (err) {
        console.warn("MAX openCodeReader closed or failed:", err);
      }
    } else {
      showToast("Камера MAX Bridge доступна внутри мессенджера. Используем тестовую квитанцию.");
      pasteSampleGostQr();
    }
  });

  document.getElementById("btn-paste-sample-qr").addEventListener("click", () => {
    haptic("light");
    pasteSampleGostQr();
  });

  // 5. Pay via SBP Simulation
  document.getElementById("btn-pay-sbp").addEventListener("click", () => {
    haptic("success");
    const alertBox = document.getElementById("payment-success-alert");
    alertBox.style.display = "block";
    alertBox.innerHTML = `
      <b>✅ Оплата 4 850.50 ₽ успешно выполнена через СБП!</b><br/>
      Платеж автоматически расщеплен на специальный счет 40821:<br/>
      • Водоканал (1 212.63 ₽) • МОЭК (2 182.72 ₽) • Мосэнергосбыт (727.58 ₽) • УК (727.57 ₽).<br/>
      Электронный чек отправлен в чат-бот MAX.
    `;
    showToast("Сплит-платеж по 103-ФЗ проведен!");
  });

  // 6. Night Flow Napkin Tests
  document.getElementById("btn-napkin-wet").addEventListener("click", async () => {
    haptic("warning");
    const res = await fetch("/api/night-flow/diagnose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entrance_id: 1, napkin_test_result: "wet" })
    });
    const data = await res.json();
    displayNightFlowResult(data);
  });

  document.getElementById("btn-napkin-dry").addEventListener("click", async () => {
    haptic("light");
    const res = await fetch("/api/night-flow/diagnose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entrance_id: 1, napkin_test_result: "dry" })
    });
    const data = await res.json();
    displayNightFlowResult(data);
  });

  document.getElementById("btn-calc-night-diff").addEventListener("click", async () => {
    haptic("light");
    const evening = parseFloat(document.getElementById("input-night-reading").value);
    const morning = parseFloat(document.getElementById("input-morning-reading").value);
    const res = await fetch("/api/night-flow/diagnose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        entrance_id: 1,
        night_reading_before_bed: evening,
        morning_reading: morning
      })
    });
    const data = await res.json();
    displayNightFlowResult(data);
  });

  document.getElementById("btn-claim-reward").addEventListener("click", () => {
    haptic("success");
    showToast("🎉 Скидка 10% на квартплату зафиксирована в лицевом счете!");
  });

  // 7. Tenant Guest Access Generator
  document.getElementById("btn-generate-guest-link").addEventListener("click", async () => {
    haptic("light");
    const res = await fetch("/api/guest/generate", {
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
    box.style.display = "block";
    box.innerHTML = `
      <b>Временная ссылка для арендатора в MAX:</b><br/>
      <span style="color:#0077ff;">${data.direct_max_link}</span><br/><br/>
      <i>Срок действия: 30 дней. Авторизация через ЕСИА не требуется. Арендатор сможет передавать показания и оплачивать квитанции.</i>
    `;
    showToast("Гостевая ссылка сгенерирована!");
  });

  // 8. Create Ticket Modal Trigger
  document.getElementById("btn-show-ticket-modal").addEventListener("click", async () => {
    haptic("light");
    const cat = prompt("Укажите категорию неисправности (Сантехника, Электрика, Лифт, Отопление):", "Сантехника");
    if (!cat) return;
    const desc = prompt("Опишите проблему:", "Протечка трубы под раковиной, капает вода");
    if (!desc) return;

    try {
      const res = await fetch("/api/tickets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          category: cat,
          description: desc,
          priority: "urgent",
          address: "ул. Ленина, д. 42, кв. 15"
        })
      });
      const t = await res.json();
      showToast(`Заявка ${t.id} создана! Срок регламента: ${t.sla_hours} ч`);
      loadTickets();
    } catch (e) {
      showToast("Ошибка создания заявки");
    }
  });
}

// ----------------- Mock AI Vision Simulation -----------------

async function triggerAiScanMock(meter = null) {
  try {
    const hint = meter ? meter.meter_type : "cold_water";
    const res = await fetch("/api/ai/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_type_hint: hint })
    });
    const data = await res.json();

    const preview = document.getElementById("ai-scan-result");
    preview.style.display = "block";
    document.getElementById("ocr-reading-val").textContent = `${data.recognized_reading} м³`;
    document.getElementById("ocr-serial-val").textContent = `№ ${data.recognized_serial_number}`;
    document.getElementById("ocr-confidence-val").textContent = `${(data.confidence * 100).toFixed(1)}%`;
    document.getElementById("input-reading-correct").value = data.recognized_reading;

    // Split display: black integer rollers and red decimal rollers
    const intStr = String(Math.floor(data.recognized_reading)).padStart(5, '0');
    document.getElementById("ocr-black-digits").textContent = intStr;
    document.getElementById("ocr-red-digits").textContent = "789";

    preview.scrollIntoView({ behavior: "smooth" });
    showToast("Распознаны цифры и заводской номер!");
  } catch (err) {
    console.error("AI scan failed:", err);
  }
}

// ----------------- GOST QR Parser & Split -----------------

function pasteSampleGostQr() {
  const sample = "ST00012|Name=ООО УК ДОМОВОЙ СЕРВИС|PersonalAcc=40702810938000012345|BIC=044525225|CorrespAcc=30101810400000000225|PayeeINN=7701234567|Sum=485050|PersAcc=1004567890|Period=092026";
  parseAndDisplayQr(sample);
}

async function parseAndDisplayQr(qrPayload) {
  try {
    const res = await fetch("/api/billing/parse-qr", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ qr_payload: qrPayload })
    });
    const data = await res.json();

    document.getElementById("bill-total-amount").textContent = `${data.total_amount_rubles.toFixed(2)} ₽`;
    document.getElementById("bill-payee-name").textContent = data.recipient_name;
    document.getElementById("bill-pers-acc").textContent = data.personal_account;
    document.getElementById("bill-account-40821").textContent = data.payee_account;

    const list = document.getElementById("split-recipients-list");
    list.innerHTML = "";
    data.split_details.forEach(item => {
      const row = document.createElement("div");
      row.className = "split-row";
      row.innerHTML = `
        <div class="split-info">
          <span class="split-name">${item.recipient_name}</span>
          <span class="split-sub">Спецсчет: ${item.account_40821} • ${item.purpose}</span>
        </div>
        <span class="split-amount">${item.amount_rubles.toFixed(2)} ₽</span>
      `;
      list.appendChild(row);
    });

    showToast("QR квитанции по ГОСТ Р 56042-2014 проверен!");
  } catch (err) {
    showToast("Неверный формат QR-кода");
  }
}

// ----------------- Night Flow Diagnostics -----------------

function displayNightFlowResult(data) {
  const alertCard = document.getElementById("night-flow-alert");
  alertCard.style.display = "block";
  document.getElementById("night-verdict-title").textContent = data.apartment_leak_detected ? "🚨 Утечка найдена в вашей квартире!" : "✅ Сантехника в норме";
  document.getElementById("night-verdict-sub").textContent = `Потеря воды: ~${data.apartment_difference_liters} литров`;
  document.getElementById("night-verdict-text").textContent = `${data.diagnosis_verdict} ${data.recommendation}`;
  
  const claimBtn = document.getElementById("btn-claim-reward");
  claimBtn.style.display = data.reward_eligible ? "inline-flex" : "none";
  alertCard.scrollIntoView({ behavior: "smooth" });
}
