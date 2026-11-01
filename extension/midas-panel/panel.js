// OpenJarvis Midas paneli.
// Bu dosya bir uzantı sayfası (side panel) içinde çalışır, content script
// değil; bu sayede manifest'teki host_permissions sayesinde yerel
// OpenJarvis sunucusuna CORS'tan etkilenmeden fetch edebilir. Hiçbir zaman
// Midas sayfasına yazmaz veya gerçek emir göndermez. "Al"/"Sat" butonları
// yalnızca openjarvis.borsa.portfolio'daki SANAL (kağıt) portföyü etkiler.

const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const POLL_MS = 30_000;

const els = {
  status: document.getElementById("status"),
  watchlistBody: document.querySelector("#watchlist tbody"),
  watchlistEmpty: document.getElementById("watchlist-empty"),
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

async function getApiBase() {
  const stored = await chrome.storage.local.get("apiBase");
  return stored.apiBase || DEFAULT_API_BASE;
}

async function setApiBase(url) {
  await chrome.storage.local.set({ apiBase: url });
}

async function fetchJson(base, path, params = {}) {
  const url = new URL(path, base);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
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
    if (!item.error) {
      const btn = document.createElement("button");
      btn.className = "buy";
      btn.textContent = "Al";
      btn.addEventListener("click", () => buyPrompt(item.symbol));
      tr.lastElementChild.appendChild(btn);
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
    const btn = document.createElement("button");
    btn.className = "sell";
    btn.textContent = "Sat";
    btn.addEventListener("click", () => sellAll(p.symbol));
    tr.lastElementChild.appendChild(btn);
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

async function init() {
  els.apiBase.value = await getApiBase();
  els.apiBase.addEventListener("change", async () => {
    await setApiBase(els.apiBase.value.trim() || DEFAULT_API_BASE);
    refresh();
  });
  els.portfolioReset.addEventListener("click", resetPortfolio);
  await refresh();
  setInterval(refresh, POLL_MS);
}

init();
