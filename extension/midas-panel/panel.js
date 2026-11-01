// OpenJarvis Midas paneli.
// Bu dosya bir uzantı sayfası (side panel) içinde çalışır, content script
// değil; bu sayede manifest'teki host_permissions sayesinde yerel
// OpenJarvis sunucusuna CORS'tan etkilenmeden fetch edebilir. Hiçbir zaman
// Midas sayfasına yazmaz veya gerçek emir göndermez. "Al"/"Sat" butonları
// yalnızca openjarvis.borsa.portfolio'daki SANAL (kağıt) portföyü etkiler.
// Sinyal/tarama otomatik teknik gösterge + analist görüşü özetidir, yatırım
// tavsiyesi değildir.

const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const POLL_MS = 30_000;

let paused = false;
let pollTimer = null;

const els = {
  status: document.getElementById("status"),
  pauseToggle: document.getElementById("pause-toggle"),
  watchlistBody: document.querySelector("#watchlist tbody"),
  watchlistEmpty: document.getElementById("watchlist-empty"),
  takipInput: document.getElementById("takip-ekle-input"),
  takipBtn: document.getElementById("takip-ekle-btn"),
  signalDetail: document.getElementById("signal-detail"),
  scanTavsiye: document.getElementById("scan-tavsiye"),
  scanRun: document.getElementById("scan-run"),
  scanResults: document.getElementById("scan-results"),
  scanEmpty: document.getElementById("scan-empty"),
  portfolioSummary: document.getElementById("portfolio-summary"),
  portfolioBody: document.querySelector("#portfolio-positions tbody"),
  portfolioEmpty: document.getElementById("portfolio-empty"),
  portfolioReset: document.getElementById("portfolio-reset"),
  events: document.getElementById("events"),
  eventsEmpty: document.getElementById("events-empty"),
  apiBase: document.getElementById("api-base"),
};

function fmtNumber(n) {
  if (typeof n !== "number") return "—";
  return n.toLocaleString("tr-TR", { maximumFractionDigits: 2 });
}

function fmtMoney(n) {
  if (typeof n !== "number") return "—";
  return `${n.toLocaleString("tr-TR", { maximumFractionDigits: 2 })} TL`;
}

function fmtChange(pct) {
  if (typeof pct !== "number") return "—";
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

function pnlClass(n) {
  return typeof n === "number" && n < 0 ? "pnl-down" : "pnl-up";
}

function verdictClass(verdict) {
  if (!verdict) return "verdict-neutral";
  if (verdict.startsWith("AL")) return "verdict-buy";
  if (verdict.startsWith("SAT")) return "verdict-sell";
  return "verdict-neutral";
}

async function getApiBase() {
  const stored = await chrome.storage.local.get("apiBase");
  return stored.apiBase || DEFAULT_API_BASE;
}

async function setApiBase(url) {
  await chrome.storage.local.set({ apiBase: url });
}

async function fetchJson(base, path, params = {}) {
  const url = new URL(path, base);
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) v.forEach((x) => url.searchParams.append(k, x));
    else url.searchParams.set(k, v);
  }
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

