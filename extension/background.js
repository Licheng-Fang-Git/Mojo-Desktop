const SERVER_URL = "http://localhost:8000/alert";

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
      }),
    }).catch((error) => {
      console.error("[Mojo] Failed to reach local bridge server:", error);
    });
  }

}
