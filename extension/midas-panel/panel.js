// OpenJarvis Midas paneli — salt-okur veri çekimi.
// Bu dosya bir uzantı sayfası (side panel) içinde çalışır, content script
// değil; bu sayede manifest'teki host_permissions sayesinde yerel
// OpenJarvis sunucusuna CORS'tan etkilenmeden fetch edebilir. Hiçbir
// zaman Midas sayfasına yazmaz veya emir göndermez.

const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const POLL_MS = 30_000;

const els = {
  status: document.getElementById("status"),
  watchlistBody: document.querySelector("#watchlist tbody"),
  watchlistEmpty: document.getElementById("watchlist-empty"),
  events: document.getElementById("events"),
  eventsEmpty: document.getElementById("events-empty"),
  apiBase: document.getElementById("api-base"),
};

function fmtNumber(n) {
  if (typeof n !== "number") return "—";
  return n.toLocaleString("tr-TR", { maximumFractionDigits: 2 });
}

function fmtChange(pct) {
  if (typeof pct !== "number") return "—";
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
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

function renderWatchlist(items) {
  els.watchlistBody.innerHTML = "";
  els.watchlistEmpty.hidden = items.length > 0;
  for (const item of items) {
    const tr = document.createElement("tr");
    const pct = item.change_percent;
    const cls = typeof pct === "number" ? (pct >= 0 ? "up" : "down") : "";
    tr.innerHTML = item.error
      ? `<td>${item.symbol}</td><td colspan="2">${item.error}</td>`
      : `<td>${item.symbol}</td><td>${fmtNumber(item.last)}</td>` +
        `<td class="${cls}">${fmtChange(pct)}</td>`;
    els.watchlistBody.appendChild(tr);
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

async function refresh() {
  const base = await getApiBase();
  try {
    const [watch, eventsRes] = await Promise.all([
      fetchJson(base, "/api/borsa/izleme"),
      fetchJson(base, "/api/borsa/alarm/olaylar"),
    ]);
    renderWatchlist(watch.items ?? []);
    renderEvents(eventsRes.events ?? []);
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
  await refresh();
  setInterval(refresh, POLL_MS);
}

init();
