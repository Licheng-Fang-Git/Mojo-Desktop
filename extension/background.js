const SERVER_URL = "http://localhost:8000/alert";

// Sites we consider distracting. Add/remove as needed.
const BLACKLIST = ["youtube.com", "reddit.com", "twitter.com"];

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete" || !tab.url) return;
  checkAndAlert(tab);
});

function checkAndAlert(tab) {
  const HOSTNAME = new URL(tab.url).hostname;
  console.log(HOSTNAME);
  // TODO (your turn):
  // 1. Pull the hostname out of tab.url — the `URL` constructor gives you
  //    `.hostname` without you having to parse the string by hand.
  // 2. Decide whether that hostname counts as a match against BLACKLIST.
  //    Think about: does "www.youtube.com" or "m.youtube.com" need to match
  //    "youtube.com" too? A plain === check won't catch those.
  // 3. If it matches, send a POST to SERVER_URL with a JSON body shaped like
  //    { site: hostname, message: "Closing distraction..." } using fetch().
}