async function postJson(base, path, body) {
  const res = await fetch(new URL(path, base), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${path}: HTTP ${res.status}`);
  return data;
}

function renderWatchlist(items) {
  els.watchlistBody.innerHTML = "";
  els.watchlistEmpty.hidden = items.length > 0;
  for (const item of items) {
    const tr = document.createElement("tr");
    const pct = item.change_percent;
    const cls = typeof pct === "number" ? (pct >= 0 ? "up" : "down") : "";
    tr.innerHTML = item.error
      ? `<td>${item.symbol}</td><td colspan="3">${item.error}</td>`
      : `<td>${item.symbol}</td><td>${fmtNumber(item.last)}</td>` +
        `<td class="${cls}">${fmtChange(pct)}</td><td></td>`;
    const cell = tr.lastElementChild;
    if (!item.error && item.kind === "stock") {
      const sigBtn = document.createElement("button");
      sigBtn.className = "signal";
      sigBtn.textContent = "Sinyal";
      sigBtn.addEventListener("click", () => showSignal(item.symbol));
      cell.appendChild(sigBtn);
    }
    if (!item.error) {
      const buyBtn = document.createElement("button");
      buyBtn.className = "buy";
      buyBtn.textContent = "Al";
      buyBtn.addEventListener("click", () => buyPrompt(item.symbol));
      cell.appendChild(buyBtn);
    }
    if (item.kaynak && item.kaynak.includes("takip")) {
      const delBtn = document.createElement("button");
      delBtn.textContent = "Sil";
      delBtn.addEventListener("click", () => removeTakip(item.symbol));
      cell.appendChild(delBtn);
    }
    els.watchlistBody.appendChild(tr);
  }
}

function renderPortfolio(summary) {
  const ret = summary.total_return_pct;
  els.portfolioSummary.innerHTML =
    `Nakit: ${fmtMoney(summary.cash)} &nbsp;·&nbsp; ` +
    `Net değer: ${fmtMoney(summary.net_worth)} ` +
    (ret != null ? `<span class="${pnlClass(ret)}">(${fmtChange(ret)})</span>` : "") +
    `<br>Gerçekleşen K/Z: <span class="${pnlClass(summary.realized_pnl)}">` +
    `${fmtMoney(summary.realized_pnl)}</span> &nbsp;·&nbsp; ` +
    `Gerçekleşmemiş K/Z: <span class="${pnlClass(summary.unrealized_pnl)}">` +
    `${fmtMoney(summary.unrealized_pnl)}</span>`;

  els.portfolioBody.innerHTML = "";
  const positions = summary.positions ?? [];
  els.portfolioEmpty.hidden = positions.length > 0;
  for (const p of positions) {
    const tr = document.createElement("tr");
    if (p.error) {
      tr.innerHTML = `<td>${p.symbol}</td><td colspan="4">${p.error}</td>`;
      els.portfolioBody.appendChild(tr);
      continue;
    }
    tr.innerHTML =
      `<td>${p.symbol}</td><td>${p.quantity.toFixed(4)}</td>` +
      `<td>${fmtNumber(p.avg_cost)}</td><td>${fmtNumber(p.last)}</td>` +
      `<td class="${pnlClass(p.unrealized_pnl)}">${fmtMoney(p.unrealized_pnl)} ` +
      `(${fmtChange(p.unrealized_pnl_pct)})</td><td></td>`;
    const cell = tr.lastElementChild;
    const sigBtn = document.createElement("button");
    sigBtn.className = "signal";
    sigBtn.textContent = "Sinyal";
    sigBtn.addEventListener("click", () => showSignal(p.symbol));
    cell.appendChild(sigBtn);
    const sellBtn = document.createElement("button");
    sellBtn.className = "sell";
    sellBtn.textContent = "Sat";
    sellBtn.addEventListener("click", () => sellAll(p.symbol));
    cell.appendChild(sellBtn);
    els.portfolioBody.appendChild(tr);
  }
}

function renderEvents(events) {
  els.events.innerHTML = "";
  const recent = events.slice(-20).reverse();
  els.eventsEmpty.hidden = recent.length > 0;
  for (const ev of recent) {
    const li = document.createElement("li");
    const when = new Date(ev.at).toLocaleString("tr-TR");
    li.textContent = `${ev.symbol} — ${ev.message} (${when})`;
    els.events.appendChild(li);
  }
}

async function showSignal(symbol) {
  els.signalDetail.innerHTML = `<p class="empty">${symbol} için sinyal yükleniyor…</p>`;
  const base = await getApiBase();
  try {
    const result = await fetchJson(base, "/api/borsa/sinyal", { sembol: symbol });
    const horizons = Object.values(result.horizons)
      .map(
        (h) =>
          `<div class="horizon"><span>${h.label}</span>` +
          `<span class="${verdictClass(h.verdict)}">${h.verdict}</span></div>`
      )
      .join("");
    const an = result.analyst_recommendation;
    const analystLine = an
      ? `<p>Analist görüşü: güçlü al ${an.strongBuy ?? 0}, al ${an.buy ?? 0}, ` +
        `tut ${an.hold ?? 0}, sat ${an.sell ?? 0}, güçlü sat ${an.strongSell ?? 0}</p>`
      : "";
    const news = (result.news ?? [])
      .slice(0, 5)
      .map(
        (n) =>
          `<a class="news-item" href="${n.url}" target="_blank" rel="noopener">` +
          `<span class="news-date">${n.date}</span> ${n.title}</a>`
      )
      .join("");
    els.signalDetail.innerHTML =
      `<strong>${result.symbol}</strong>${horizons}${analystLine}` +
      (news ? `<p>Son haberler:</p>${news}` : "") +
      `<p class="empty">${result.disclaimer}</p>`;
  } catch (err) {
    els.signalDetail.innerHTML = `<p class="empty">Sinyal alınamadı: ${err.message}</p>`;
  }
}

async function runScan() {
  const base = await getApiBase();
  els.scanResults.innerHTML = "";
  els.scanEmpty.hidden = true;
  try {
    const result = await fetchJson(base, "/api/borsa/tara", {
      tavsiye: els.scanTavsiye.value,
      limit: 15,
    });
    els.scanEmpty.hidden = result.items.length > 0;
    for (const item of result.items) {
      const li = document.createElement("li");
      li.textContent = `${item.symbol} — ${item.name}`;
      els.scanResults.appendChild(li);
    }
  } catch (err) {
    els.scanEmpty.hidden = false;
    els.scanEmpty.textContent = `Tarama başarısız: ${err.message}`;
  }
}

async function addTakip() {
  const symbol = els.takipInput.value.trim();
  if (!symbol) return;
  const base = await getApiBase();
  try {
    await postJson(base, "/api/borsa/takip/ekle", { sembol: symbol });
    els.takipInput.value = "";
    refresh();
  } catch (err) {
    window.alert(`Eklenemedi: ${err.message}`);
  }
}

async function removeTakip(symbol) {
  if (!window.confirm(`${symbol} takip listesinden çıkarılsın mı?`)) return;
  const base = await getApiBase();
  try {
    await postJson(base, "/api/borsa/takip/sil", { sembol: symbol });
    refresh();
  } catch (err) {
    window.alert(`Çıkarılamadı: ${err.message}`);
  }
}

async function buyPrompt(symbol) {
  const raw = window.prompt(`${symbol} için SANAL alım tutarı (TL):`, "1000");
  if (raw === null) return;
  const amount = Number(raw.replace(",", "."));
  if (!Number.isFinite(amount) || amount <= 0) {
    window.alert("Geçerli bir tutar girin.");
    return;
  }
  const base = await getApiBase();
  try {
    const trade = await postJson(base, "/api/borsa/portfoy/al", {
      sembol: symbol,
      tutar: amount,
    });
    window.alert(
      `SANAL alım: ${trade.quantity.toFixed(4)} ${trade.symbol} @ ${trade.price}`
    );
    refresh();
  } catch (err) {
    window.alert(`Alım başarısız: ${err.message}`);
  }
}

async function sellAll(symbol) {
  if (!window.confirm(`${symbol} sanal pozisyonunun tamamını satmak istiyor musun?`)) {
    return;
  }
  const base = await getApiBase();
  try {
    const trade = await postJson(base, "/api/borsa/portfoy/sat", { sembol: symbol });
    window.alert(
      `SANAL satış: ${trade.quantity.toFixed(4)} ${trade.symbol} @ ${trade.price} ` +
      `(K/Z ${trade.realized_pnl >= 0 ? "+" : ""}${trade.realized_pnl} TL)`
    );
    refresh();
  } catch (err) {
    window.alert(`Satış başarısız: ${err.message}`);
  }
}

async function resetPortfolio() {
  if (!window.confirm("Sanal portföyü sıfırlamak istediğine emin misin? Tüm pozisyonlar ve geçmiş silinir.")) {
    return;
  }
  const raw = window.prompt("Yeni başlangıç bakiyesi (TL):", "100000");
  if (raw === null) return;
  const balance = Number(raw.replace(",", "."));
  if (!Number.isFinite(balance) || balance <= 0) {
    window.alert("Geçerli bir bakiye girin.");
    return;
  }
  const base = await getApiBase();
  try {
    await postJson(base, "/api/borsa/portfoy/sifirla", { bakiye: balance });
    refresh();
  } catch (err) {
    window.alert(`Sıfırlama başarısız: ${err.message}`);
  }
}

async function refresh() {
  if (paused) return;
  const base = await getApiBase();
  try {
    const [watch, eventsRes, portfolioRes] = await Promise.all([
      fetchJson(base, "/api/borsa/izleme"),
      fetchJson(base, "/api/borsa/alarm/olaylar"),
      fetchJson(base, "/api/borsa/portfoy"),
    ]);
    renderWatchlist(watch.items ?? []);
    renderEvents(eventsRes.events ?? []);
    renderPortfolio(portfolioRes);
    els.status.textContent = `bağlı · ${new Date().toLocaleTimeString("tr-TR")}`;
  } catch (err) {
    els.status.textContent = `sunucuya bağlanılamadı (${base})`;
    console.warn("OpenJarvis borsa paneli:", err);
  }
}

function togglePause() {
  paused = !paused;
  els.pauseToggle.textContent = paused ? "Devam ettir" : "Duraklat";
  els.pauseToggle.classList.toggle("paused", paused);
  if (!paused) refresh();
}

async function init() {
  els.apiBase.value = await getApiBase();
  els.apiBase.addEventListener("change", async () => {
    await setApiBase(els.apiBase.value.trim() || DEFAULT_API_BASE);
    refresh();
  });
  els.portfolioReset.addEventListener("click", resetPortfolio);
  els.takipBtn.addEventListener("click", addTakip);
  els.takipInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") addTakip();
  });
  els.scanRun.addEventListener("click", runScan);
  els.pauseToggle.addEventListener("click", togglePause);
  await refresh();
  pollTimer = setInterval(refresh, POLL_MS);
}

init();
