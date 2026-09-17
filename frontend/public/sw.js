/* Service Worker — Casa da Barrica Wines PWA
   Cache APENAS de assets estáticos públicos. Nunca armazena respostas de /api
   (dados privados, financeiros ou sensíveis). */
const CACHE = "adega-static-v1";
const STATIC = ["/", "/manifest.json", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(STATIC)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (url.pathname.startsWith("/api")) return; // nunca cachear API
  e.respondWith(
    caches.match(e.request).then((cached) => cached || fetch(e.request).then((res) => {
      if (res.ok && url.origin === self.location.origin && !url.pathname.includes("conta") && !url.pathname.includes("admin") && !url.pathname.includes("carrinho")) {
        const clone = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, clone));
      }
      return res;
    }).catch(() => cached))
  );
});
