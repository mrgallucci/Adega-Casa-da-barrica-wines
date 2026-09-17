import { createContext, useContext, useEffect, useRef, useState } from "react";
import { trackAddToCart } from "@/lib/analytics";
import { useAuth } from "@/context/AuthContext";
import { api } from "@/lib/api";

const CartContext = createContext(null);
const KEY = "minha_adega_cart_v1";

export function CartProvider({ children }) {
  const [items, setItems] = useState(() => {
    try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch { return []; }
  });
  const auth = useAuth();
  const user = auth?.user ?? null;
  const mergedFor = useRef(null);

  useEffect(() => { localStorage.setItem(KEY, JSON.stringify(items)); }, [items]);

  // Merge do carrinho da conta no login: mesma variante vira UMA linha (o
  // carrinho atual do visitante vence em duplicatas). Preço/estoque são
  // revalidados no servidor na cotação e no checkout.
  useEffect(() => {
    if (!user) { mergedFor.current = null; return; }
    if (mergedFor.current === user.user_id) return;
    mergedFor.current = user.user_id;
    api.get("/account/cart").then((r) => {
      const remote = r.data.items || [];
      if (!remote.length) return;
      setItems((local) => {
        const map = new Map();
        for (const it of remote) {
          const key = `${it.wine_id}:${it.variant_id || "default"}`;
          map.set(key, { ...it, key });
        }
        for (const it of local) map.set(it.key, it);
        return [...map.values()];
      });
    }).catch(() => {});
    // eslint-disable-next-line
  }, [user]);

  // Sincroniza o carrinho com a conta (debounce). Nunca confia em preço daqui.
  useEffect(() => {
    if (!user) return;
    const t = setTimeout(() => {
      api.put("/account/cart", {
        items: items.map(({ wine_id, variant_id, qty, name, variant_label, price, image }) =>
          ({ wine_id, variant_id: variant_id || "default", qty, name, variant_label, price, image })),
      }).catch(() => {});
    }, 500);
    return () => clearTimeout(t);
  }, [items, user]);

  const add = (wine, qty = 1, variant = null) => {
    trackAddToCart(wine.wine_id);
    const v = variant || (wine.variants && wine.variants[0]) || null;
    const vid = v?.variant_id || null;
    const price = v ? (v.discount_price || v.price) : (wine.discount_price || wine.price);
    const key = `${wine.wine_id}:${vid || "default"}`;
    setItems((prev) => {
      const found = prev.find((i) => i.key === key);
      if (found) return prev.map((i) => i.key === key ? { ...i, qty: i.qty + qty } : i);
      return [...prev, {
        key, wine_id: wine.wine_id, variant_id: vid, qty, name: wine.name,
        variant_label: v ? `${v.vintage || "sem safra"} · ${v.volume_ml || 750}ml` : null,
        price, image: wine.image,
      }];
    });
  };
  const remove = (key) => setItems((prev) => prev.filter((i) => i.key !== key));
  const setQty = (key, qty) => setItems((prev) => prev.map((i) => i.key === key ? { ...i, qty: Math.max(1, qty) } : i));
  const clear = () => setItems([]);
  const count = items.reduce((s, i) => s + i.qty, 0);
  const subtotal = items.reduce((s, i) => s + i.qty * i.price, 0);

  return (
    <CartContext.Provider value={{ items, add, remove, setQty, clear, count, subtotal }}>
      {children}
    </CartContext.Provider>
  );
}

export const useCart = () => useContext(CartContext);
