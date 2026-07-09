const SERVER_URL = "http://localhost:8000/alert";
const DECISION_URL = "http://localhost:8000/decision";
// chrome.alarms is what actually wakes a terminated MV3 service worker back
// up — a plain setInterval dies the moment Chrome kills the idle worker.
// Chrome only enforces its 30s/1min minimum alarm period for packed/
// published extensions; unpacked dev builds (like this one) aren't
// throttled, so a short period is fine here.
const POLL_PERIOD_MINUTES = 2 / 60; // ~2 seconds

// Sites we consider distracting. Add/remove as needed.
const BLACKLIST = ["youtube.com", "reddit.com", "twitter.com"];

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete" || !tab.url) return;
  checkAndAlert(tab);
});

function checkAndAlert(tab) {
  let hostName = new URL(tab.url).hostname;
  const wwwPrefix = hostName.startsWith("www.") ? hostName.indexOf(".") : hostName.indexOf("/");
  hostName = hostName.substring(wwwPrefix + 1);

  if (BLACKLIST.some((site) => hostName.includes(site))) {
    fetch(SERVER_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        site: hostName,
        message: "Closing distraction...",
        tab_id: tab.id,
      }),
    })
      .then(() => startPolling(tab.id))
      .catch((error) => {
        console.error("[Mojo] Failed to reach local bridge server:", error);
      });
  }
}

function startPolling(tabId) {
  // periodInMinutes also covers the first fire — no separate delayInMinutes
  // needed for a short interval like this.
  chrome.alarms.create(pollAlarmName(tabId), { periodInMinutes: POLL_PERIOD_MINUTES });
}

function pollAlarmName(tabId) {
  return `mojo-poll-${tabId}`;
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (!alarm.name.startsWith("mojo-poll-")) return;

  const tabId = parseInt(alarm.name.slice("mojo-poll-".length), 10);

  fetch(`${DECISION_URL}?tab_id=${tabId}`)
    .then((res) => res.json())
    .then(({ action }) => {
      if (!action) return; // still waiting on the user, keep polling

      chrome.alarms.clear(alarm.name);
      if (action.type === "close") {
        chrome.tabs.remove(tabId);
      } else if (action.type === "open" && action.url) {
        chrome.tabs.update(tabId, { url: action.url });
      }
    })
    .catch((error) => {
      console.error("[Mojo] Failed to poll for decision:", error);
    });
});
