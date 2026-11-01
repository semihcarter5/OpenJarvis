// OpenJarvis Midas paneli — arka plan servis çalışanı.
// Yan paneli yalnızca Midas sekmelerinde etkinleştirir; başka bir yerde
// araç çubuğu simgesine tıklamak hiçbir şey yapmaz. Bu dosya ağ isteği
// yapmaz; tüm veri çekimi panel.js içinde, uzantı sayfası bağlamından olur.

const MIDAS_HOST_RE = /(^|\.)midas\.com\.tr$/i;

function isMidasUrl(url) {
  try {
    return MIDAS_HOST_RE.test(new URL(url).hostname);
  } catch {
    return false;
  }
}

async function syncPanelForTab(tabId, url) {
  const enabled = Boolean(url) && isMidasUrl(url);
  await chrome.sidePanel.setOptions({
    tabId,
    path: "panel.html",
    enabled,
  });
}

// Without this, clicking the toolbar icon does nothing at all - Chrome
// only opens the side panel on an action click if this is explicitly
// turned on. It still respects the per-tab enabled/disabled state set
// above, so the icon stays inert on non-Midas tabs.
chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((err) => console.error("setPanelBehavior failed:", err));

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.url || changeInfo.status === "complete") {
    syncPanelForTab(tabId, tab.url);
  }
});

chrome.tabs.onActivated.addListener(({ tabId }) => {
  chrome.tabs.get(tabId, (tab) => {
    if (tab?.url) syncPanelForTab(tabId, tab.url);
  });
});

// Re-sync the currently active tab on install/startup - onUpdated/onActivated
// only fire on future navigation or tab switches, so a tab already open when
// the extension loads would otherwise stay in its default (disabled) state.
chrome.runtime.onInstalled.addListener(syncActiveTab);
chrome.runtime.onStartup.addListener(syncActiveTab);

function syncActiveTab() {
  chrome.tabs.query({ active: true, lastFocusedWindow: true }, ([tab]) => {
    if (tab?.id && tab.url) syncPanelForTab(tab.id, tab.url);
  });
}
