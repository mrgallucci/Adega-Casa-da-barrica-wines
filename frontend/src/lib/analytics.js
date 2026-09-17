import { api } from "./api";

// Analytics privacy-first: sessão = id aleatório por aba (sessionStorage),
// sem cookies persistentes, sem fingerprint. O servidor descarta acessos de
// administradores e honra Do Not Track / Global Privacy Control.
const KEY = "cdb_analytics_session";

export function analyticsSessionId() {
  let s = sessionStorage.getItem(KEY);
  if (!s) {
    s = crypto.randomUUID();
    sessionStorage.setItem(KEY, s);
  }
  return s;
}

function privacyOptOut() {
  return navigator.doNotTrack === "1" || navigator.globalPrivacyControl === true;
}

export function trackPageview(path) {
  if (privacyOptOut()) return;
  const m = path.match(/^\/vinho\/([^/?#]+)/);
  api.post("/analytics/track", {
    type: "pageview", path, wine_id: m ? m[1] : null, session_id: analyticsSessionId(),
  }).catch(() => {});
}

export function trackAddToCart(wineId) {
  if (privacyOptOut()) return;
  api.post("/analytics/track", {
    type: "add_to_cart", path: "/carrinho", wine_id: wineId || null, session_id: analyticsSessionId(),
  }).catch(() => {});
}
