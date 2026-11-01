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
