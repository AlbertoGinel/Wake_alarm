// Minimal service worker -- exists only to satisfy PWA installability
// criteria (Chrome requires one registered before offering a real "Add to
// Home screen" app install, not just a bookmark shortcut). Deliberately
// does NOT cache anything: this page's whole value is live BLE data from
// the ESP32, and caching the HTML/JS shell risks serving a stale version
// after this file changes.
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
