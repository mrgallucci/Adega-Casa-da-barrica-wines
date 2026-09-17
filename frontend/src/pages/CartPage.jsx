import { useState, useEffect } from "react";
import { useCart } from "@/context/CartContext";
import { useAuth } from "@/context/AuthContext";
import { api, formatBRL } from "@/lib/api";
import { Link, useNavigate } from "react-router-dom";
import { Trash2, ShoppingBag } from "lucide-react";
import { toast } from "sonner";

export default function CartPage() {
  const { items, remove, setQty, subtotal, clear } = useCart();
  const { user } = useAuth();
  const [cep, setCep] = useState("");
  const [address, setAddress] = useState("");
  const [birthDate, setBirthDate] = useState("");
  const [coupon, setCoupon] = useState("");
  const [payment, setPayment] = useState("pix");
  const [quote, setQuote] = useState(null);
  const [quoteError, setQuoteError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [salesSuspended, setSalesSuspended] = useState(false);
  const [pointsCfg, setPointsCfg] = useState(null);
  const [usePoints, setUsePoints] = useState("");
  const nav = useNavigate();
  const suspendedForMe = salesSuspended && !user?.homologation_buyer;
  const redeemActive = !!(user && user.club_member && pointsCfg?.redeem_enabled && (user.points_balance || 0) > 0);
  const redeemQty = redeemActive && parseInt(usePoints) > 0 ? parseInt(usePoints) : null;

  useEffect(() => {
    api.get("/settings").then((r) => { setSalesSuspended(r.data.sales_status === "suspended"); setPointsCfg(r.data.points || null); }).catch(() => {});
  }, []);

  const requestQuote = async () => {
    if (items.length === 0) return;
    try {
      const { data } = await api.post("/checkout/quote", {
        items: items.map((i) => ({ wine_id: i.wine_id, variant_id: i.variant_id, qty: i.qty })),
        cep: cep || "00000000", address: address || "N/A",
        birth_date: birthDate || "1990-01-01",
        payment_method: payment, coupon: coupon || null, redeem_points: redeemQty,
      });
      setQuote(data);
      setQuoteError(null);
    } catch (err) {
      setQuote(null);
      setQuoteError(err?.response?.data?.detail || null);
    }
  };

  useEffect(() => { requestQuote(); /* eslint-disable-next-line */ }, [items, cep, coupon, payment, birthDate, usePoints]);

  const submit = async () => {
    if (!user) { toast.info("Para finalizar sua compra, entre na sua conta ou cadastre-se."); return nav("/login?next=/carrinho"); }
    if (!cep || !address) return toast.error("Informe CEP e endereço.");
    if (!birthDate) return toast.error("Informe sua data de nascimento (obrigatório para venda de bebidas).");
    setLoading(true);
    try {
      const { data } = await api.post("/checkout", {
        items: items.map((i) => ({ wine_id: i.wine_id, variant_id: i.variant_id, qty: i.qty })),
        cep, address, birth_date: birthDate, payment_method: payment, coupon: coupon || null, redeem_points: redeemQty,
      });
      if (data.checkout_url) {
        // Mercado Pago Checkout Pro (hospedado) — dados do cartão nunca passam pela loja
        toast.info("Redirecionando para o pagamento seguro...");
        window.location.assign(data.checkout_url);
        return;
      }
      await api.post(`/orders/${data.order_id}/confirm-demo`);
      toast.success("Pedido confirmado em modo demonstração!");
      clear();
      nav(`/conta?order=${data.order_id}`);
    } catch (err) {
      if (err?.response?.status === 401) {
        toast.info("Sua sessão expirou. Para finalizar sua compra, entre na sua conta ou cadastre-se.");
        return nav("/login?next=/carrinho");
      }
      toast.error(err?.response?.data?.detail || "Falha no checkout.");
    } finally { setLoading(false); }
  };

  if (items.length === 0) {
    return (
      <div className="surface p-12 text-center max-w-xl mx-auto fade-up" data-testid="cart-empty">
        <ShoppingBag className="w-12 h-12 mx-auto text-[#C28D58] mb-4" />
        <h1 className="font-serif text-3xl text-[#F7F2EB] mb-2">Seu carrinho está vazio</h1>
        <p className="text-[#A89B8C] mb-6">Descubra rótulos selecionados pelo nosso sommelier.</p>
        <Link to="/catalogo" className="btn-primary inline-block">Explorar catálogo</Link>
      </div>
    );
  }

  return (
    <div className="fade-up">
      <h1 className="font-serif text-4xl text-[#F7F2EB] mb-8">Carrinho</h1>
      <div className="grid lg:grid-cols-[1fr_400px] gap-8">
        <div className="space-y-4">
          {items.map((i) => (
            <div key={i.key} className="surface p-4 flex gap-4 items-center" data-testid={`cart-item-${i.key}`}>
              <img src={i.image} alt={i.name} className="w-20 h-24 object-cover rounded-lg bg-[#25201B]" />
              <div className="flex-1">
                <div className="font-serif text-lg text-[#F7F2EB]">{i.name}</div>
                {i.variant_label && <div className="text-xs text-[#A89B8C]">{i.variant_label}</div>}
                <div className="text-[#C28D58]">{formatBRL(i.price)}</div>
                <div className="flex items-center gap-2 mt-2">
                  <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setQty(i.key, i.qty - 1)}>−</button>
                  <span className="w-8 text-center text-sm">{i.qty}</span>
                  <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setQty(i.key, i.qty + 1)}>+</button>
                </div>
              </div>
              <button onClick={() => remove(i.key)} className="text-[#A89B8C] hover:text-[#8A2436]" data-testid={`cart-remove-${i.key}`}>
                <Trash2 className="w-5 h-5" />
              </button>
            </div>
          ))}
        </div>

        <div className="surface p-6 h-fit sticky top-24">
          <h2 className="font-serif text-2xl text-[#F7F2EB] mb-4">Resumo</h2>
          <div className="space-y-3 mb-4">
            <input data-testid="cart-cep" className="input-cellar" placeholder="CEP" value={cep} onChange={(e) => setCep(e.target.value)} />
            <input data-testid="cart-address" className="input-cellar" placeholder="Endereço completo" value={address} onChange={(e) => setAddress(e.target.value)} />
            <div>
              <label className="text-xs text-[#A89B8C] block mb-1">Data de nascimento (obrigatório — venda de bebidas alcoólicas)</label>
              <input data-testid="cart-birthdate" type="date" className="input-cellar" value={birthDate} onChange={(e) => setBirthDate(e.target.value)} />
            </div>
            <input data-testid="cart-coupon" className="input-cellar" placeholder="Cupom (ex.: PRIMEIRAADEGA)" value={coupon} onChange={(e) => setCoupon(e.target.value)} />
            {redeemActive && (
              <div>
                <label className="text-xs text-[#A89B8C] block mb-1">Usar pontos do Clube (saldo: {user.points_balance} · 1 ponto = {formatBRL(pointsCfg.point_value_brl)})</label>
                <input data-testid="redeem-points" type="number" min="0" max={user.points_balance} className="input-cellar" placeholder="0" value={usePoints} onChange={(e) => setUsePoints(e.target.value)} />
              </div>
            )}
            <div className="flex gap-2">
              <button onClick={() => setPayment("pix")} className={`flex-1 py-2 rounded-full text-sm border ${payment === "pix" ? "border-[#C28D58] bg-[#C28D58]/10 text-[#C28D58]" : "border-[#C28D58]/25 text-[#D5C7B7]"}`} data-testid="pay-pix">PIX</button>
              <button onClick={() => setPayment("credit_card")} className={`flex-1 py-2 rounded-full text-sm border ${payment === "credit_card" ? "border-[#C28D58] bg-[#C28D58]/10 text-[#C28D58]" : "border-[#C28D58]/25 text-[#D5C7B7]"}`} data-testid="pay-card">Cartão</button>
            </div>
          </div>
          <div className="text-sm space-y-2 border-t border-[#C28D58]/20 pt-4">
            <div className="flex justify-between text-[#D5C7B7]"><span>Subtotal</span><span>{formatBRL(quote?.subtotal ?? subtotal)}</span></div>
            <div className="flex justify-between text-[#D5C7B7]">
              <span>Frete climatizado <span className="text-[10px] text-[#A89B8C]">(tabela da loja)</span></span>
              <span>{quote ? formatBRL(quote.shipping) : "—"}</span>
            </div>
            {quote?.shipping_deadline && (
              <div className="flex justify-between text-[#A89B8C] text-xs">
                <span>Prazo estimado</span><span data-testid="shipping-deadline">{quote.shipping_deadline}</span>
              </div>
            )}
            {quoteError && (
              <div className="text-xs text-[#D88A96] bg-[#5E1925]/30 border border-[#8A2436]/50 rounded-lg p-2" data-testid="shipping-error">{quoteError}</div>
            )}
            {quote?.discount > 0 && <div className="flex justify-between text-[#C28D58]"><span>Cupom</span><span>−{formatBRL(quote.discount)}</span></div>}
            {quote?.points_discount > 0 && <div className="flex justify-between text-[#C28D58]" data-testid="cart-points-discount"><span>Pontos ({quote.points_used})</span><span>−{formatBRL(quote.points_discount)}</span></div>}
            <div className="flex justify-between font-serif text-xl text-[#F7F2EB] pt-2 border-t border-[#C28D58]/20"><span>Total</span><span data-testid="cart-total">{formatBRL(quote?.total ?? subtotal)}</span></div>
            {pointsCfg?.earn_enabled && quote && (
              <div className="text-[11px] text-[#A89B8C]" data-testid="cart-points-earn">
                Uma compra real aprovada renderia {Math.floor(Math.max(0, quote.subtotal - (quote.discount || 0) - (quote.points_discount || 0)) / pointsCfg.reais_per_point)} pontos do Clube (pedidos em demonstração não geram pontos).
              </div>
            )}
          </div>
          {suspendedForMe && (
            <div className="text-sm text-[#D8A36E] bg-[#5E1925]/30 border border-[#8A2436]/50 rounded-lg p-3 mb-4" data-testid="sales-suspended-banner">
              Vendas temporariamente suspensas. Pedidos já realizados seguem em processamento normal.
            </div>
          )}
          <button onClick={submit} disabled={loading || (user && (!!quoteError || suspendedForMe))} className="btn-primary w-full mt-5" data-testid="cart-checkout-btn">
            {loading ? "Processando..." : "Finalizar pedido"}
          </button>
          <p className="text-[10px] text-[#A89B8C] text-center mt-3">Pagamento processado por provedor especializado. Dados do cartão nunca passam pelos servidores da loja. Na entrega, será exigido documento com foto de um adulto (18+).</p>
        </div>
      </div>
    </div>
  );
}
