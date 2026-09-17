import { useEffect, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { api, formatBRL } from "@/lib/api";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { BarChart3, Wine, Package, TrendingUp, Ticket, Settings, Boxes, Users, Truck, Crown } from "lucide-react";
import AdminSubscriptions from "@/components/admin/AdminSubscriptions";

const EMPTY_WINE = { name: "", winery: "", country: "", region: "", type: "Tinto", grapes: [], alcohol: "", body: "", sweetness: "", image: "", pairing_notes: "", aromas: "", service_temp: "", featured: false, badge: "", archived: false, variants: [{ variant_id: "default", vintage: "", volume_ml: 750, sku: "", price: 0, discount_price: null, cost: 0, stock: 0 }] };

export default function AdminPage() {
  const { user, loading } = useAuth();
  const nav = useNavigate();
  const [tab, setTab] = useState("resumo");
  const [wines, setWines] = useState([]);
  const [orders, setOrders] = useState([]);
  const [stats, setStats] = useState(null);
  const [coupons, setCoupons] = useState([]);
  const [movements, setMovements] = useState([]);
  const [users, setUsers] = useState([]);
  const [settings, setSettings] = useState(null);
  const [editing, setEditing] = useState(null);
  const [couponForm, setCouponForm] = useState({ code: "", percent: 10, active: true, valid_until: "", max_uses: "" });
  const [stockForm, setStockForm] = useState({ wine_id: "", variant_id: "", qty: 0, reason: "", supplier: "" });
  const [zones, setZones] = useState([]);
  const [zoneForm, setZoneForm] = useState({ name: "", cep_prefixes: "", price: 0, deadline: "", active: true, confirmed: false });
  const [cronRuns, setCronRuns] = useState([]);
  const [backups, setBackups] = useState([]);
  const [secretsStatus, setSecretsStatus] = useState(null);
  const [secretsForm, setSecretsForm] = useState({ mp_access_token: "", mp_webhook_secret: "" });
  const [secretsSaving, setSecretsSaving] = useState(false);
  const [connTest, setConnTest] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [analyticsDays, setAnalyticsDays] = useState(30);

  const ROUTE_OPTIONS = [["/", "Início"], ["/catalogo", "Explorar Vinhos"], ["/harmonizar", "Harmonizar com Sommelier"], ["/carrinho", "Carrinho"], ["/conta", "Minha Conta"], ["/login", "Entrar"]];
  const HOME_TEXT_FIELDS = [
    ["hero_eyebrow", "Hero — selo acima do título"],
    ["hero_title_start", "Hero — título (início)"],
    ["hero_title_highlight", "Hero — título (destaque em cobre)"],
    ["hero_title_end", "Hero — título (final)"],
    ["hero_description", "Hero — descrição principal"],
    ["featured_eyebrow", "Seção de destaques — selo"],
    ["featured_title", "Seção de destaques — título"],
    ["cta_eyebrow", "Seção sommelier — selo"],
    ["cta_title", "Seção sommelier — título"],
    ["cta_description", "Seção sommelier — descrição"],
  ];
  const HOME_BUTTONS = [["hero_primary", "Hero — botão principal"], ["hero_secondary", "Hero — botão secundário"], ["cta_button", "Seção sommelier — botão"]];
  const HOME_CARD_FIELDS = [["card_badge", "Selo"], ["card_text", "Texto"], ["card_title", "Título"], ["card_description", "Descrição"], ["card_button_label", "Texto do botão"]];
  const FOOTER_FIELDS = [["about", "Apresentação da loja"], ["email", "E-mail de contato"], ["phone", "Telefone"], ["address", "Endereço / região"]];
  const setHomeField = (k, v) => setSettings((s) => ({ ...s, home: { ...(s?.home || {}), [k]: v } }));
  const setFooterField = (k, v) => setSettings((s) => ({ ...s, footer: { ...(s?.footer || {}), [k]: v } }));
  const setPointsField = (k, v) => setSettings((s) => ({ ...s, points: { ...(s?.points || {}), [k]: v } }));
  const setChatField = (k, v) => setSettings((s) => ({ ...s, chat: { ...(s?.chat || {}), [k]: v } }));

  const isAdmin = user && (user.role === "owner" || user.role === "staff");

  const reload = () => {
    api.get("/admin/wines-full").then((r) => setWines(r.data));
    api.get("/admin/stats").then((r) => setStats(r.data));
  };

  useEffect(() => {
    if (!loading && !isAdmin) nav("/");
    if (isAdmin) {
      reload();
      api.get("/admin/orders").then((r) => setOrders(r.data));
      api.get("/admin/coupons").then((r) => setCoupons(r.data));
      api.get("/admin/stock/movements").then((r) => setMovements(r.data));
      api.get("/settings").then((r) => setSettings(r.data));
      api.get("/admin/shipping").then((r) => setZones(r.data));
      api.get("/admin/cron-runs").then((r) => setCronRuns(r.data)).catch(() => {});
      api.get("/admin/backups").then((r) => setBackups(r.data)).catch(() => {});
      if (user.role === "owner") api.get("/admin/secrets/status").then((r) => setSecretsStatus(r.data)).catch(() => {});
      if (user.role === "owner") api.get("/admin/users").then((r) => setUsers(r.data));
    }
    // eslint-disable-next-line
  }, [user, loading, nav]);

  useEffect(() => {
    if (isAdmin && tab === "acessos") {
      api.get(`/admin/analytics?days=${analyticsDays}`).then((r) => setAnalytics(r.data)).catch(() => setAnalytics(null));
    }
    // eslint-disable-next-line
  }, [tab, analyticsDays, isAdmin]);

  const saveWine = async () => {
    try {
      const payload = { ...editing, grapes: typeof editing.grapes === "string" ? editing.grapes.split(",").map((s) => s.trim()) : editing.grapes };
      if (editing.wine_id) await api.put(`/admin/wines/${editing.wine_id}`, payload);
      else await api.post("/admin/wines", payload);
      toast.success("Vinho salvo");
      setEditing(null);
      reload();
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha ao salvar"); }
  };

  const archive = async (id) => {
    if (!confirm("Arquivar este vinho? Ele deixará de aparecer no catálogo, mas o histórico de pedidos é preservado.")) return;
    await api.delete(`/admin/wines/${id}`);
    reload();
  };

  const setStatus = async (order_id, field, value) => {
    await api.put(`/admin/orders/${order_id}/status`, { [field]: value });
    api.get("/admin/orders").then((r) => setOrders(r.data));
  };

  const saveCoupon = async () => {
    try {
      await api.post("/admin/coupons", {
        ...couponForm,
        percent: parseFloat(couponForm.percent),
        max_uses: couponForm.max_uses ? parseInt(couponForm.max_uses) : null,
        valid_until: couponForm.valid_until || null,
      });
      toast.success("Cupom salvo");
      setCouponForm({ code: "", percent: 10, active: true, valid_until: "", max_uses: "" });
      api.get("/admin/coupons").then((r) => setCoupons(r.data));
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha ao salvar cupom"); }
  };

  const submitStock = async (kind) => {
    try {
      await api.post(`/admin/stock/${kind}`, { ...stockForm, qty: parseInt(stockForm.qty), variant_id: stockForm.variant_id || null });
      toast.success(kind === "entry" ? "Entrada registrada" : "Ajuste registrado");
      api.get("/admin/stock/movements").then((r) => setMovements(r.data));
      reload();
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha no estoque"); }
  };

  const resolveStock = async (order_id, action) => {
    const reason = prompt(action === "fulfill" ? "Motivo da baixa manual (ex.: estoque localizado):" : "Motivo do reembolso:");
    if (!reason) return;
    try {
      const { data } = await api.post(`/admin/orders/${order_id}/resolve-stock`, { action, reason });
      toast.success(action === "fulfill" ? "Estoque baixado, pedido liberado para preparo" : `Marcado para reembolso. ${data.note || ""}`);
      api.get("/admin/orders").then((r) => setOrders(r.data));
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha na resolução"); }
  };

  const confirmRefund = async (order_id) => {
    try {
      await api.post(`/admin/orders/${order_id}/refund/confirm`);
      toast.success("Reembolso confirmado — cliente notificado");
      api.get("/admin/orders").then((r) => setOrders(r.data));
    } catch (err) { toast.error(err?.response?.data?.detail || "Provedor ainda não confirmou o estorno"); }
  };

  const consultPayment = async (order_id) => {
    try {
      const { data } = await api.post(`/admin/orders/${order_id}/consult-payment`);
      toast.success(`Consulta ao provedor: ${data.result}`);
      api.get("/admin/orders").then((r) => setOrders(r.data));
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha na consulta ao Mercado Pago"); }
  };

  const confirmZone = async (z) => {
    await api.post("/admin/shipping", { ...z, confirmed: true });
    toast.success(`Região "${z.name}" confirmada e ativa no checkout`);
    api.get("/admin/shipping").then((r) => setZones(r.data));
  };

  const verifyBackup = async (backup_id) => {
    try {
      const { data } = await api.post(`/admin/backups/${backup_id}/verify-restore`);
      toast.success(`Restauração externa OK: ${JSON.stringify(data.restored_counts)}`);
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha na verificação"); }
  };

  const saveZone = async () => {
    try {
      await api.post("/admin/shipping", {
        ...zoneForm,
        cep_prefixes: zoneForm.cep_prefixes.split(",").map((s) => s.trim()).filter(Boolean),
        price: parseFloat(zoneForm.price) || 0,
      });
      toast.success("Região salva");
      setZoneForm({ name: "", cep_prefixes: "", price: 0, deadline: "", active: true });
      api.get("/admin/shipping").then((r) => setZones(r.data));
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha ao salvar região"); }
  };

  const saveSecrets = async () => {
    setSecretsSaving(true);
    try {
      const payload = {};
      if (secretsForm.mp_access_token) payload.mp_access_token = secretsForm.mp_access_token;
      if (secretsForm.mp_webhook_secret) payload.mp_webhook_secret = secretsForm.mp_webhook_secret;
      const { data } = await api.post("/admin/secrets", payload);
      setSecretsForm({ mp_access_token: "", mp_webhook_secret: "" });
      api.get("/admin/secrets/status").then((r) => setSecretsStatus(r.data));
      api.get("/settings").then((r) => setSettings(r.data));
      setConnTest(null);
      toast.success("Credenciais salvas com segurança. Use 'Testar credencial' para validar no sandbox.");
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha ao salvar"); }
    finally { setSecretsSaving(false); }
  };

  const testConnection = async () => {
    setConnTest({ loading: true });
    try {
      const { data } = await api.post("/admin/secrets/test-connection");
      setConnTest(data);
      data.credential_accepted_for_preferences
        ? toast.success("Credencial aceita para criar preferências no sandbox")
        : toast.error(data.detail);
    } catch (err) {
      setConnTest(null);
      toast.error(err?.response?.data?.detail || "Falha no teste");
    }
  };

  const saveSettings = async () => {
    try {
      await api.put("/admin/settings", { store_name: settings.store_name, tagline: settings.tagline, home: settings.home, footer: settings.footer, payment_mode: settings.payment_mode, sales_status: settings.sales_status, points: settings.points, chat: settings.chat });
      toast.success("Configurações salvas");
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha ao salvar"); }
  };

  const setRole = async (email, role) => {
    try {
      await api.post("/admin/staff", { email, role });
      toast.success("Papel atualizado (usuário deverá entrar novamente)");
      api.get("/admin/users").then((r) => setUsers(r.data));
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha"); }
  };

  if (!isAdmin) return <div className="text-[#A89B8C]">Carregando...</div>;

  const TABS = [
    { id: "resumo", label: "Visão Geral", icon: BarChart3 },
    { id: "produtos", label: "Catálogo", icon: Wine },
    { id: "pedidos", label: "Pedidos", icon: Package },
    { id: "estoque", label: "Estoque", icon: Boxes },
    { id: "frete", label: "Frete & Entrega", icon: Truck },
    { id: "cupons", label: "Cupons", icon: Ticket },
    { id: "assinaturas", label: "Assinaturas", icon: Crown },
    { id: "acessos", label: "Acessos", icon: BarChart3 },
    { id: "relatorios", label: "Relatórios", icon: TrendingUp },
    ...(user.role === "owner" ? [{ id: "equipe", label: "Equipe", icon: Users }, { id: "config", label: "Configurações", icon: Settings }] : []),
  ];

  return (
    <div className="fade-up">
      <div className="mb-8">
        <div className="eyebrow mb-2">Painel do Sommelier</div>
        <h1 className="font-serif text-4xl md:text-5xl text-[#F7F2EB]">Painel Administrativo</h1>
      </div>

      <div className="flex flex-wrap gap-2 mb-8 border-b border-[#C28D58]/20 pb-3">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button key={id} onClick={() => setTab(id)} data-testid={`admin-tab-${id}`}
            className={`px-4 py-2 rounded-full text-sm inline-flex items-center gap-2 border transition-colors ${tab === id ? "bg-[#5E1925]/40 text-[#F7F2EB] border-[#C28D58]/60" : "text-[#A89B8C] border-transparent hover:text-[#F7F2EB]"}`}>
            <Icon className="w-4 h-4" /> {label}
          </button>
        ))}
      </div>

      {tab === "resumo" && stats && (
        <div className="grid md:grid-cols-4 gap-4 mb-8">
          <MetricCard label="Receita real (aprovada)" value={formatBRL(stats.real.revenue)} testId="stat-revenue" />
          <MetricCard label="Pedidos pagos (reais)" value={stats.real.orders_paid} testId="stat-orders" />
          <MetricCard label="Ticket médio (real)" value={formatBRL(stats.real.avg_ticket)} testId="stat-avg-ticket" />
          <MetricCard label="Receita demo" value={formatBRL(stats.demo.revenue)} testId="stat-demo-revenue" />
          {stats.low_stock.length > 0 && (
            <div className="surface p-5 md:col-span-4">
              <div className="eyebrow mb-2">⚠️ Estoque baixo (menos de 5 disponíveis)</div>
              <ul className="text-sm text-[#D5C7B7] space-y-1">
                {stats.low_stock.map((w, i) => <li key={i}>{w.name} ({w.vintage}) — {w.available} un.</li>)}
              </ul>
            </div>
          )}
        </div>
      )}

      {tab === "produtos" && (
        <div>
          <div className="flex justify-between mb-4">
            <h2 className="font-serif text-2xl text-[#F7F2EB]">Catálogo</h2>
            <button className="btn-primary" onClick={() => setEditing(JSON.parse(JSON.stringify(EMPTY_WINE)))} data-testid="admin-new-wine-btn">+ Novo vinho</button>
          </div>
          <div className="surface overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-[#A89B8C] text-xs uppercase tracking-wide">
                <tr><th className="p-3">Vinho</th><th className="p-3">Variantes</th><th className="p-3">Preço</th><th className="p-3">Estoque</th><th className="p-3">Ações</th></tr>
              </thead>
              <tbody>
                {wines.map((w) => (
                  <tr key={w.wine_id} className="border-t border-[#C28D58]/10" data-testid={`admin-wine-row-${w.wine_id}`}>
                    <td className="p-3">
                      <div className="text-[#F7F2EB]">{w.name} {w.archived && <span className="text-xs text-[#A89B8C]">(arquivado)</span>}</div>
                      <div className="text-xs text-[#A89B8C]">{w.winery}</div>
                    </td>
                    <td className="p-3 text-xs text-[#D5C7B7]">
                      {(w.variants || []).map((v) => (
                        <div key={v.variant_id}>{v.vintage || "sem safra"} · {v.volume_ml}ml · {v.stock - (v.reserved || 0)} disp.</div>
                      ))}
                    </td>
                    <td className="p-3 text-[#C28D58]">{formatBRL(w.discount_price || w.price)}</td>
                    <td className="p-3 text-[#D5C7B7]">{w.stock}</td>
                    <td className="p-3 space-x-2">
                      <button className="text-[#C28D58] hover:underline text-xs" onClick={() => setEditing(JSON.parse(JSON.stringify(w)))} data-testid={`admin-edit-${w.wine_id}`}>Editar</button>
                      {!w.archived && <button className="text-[#8A2436] hover:underline text-xs" onClick={() => archive(w.wine_id)}>Arquivar</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "pedidos" && (
        <div className="space-y-3" data-testid="admin-orders-list">
          {orders.length === 0 && <div className="surface p-8 text-[#A89B8C]">Nenhum pedido ainda.</div>}
          {orders.map((o) => (
            <div key={o.order_id} className="surface p-5">
              <div className="flex flex-wrap justify-between mb-2">
                <div>
                  <div className="eyebrow">{o.order_id.slice(-6).toUpperCase()} {o.demo && <span className="text-[#A89B8C]">(demo)</span>}</div>
                  <div className="text-xs text-[#A89B8C]">{new Date(o.created_at).toLocaleString("pt-BR")} · {o.user_email}</div>
                  {o.needs_stock_review && <div className="text-xs text-[#D8A36E] mt-1">⚠️ Pagamento aprovado após expiração da reserva — revisar estoque</div>}
                </div>
                <div className="text-[#C28D58] font-serif text-lg">{formatBRL(o.quote.total)}</div>
              </div>
              {o.needs_stock_review && (
                <div className="surface p-3 mb-3 border-[#D8A36E]/50" data-testid={`stock-review-${o.order_id}`}>
                  <p className="text-xs text-[#D8A36E] mb-2">Pagamento aprovado após a expiração da reserva. O envio está bloqueado até resolver: baixe o estoque se houver disponibilidade ou marque para reembolso.</p>
                  <div className="flex gap-2">
                    <button className="btn-copper text-xs" onClick={() => resolveStock(o.order_id, "fulfill")} data-testid={`resolve-fulfill-${o.order_id}`}>Baixar estoque e enviar</button>
                    <button className="btn-ghost text-xs !border-[#8A2436] text-[#D88A96]" onClick={() => resolveStock(o.order_id, "refund")} data-testid={`resolve-refund-${o.order_id}`}>Reembolsar</button>
                  </div>
                </div>
              )}
              <div className="text-sm text-[#D5C7B7] mb-3">{o.quote.lines.map((l) => `${l.qty}× ${l.name}${l.vintage ? ` (${l.vintage})` : ""}`).join(" • ")}</div>
              {o.payment_status === "refund_requested" && (
                <div className="surface p-3 mb-3 border-[#D8A36E]/50" data-testid={`refund-pending-${o.order_id}`}>
                  <p className="text-xs text-[#D8A36E] mb-1">
                    Reembolso solicitado{o.refund?.partial ? ` (parcial ${formatBRL(o.refund.amount)})` : ` (${formatBRL(o.refund?.amount || o.quote.total)})`} — status no provedor: {o.refund?.status || "requested"}.
                    O cliente só será avisado após a confirmação.
                  </p>
                  <button className="btn-copper text-xs" onClick={() => confirmRefund(o.order_id)} data-testid={`refund-confirm-${o.order_id}`}>Confirmar estorno</button>
                </div>
              )}
              {o.payment_status === "refunded" && o.refund?.confirmed_at && (
                <p className="text-[11px] text-[#A89B8C] mb-2">Reembolsado em {new Date(o.refund.confirmed_at).toLocaleString("pt-BR")}{o.refund?.provider_refund_id ? ` · id ${o.refund.provider_refund_id}` : ""} · {formatBRL(o.refund.amount || o.quote.total)}</p>
              )}
              <div className="flex gap-2 flex-wrap">
                <select value={o.payment_status} onChange={(e) => setStatus(o.order_id, "payment_status", e.target.value)} className="input-cellar text-xs" data-testid={`admin-payment-${o.order_id}`}>
                  <option value="pending">Aguardando pagamento</option><option value="in_process">Em análise</option><option value="approved">Aprovado</option><option value="rejected">Recusado</option><option value="expired">Expirado</option><option value="cancelled">Cancelado</option><option value="refund_requested">Reembolso solicitado</option><option value="refunded">Reembolsado</option>
                </select>
                <select value={o.fulfillment_status} onChange={(e) => setStatus(o.order_id, "fulfillment_status", e.target.value)} className="input-cellar text-xs" data-testid={`admin-fulfill-${o.order_id}`}>
                  <option value="aguardando_pagamento">Aguardando pagamento</option>
                  <option value="preparando">Preparando</option>
                  <option value="enviado">Enviado</option>
                  <option value="entregue">Entregue</option>
                  <option value="cancelado">Cancelado</option>
                </select>
              </div>
              {!o.demo && user?.role === "owner" && (
                <div className="mt-3 pt-3 border-t border-[#C28D58]/15 space-y-1" data-testid={`payment-check-${o.order_id}`}>
                  {["pending", "in_process", "expired"].includes(o.payment_status) && (
                    <button className="btn-copper text-xs" onClick={() => consultPayment(o.order_id)} data-testid={`consult-payment-${o.order_id}`}>
                      Consultar pagamento no Mercado Pago
                    </button>
                  )}
                  {o.payment_check?.at && (
                    <p className="text-[11px] text-[#A89B8C]">
                      Última consulta: {new Date(o.payment_check.at).toLocaleString("pt-BR")} · resultado: {o.payment_check.result} · origem: {o.payment_check.via}
                    </p>
                  )}
                  {o.paid_via && o.payment_status === "approved" && (
                    <p className="text-[11px] text-[#A89B8C]">Confirmação: {o.paid_via === "webhook" ? "webhook" : o.paid_via === "reconciliacao" ? "consulta automática à API" : o.paid_via === "consulta_manual" ? "consulta manual à API" : o.paid_via}</p>
                  )}
                  {o.payment_review?.needed && (
                    <p className="text-[11px] text-[#D8A36E]" data-testid={`payment-review-${o.order_id}`}>⚠ Revisão necessária: {o.payment_review.reason}</p>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {tab === "estoque" && (
        <div className="grid lg:grid-cols-2 gap-6">
          <div className="surface p-6">
            <h3 className="font-serif text-xl text-[#F7F2EB] mb-4">Entrada / ajuste</h3>
            <div className="space-y-3">
              <select className="input-cellar" value={stockForm.wine_id} onChange={(e) => setStockForm({ ...stockForm, wine_id: e.target.value })} data-testid="stock-wine-select">
                <option value="">Selecione o vinho</option>
                {wines.filter((w) => !w.archived).map((w) => <option key={w.wine_id} value={w.wine_id}>{w.name}</option>)}
              </select>
              <input className="input-cellar" placeholder="Quantidade (negativo para ajuste de baixa)" type="number" value={stockForm.qty} onChange={(e) => setStockForm({ ...stockForm, qty: e.target.value })} data-testid="stock-qty" />
              <input className="input-cellar" placeholder="Motivo (obrigatório)" value={stockForm.reason} onChange={(e) => setStockForm({ ...stockForm, reason: e.target.value })} data-testid="stock-reason" />
              <input className="input-cellar" placeholder="Fornecedor (entrada)" value={stockForm.supplier} onChange={(e) => setStockForm({ ...stockForm, supplier: e.target.value })} data-testid="stock-supplier" />
              <div className="flex gap-2">
                <button className="btn-copper" onClick={() => submitStock("entry")} data-testid="stock-entry-btn">Registrar entrada</button>
                <button className="btn-ghost" onClick={() => submitStock("adjust")} data-testid="stock-adjust-btn">Registrar ajuste</button>
              </div>
            </div>
          </div>
          <div className="surface p-6">
            <h3 className="font-serif text-xl text-[#F7F2EB] mb-4">Movimentações recentes</h3>
            <div className="space-y-2 max-h-96 overflow-auto text-xs" data-testid="stock-movements">
              {movements.map((m) => (
                <div key={m.movement_id} className="flex justify-between border-b border-[#C28D58]/10 pb-1">
                  <span className="text-[#D5C7B7]">{m.type} · {m.qty > 0 ? "+" : ""}{m.qty} · {m.reason}</span>
                  <span className="text-[#A89B8C]">{new Date(m.created_at).toLocaleDateString("pt-BR")}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {tab === "frete" && (
        <div className="grid lg:grid-cols-2 gap-6">
          <div className="surface p-6">
            <h3 className="font-serif text-xl text-[#F7F2EB] mb-2">Regiões atendidas</h3>
            <p className="text-xs text-[#A89B8C] mb-4">Tabela própria da loja. CEPs sem região configurada têm a compra bloqueada no checkout. Prazos e preços devem refletir a operação real.</p>
            <div className="space-y-3">
              <input className="input-cellar" placeholder="Nome da região" value={zoneForm.name} onChange={(e) => setZoneForm({ ...zoneForm, name: e.target.value })} data-testid="zone-name" />
              <input className="input-cellar" placeholder="Prefixos de CEP (ex.: 01, 02, 13)" value={zoneForm.cep_prefixes} onChange={(e) => setZoneForm({ ...zoneForm, cep_prefixes: e.target.value })} data-testid="zone-prefixes" />
              <input className="input-cellar" type="number" placeholder="Preço do frete (R$)" value={zoneForm.price} onChange={(e) => setZoneForm({ ...zoneForm, price: e.target.value })} data-testid="zone-price" />
              <input className="input-cellar" placeholder="Prazo real (ex.: 2 a 4 dias úteis)" value={zoneForm.deadline} onChange={(e) => setZoneForm({ ...zoneForm, deadline: e.target.value })} data-testid="zone-deadline" />
              <button className="btn-primary" onClick={saveZone} data-testid="zone-save-btn">Salvar região</button>
            </div>
          </div>
          <div className="space-y-4">
            <div className="surface p-6">
              <h3 className="font-serif text-xl text-[#F7F2EB] mb-4">Regiões configuradas</h3>
              <div className="space-y-2 text-sm" data-testid="zone-list">
                {zones.map((z) => (
                  <div key={z.zone_id} className="flex justify-between items-start border-b border-[#C28D58]/10 pb-2">
                    <div>
                      <div className="text-[#F7F2EB]">{z.name} {!z.confirmed && <span className="text-[10px] text-[#D8A36E]">(exemplo — aguardando sua confirmação)</span>}</div>
                      <div className="text-xs text-[#A89B8C]">CEPs {z.cep_prefixes.join(", ")}… · {formatBRL(z.price)} · {z.deadline || "prazo não informado"}</div>
                    </div>
                    <div className="flex gap-3">
                      {!z.confirmed && <button className="text-[#C28D58] text-xs hover:underline" onClick={() => confirmZone(z)} data-testid={`zone-confirm-${z.zone_id}`}>Confirmar</button>}
                      <button className="text-[#8A2436] text-xs hover:underline" onClick={async () => { await api.delete(`/admin/shipping/${z.zone_id}`); api.get("/admin/shipping").then((r) => setZones(r.data)); }}>Excluir</button>
                    </div>
                  </div>
                ))}
                {zones.length === 0 && <p className="text-[#A89B8C] text-sm">Nenhuma região — o checkout ficará bloqueado até configurar e confirmar.</p>}
              </div>
            </div>
            <div className="surface p-6">
              <h3 className="font-serif text-lg text-[#F7F2EB] mb-3">Operação de entrega (maioridade)</h3>
              <ul className="text-xs text-[#D5C7B7] space-y-2 list-disc pl-4">
                <li>A data de nascimento coletada no cadastro/checkout é uma <strong>declaração</strong>, não verificação documental.</li>
                <li>Na entrega, o transportador deve exigir documento com foto de um adulto (18+) e recusar a entrega sem ele.</li>
                <li>Entrega recusada por impossibilidade de verificar maioridade: registrar no pedido (status "cancelado"), restituir o valor e lançar a mercadoria de volta ao estoque via Estoque → Entrada, com motivo "devolução — entrega recusada".</li>
                <li>Nunca deixar a encomenda com menores ou em local sem recebedor identificado.</li>
              </ul>
            </div>
            <div className="surface p-6">
              <h3 className="font-serif text-lg text-[#F7F2EB] mb-3">Backups (armazenamento externo)</h3>
              <p className="text-xs text-[#A89B8C] mb-3">Backups diários são enviados ao armazenamento de objetos externo (fora do disco do pod). Acesso restrito a administradores; falhas aparecem no monitoramento acima.</p>
              <div className="space-y-1 text-xs max-h-40 overflow-auto" data-testid="backup-list">
                {backups.length === 0 && <p className="text-[#A89B8C]">Nenhum backup registrado ainda.</p>}
                {backups.map((b) => (
                  <div key={b.backup_id} className="flex justify-between items-center border-b border-[#C28D58]/10 pb-1">
                    <span className="text-[#D5C7B7]">{new Date(b.created_at).toLocaleString("pt-BR")} · {(b.size_bytes/1024).toFixed(0)} KB</span>
                    <button className="text-[#C28D58] hover:underline" onClick={() => verifyBackup(b.backup_id)} data-testid={`backup-verify-${b.backup_id}`}>Testar restauração</button>
                  </div>
                ))}
              </div>
            </div>
            <div className="surface p-6">
              <h3 className="font-serif text-lg text-[#F7F2EB] mb-3">Tarefas automáticas (monitoramento)</h3>
              <div className="space-y-1 text-xs max-h-40 overflow-auto" data-testid="cron-runs">
                {cronRuns.length === 0 && <p className="text-[#A89B8C]">Nenhuma execução registrada ainda.</p>}
                {cronRuns.map((c) => (
                  <div key={c.run_id} className="flex justify-between border-b border-[#C28D58]/10 pb-1">
                    <span className="text-[#D5C7B7]">{c.job} — {c.status === "ok" ? "ok" : `⚠️ ${c.status}`}</span>
                    <span className="text-[#A89B8C]">{new Date(c.at).toLocaleString("pt-BR")}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {tab === "assinaturas" && <AdminSubscriptions user={user} />}
      {tab === "cupons" && (        <div className="grid lg:grid-cols-2 gap-6">
          <div className="surface p-6">
            <h3 className="font-serif text-xl text-[#F7F2EB] mb-4">Novo cupom</h3>
            <div className="space-y-3">
              <input className="input-cellar" placeholder="Código (ex.: PRIMEIRAADEGA)" value={couponForm.code} onChange={(e) => setCouponForm({ ...couponForm, code: e.target.value.toUpperCase() })} data-testid="coupon-code" />
              <input className="input-cellar" type="number" placeholder="% de desconto" value={couponForm.percent} onChange={(e) => setCouponForm({ ...couponForm, percent: e.target.value })} data-testid="coupon-percent" />
              <div>
                <label className="text-xs text-[#A89B8C] block mb-1">Válido até (opcional)</label>
                <input className="input-cellar" type="date" value={couponForm.valid_until} onChange={(e) => setCouponForm({ ...couponForm, valid_until: e.target.value })} data-testid="coupon-valid-until" />
              </div>
              <input className="input-cellar" type="number" placeholder="Limite de usos (opcional)" value={couponForm.max_uses} onChange={(e) => setCouponForm({ ...couponForm, max_uses: e.target.value })} data-testid="coupon-max-uses" />
              <button className="btn-primary" onClick={saveCoupon} data-testid="coupon-save-btn">Salvar cupom</button>
            </div>
          </div>
          <div className="surface p-6">
            <h3 className="font-serif text-xl text-[#F7F2EB] mb-4">Cupons ativos</h3>
            <div className="space-y-2 text-sm" data-testid="coupon-list">
              {coupons.map((c) => (
                <div key={c.code} className="flex justify-between items-center border-b border-[#C28D58]/10 pb-2">
                  <div>
                    <span className="text-[#C28D58] font-mono">{c.code}</span>
                    <span className="text-[#D5C7B7] ml-2">{c.percent}% off</span>
                    <div className="text-xs text-[#A89B8C]">usos: {c.uses_count}{c.max_uses ? `/${c.max_uses}` : ""} {c.valid_until ? `· até ${c.valid_until}` : ""}</div>
                  </div>
                  <button className="text-[#8A2436] text-xs hover:underline" onClick={async () => { await api.delete(`/admin/coupons/${c.code}`); api.get("/admin/coupons").then((r) => setCoupons(r.data)); }}>Excluir</button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {tab === "acessos" && (
        <div data-testid="analytics-panel">
          <div className="flex gap-2 mb-6">
            {[7, 30, 90].map((d) => (
              <button key={d} onClick={() => setAnalyticsDays(d)} data-testid={`analytics-days-${d}`}
                className={`px-4 py-2 rounded-full text-sm border transition-colors ${analyticsDays === d ? "bg-[#5E1925]/40 text-[#F7F2EB] border-[#C28D58]/60" : "text-[#A89B8C] border-transparent hover:text-[#F7F2EB]"}`}>
                {d} dias
              </button>
            ))}
          </div>
          {!analytics ? <div className="text-[#A89B8C]">Carregando...</div> : (
            <>
              <div className="grid md:grid-cols-3 gap-4 mb-4">
                <MetricCard label="Visitas (sessões únicas)" value={analytics.unique_sessions} testId="an-sessions" />
                <MetricCard label="Páginas vistas" value={analytics.pageviews} testId="an-pageviews" />
                <MetricCard label="Adições ao carrinho" value={analytics.add_to_cart} testId="an-cart" />
                <MetricCard label="Checkouts iniciados" value={analytics.checkout_starts} testId="an-checkouts" />
                <MetricCard label="Compras confirmadas (reais)" value={analytics.purchases} testId="an-purchases" />
                <MetricCard label="Receita no período (real)" value={formatBRL(analytics.revenue_real)} testId="an-revenue" />
              </div>
              <div className="surface p-6 mb-4" data-testid="an-funnel">
                <div className="eyebrow mb-3">Funil de conversão ({analytics.days} dias)</div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
                  {[["Visita → Carrinho", analytics.funnel.visita_para_carrinho], ["Carrinho → Checkout", analytics.funnel.carrinho_para_checkout], ["Checkout → Compra", analytics.funnel.checkout_para_compra], ["Visita → Compra", analytics.funnel.visita_para_compra]].map(([l, v]) => (
                    <div key={l}><div className="font-serif text-2xl text-[#C28D58]">{v}%</div><div className="text-xs text-[#A89B8C]">{l}</div></div>
                  ))}
                </div>
                {analytics.purchases_demo > 0 && <p className="text-[11px] text-[#A89B8C] mt-3">{analytics.purchases_demo} compra(s) em modo demonstração não entram na conversão real.</p>}
              </div>
              <div className="grid md:grid-cols-3 gap-4 mb-4">
                <MetricCard label="Cadastros no período (indicador separado)" value={analytics.signups} testId="an-signups" />
                <div className="surface p-5 md:col-span-2" data-testid="an-top-wines">
                  <div className="eyebrow mb-2">Vinhos mais visitados</div>
                  {analytics.top_wines.length === 0 && <p className="text-xs text-[#A89B8C]">Sem dados no período.</p>}
                  {analytics.top_wines.map((w) => (
                    <div key={w.wine_id} className="flex justify-between text-sm border-b border-[#C28D58]/10 py-1">
                      <span className="text-[#D5C7B7]">{w.name}</span><span className="text-[#C28D58]">{w.views}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div className="surface p-6 mb-4" data-testid="an-top-pages">
                <div className="eyebrow mb-2">Páginas mais visitadas</div>
                {analytics.top_pages.length === 0 && <p className="text-xs text-[#A89B8C]">Sem dados no período.</p>}
                {analytics.top_pages.map((p) => (
                  <div key={p.path} className="flex justify-between text-sm border-b border-[#C28D58]/10 py-1">
                    <span className="text-[#D5C7B7] font-mono text-xs">{p.path}</span><span className="text-[#C28D58]">{p.views}</span>
                  </div>
                ))}
              </div>
              <p className="text-[11px] text-[#A89B8C] max-w-3xl" data-testid="an-privacy-note">
                {analytics.privacy_note} Retenção: {analytics.retention_days} dias — eventos mais antigos são apagados automaticamente.
              </p>
            </>
          )}
        </div>
      )}

      {tab === "relatorios" && stats && (
        <div>
          <p className="text-sm text-[#A89B8C] mb-4 max-w-2xl">{stats.note}</p>
          <div className="grid md:grid-cols-3 gap-4">
            <MetricCard label="Receita real" value={formatBRL(stats.real.revenue)} testId="rep-revenue" />
            <MetricCard label="Pedidos reais pagos" value={stats.real.orders_paid} testId="rep-orders" />
            <MetricCard label="Cancelados/expirados (reais)" value={stats.real.orders_cancelled} testId="rep-cancelled" />
            <MetricCard label="Reembolsados (reais)" value={stats.real.orders_refunded} testId="rep-refunded" />
            <MetricCard label="Receita demo (separada)" value={formatBRL(stats.demo.revenue)} testId="rep-demo" />
            <MetricCard label="Ticket médio real" value={formatBRL(stats.real.avg_ticket)} testId="rep-ticket" />
          </div>
          <div className="surface p-6 mt-4">
            <div className="eyebrow mb-3">O que este relatório mostra</div>
            <ul className="text-sm text-[#D5C7B7] space-y-1 list-disc pl-5">
              <li>Receita conta apenas pedidos com pagamento confirmado pelo provedor (status "aprovado").</li>
              <li>Pedidos de demonstração são exibidos separadamente e nunca somados à receita real.</li>
              <li>Cancelamentos, recusas e reembolsos não compõem a receita.</li>
              <li>Relatórios por período, produto e cliente serão adicionados na próxima etapa.</li>
            </ul>
          </div>
        </div>
      )}

      {tab === "equipe" && user.role === "owner" && (
        <div className="surface p-6 max-w-2xl" data-testid="staff-panel">
          <h3 className="font-serif text-xl text-[#F7F2EB] mb-2">Equipe e permissões</h3>
          <p className="text-xs text-[#A89B8C] mb-4">Somente o proprietário concede acesso administrativo. Funcionários com papel "staff" precisarão configurar MFA no próximo login.</p>
          <table className="w-full text-sm">
            <thead className="text-left text-[#A89B8C] text-xs uppercase"><tr><th className="p-2">E-mail</th><th className="p-2">Papel</th><th className="p-2">Ação</th></tr></thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.user_id} className="border-t border-[#C28D58]/10">
                  <td className="p-2 text-[#D5C7B7]">{u.email}</td>
                  <td className="p-2 text-[#C28D58]">{u.role}</td>
                  <td className="p-2">
                    {u.user_id !== user.user_id && (
                      <select value={u.role} onChange={(e) => setRole(u.email, e.target.value)} className="input-cellar text-xs" data-testid={`staff-role-${u.user_id}`}>
                        <option value="customer">Cliente</option>
                        <option value="staff">Funcionário (admin)</option>
                      </select>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "config" && user.role === "owner" && settings && (
        <div className="surface p-6 max-w-xl space-y-4" data-testid="settings-panel">
          <h3 className="font-serif text-xl text-[#F7F2EB]">Configurações da loja</h3>
          <div>
            <label className="eyebrow mb-1 block">Nome da loja</label>
            <input className="input-cellar" value={settings.store_name} onChange={(e) => setSettings({ ...settings, store_name: e.target.value })} data-testid="settings-store-name" />
          </div>
          <div>
            <label className="eyebrow mb-1 block">Tagline (exibida abaixo do nome)</label>
            <input className="input-cellar" value={settings.tagline || ""} onChange={(e) => setSettings({ ...settings, tagline: e.target.value })} data-testid="settings-tagline" />
          </div>
          <div className="border-t border-[#C28D58]/20 pt-4 space-y-3" data-testid="home-content-editor">
            <h4 className="font-serif text-lg text-[#F7F2EB]">Conteúdo da página inicial</h4>
            {HOME_TEXT_FIELDS.map(([k, label]) => (
              <div key={k}>
                <label className="eyebrow mb-1 block">{label}</label>
                {k.includes("description") ? (
                  <textarea className="input-cellar" rows={2} value={settings.home?.[k] || ""} onChange={(e) => setHomeField(k, e.target.value)} data-testid={`home-field-${k}`} />
                ) : (
                  <input className="input-cellar" value={settings.home?.[k] || ""} onChange={(e) => setHomeField(k, e.target.value)} data-testid={`home-field-${k}`} />
                )}
              </div>
            ))}
            {HOME_BUTTONS.map(([prefix, label]) => (
              <div key={prefix} className="border border-[#C28D58]/15 rounded-xl p-3 space-y-2" data-testid={`home-button-${prefix}`}>
                <div className="eyebrow">{label}</div>
                <div>
                  <label className="eyebrow mb-1 block">Texto do botão</label>
                  <input className="input-cellar" value={settings.home?.[`${prefix}_label`] || ""} onChange={(e) => setHomeField(`${prefix}_label`, e.target.value)} data-testid={`home-field-${prefix}-label`} />
                </div>
                <div>
                  <label className="eyebrow mb-1 block">Destino do botão</label>
                  <select className="input-cellar" value={settings.home?.[`${prefix}_href`] || "/"} onChange={(e) => setHomeField(`${prefix}_href`, e.target.value)} data-testid={`home-field-${prefix}-href`}>
                    {ROUTE_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </div>
              </div>
            ))}
            <div className="border border-[#C28D58]/15 rounded-xl p-3 space-y-2" data-testid="home-card-editor">
              <div className="eyebrow">Cartão sobre a imagem do hero</div>
              {HOME_CARD_FIELDS.map(([k, label]) => (
                <div key={k}>
                  <label className="eyebrow mb-1 block">{label}</label>
                  {k === "card_description" ? (
                    <textarea className="input-cellar" rows={2} value={settings.home?.[k] || ""} onChange={(e) => setHomeField(k, e.target.value)} data-testid={`home-field-${k}`} />
                  ) : (
                    <input className="input-cellar" value={settings.home?.[k] || ""} onChange={(e) => setHomeField(k, e.target.value)} data-testid={`home-field-${k}`} />
                  )}
                </div>
              ))}
            </div>
          </div>
          <div className="border-t border-[#C28D58]/20 pt-4 space-y-3" data-testid="footer-content-editor">
            <h4 className="font-serif text-lg text-[#F7F2EB]">Rodapé — apresentação e contato</h4>
            {FOOTER_FIELDS.map(([k, label]) => (
              <div key={k}>
                <label className="eyebrow mb-1 block">{label}</label>
                {k === "about" ? (
                  <textarea className="input-cellar" rows={2} value={settings.footer?.[k] || ""} onChange={(e) => setFooterField(k, e.target.value)} data-testid={`footer-field-${k}`} />
                ) : (
                  <input className="input-cellar" value={settings.footer?.[k] || ""} onChange={(e) => setFooterField(k, e.target.value)} data-testid={`footer-field-${k}`} />
                )}
              </div>
            ))}
          </div>
          <div>
            <label className="eyebrow mb-1 block">Status das vendas</label>
            <select className="input-cellar" value={settings.sales_status || "open"} onChange={(e) => setSettings({ ...settings, sales_status: e.target.value })} data-testid="settings-sales-status">
              <option value="open">Abertas</option>
              <option value="suspended">Suspensas (bloqueia novos checkouts; pedidos em andamento e webhooks seguem processando)</option>
            </select>
          </div>
          <div>
            <label className="eyebrow mb-1 block">Modo de pagamento</label>
            <select className="input-cellar" value={settings.payment_mode} onChange={(e) => setSettings({ ...settings, payment_mode: e.target.value })} data-testid="settings-payment-mode">
              <option value="demo">Demonstração (sem cobrança real)</option>
              <option value="mercadopago_test">Mercado Pago — ambiente de testes</option>
              <option value="mercadopago_live">Mercado Pago — produção (requer aprovação)</option>
            </select>
            {!settings.mp_configured && settings.payment_mode !== "demo" && (
              <p className="text-xs text-[#D8A36E] mt-2">⚠️ Credenciais do Mercado Pago não configuradas no servidor. A loja recusará checkouts até que MP_ACCESS_TOKEN e MP_WEBHOOK_SECRET sejam definidos.</p>
            )}
          </div>
          <div className="border-t border-[#C28D58]/20 pt-4 space-y-3" data-testid="chat-config">
            <h4 className="font-serif text-lg text-[#F7F2EB]">Atendimento (Sommelier Virtual)</h4>
            <p className="text-[11px] text-[#A89B8C]">
              Custos: cada mensagem do cliente gera 1 chamada de texto (Claude Sonnet 5) usando a Chave Universal Emergent,
              debitada do saldo da Universal Key (Perfil → Gerenciar plano → Universal Key). Ao modelo vai apenas o mínimo:
              catálogo resumido, frete aprovado, regras do Clube e estes textos — nunca senhas ou dados de pagamento.
              Conversas retidas por 90 dias e apagadas automaticamente. O encaminhamento humano (WhatsApp) funciona sempre,
              mesmo com o Sommelier Virtual desativado ou indisponível.
            </p>
            <label className="flex items-center gap-2 text-sm text-[#D5C7B7]">
              <input type="checkbox" checked={!!settings.chat?.enabled} onChange={(e) => setChatField("enabled", e.target.checked)} data-testid="chat-enabled" />
              Ativar Sommelier Virtual (entregue desativado — ative após revisar o limite)
            </label>
            <div>
              <label className="eyebrow mb-1 block">Saudação inicial</label>
              <textarea className="input-cellar" rows={2} value={settings.chat?.greeting ?? ""} onChange={(e) => setChatField("greeting", e.target.value)} data-testid="chat-greeting" />
            </div>
            <div>
              <label className="eyebrow mb-1 block">Informações de atendimento (horários, canais)</label>
              <textarea className="input-cellar" rows={2} value={settings.chat?.atendimento_info ?? ""} onChange={(e) => setChatField("atendimento_info", e.target.value)} data-testid="chat-info" />
            </div>
            <div>
              <label className="eyebrow mb-1 block">Perguntas frequentes (texto livre — uma por linha: pergunta — resposta)</label>
              <textarea className="input-cellar" rows={3} value={settings.chat?.faqs ?? ""} onChange={(e) => setChatField("faqs", e.target.value)} data-testid="chat-faqs" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="eyebrow mb-1 block">WhatsApp de encaminhamento (DDI+DDD+número)</label>
                <input className="input-cellar" value={settings.chat?.whatsapp_number ?? ""} onChange={(e) => setChatField("whatsapp_number", e.target.value)} data-testid="chat-whatsapp" />
              </div>
              <div>
                <label className="eyebrow mb-1 block">Limite de mensagens por conversa</label>
                <input className="input-cellar" type="number" min="1" max="200" value={settings.chat?.max_messages_per_session ?? 20} onChange={(e) => setChatField("max_messages_per_session", parseInt(e.target.value) || 20)} data-testid="chat-max-msgs" />
              </div>
            </div>
            <div>
              <label className="eyebrow mb-1 block">Instagram (URL HTTPS do perfil — vazio oculta o link no rodapé)</label>
              <input className="input-cellar" placeholder="https://instagram.com/seu_perfil" value={settings.chat?.instagram_url ?? ""} onChange={(e) => setChatField("instagram_url", e.target.value)} data-testid="chat-instagram" />
            </div>
          </div>
          <div className="border-t border-[#C28D58]/20 pt-4 space-y-3" data-testid="points-config">
            <h4 className="font-serif text-lg text-[#F7F2EB]">Programa de pontos (Clube)</h4>
            <p className="text-[11px] text-[#A89B8C]">Crédito somente em compras REAIS aprovadas — pedidos demo e de homologação nunca geram saldo. Base: produtos após descontos (cupom e pontos), sem frete, arredondada para baixo. Todo movimento fica no histórico auditável do cliente.</p>
            <label className="flex items-center gap-2 text-sm text-[#D5C7B7]">
              <input type="checkbox" checked={!!settings.points?.earn_enabled} onChange={(e) => setPointsField("earn_enabled", e.target.checked)} data-testid="points-earn-enabled" />
              Creditar pontos em compras reais aprovadas
            </label>
            <div>
              <label className="eyebrow mb-1 block">Reais por ponto (5 = 1 ponto a cada R$ 5)</label>
              <input className="input-cellar" type="number" step="0.5" min="0.5" value={settings.points?.reais_per_point ?? 5} onChange={(e) => setPointsField("reais_per_point", parseFloat(e.target.value))} data-testid="points-reais-per-point" />
            </div>
            <label className="flex items-center gap-2 text-sm text-[#D5C7B7]">
              <input type="checkbox" checked={!!settings.points?.redeem_enabled} onChange={(e) => setPointsField("redeem_enabled", e.target.checked)} data-testid="points-redeem-enabled" />
              Ativar resgate de pontos (exige valor do ponto definido abaixo)
            </label>
            <div>
              <label className="eyebrow mb-1 block">Valor do ponto (R$) — decisão do proprietário</label>
              <input className="input-cellar" type="number" step="0.01" min="0" value={settings.points?.point_value_brl ?? 0} onChange={(e) => setPointsField("point_value_brl", parseFloat(e.target.value) || 0)} data-testid="points-value" />
            </div>
            <div>
              <label className="eyebrow mb-1 block">Limite do resgate (% do valor dos produtos)</label>
              <input className="input-cellar" type="number" min="1" max="100" value={settings.points?.max_redeem_percent ?? 50} onChange={(e) => setPointsField("max_redeem_percent", parseFloat(e.target.value))} data-testid="points-max-percent" />
            </div>
          </div>
          <div className="border-t border-[#C28D58]/20 pt-4">
            <h4 className="font-serif text-lg text-[#F7F2EB] mb-2">Credenciais Mercado Pago (ambiente de testes)</h4>
            <p className="text-[11px] text-[#A89B8C] mb-3">
              Formulário seguro: os valores são enviados direto ao servidor e ficam apenas lá (nunca são exibidos, retornados pela API ou gravados em logs).
              {secretsStatus && (
                <span className="block mt-1">
                  Access Token: <strong className={secretsStatus.mp_access_token_set ? "text-[#7FBF7F]" : "text-[#D8A36E]"}>{secretsStatus.mp_access_token_set ? "configurado" : "não configurado"}</strong>
                  {" · "}Segredo do webhook: <strong className={secretsStatus.mp_webhook_secret_set ? "text-[#7FBF7F]" : "text-[#D8A36E]"}>{secretsStatus.mp_webhook_secret_set ? "configurado" : "não configurado"}</strong>
                </span>
              )}
            </p>
            <div className="space-y-3">
              <input type="password" autoComplete="off" className="input-cellar" placeholder="MP_ACCESS_TOKEN (credenciais de teste)"
                value={secretsForm.mp_access_token} onChange={(e) => setSecretsForm({ ...secretsForm, mp_access_token: e.target.value })} data-testid="secret-mp-token" />
              <input type="password" autoComplete="off" className="input-cellar" placeholder="MP_WEBHOOK_SECRET (Webhooks → Configurar notificações)"
                value={secretsForm.mp_webhook_secret} onChange={(e) => setSecretsForm({ ...secretsForm, mp_webhook_secret: e.target.value })} data-testid="secret-mp-webhook" />
              <button className="btn-primary" onClick={saveSecrets} disabled={secretsSaving || (!secretsForm.mp_access_token && !secretsForm.mp_webhook_secret)} data-testid="secrets-save-btn">
                {secretsSaving ? "Salvando..." : "Salvar credenciais com segurança"}
              </button>
              {secretsStatus?.mp_access_token_set && (
                <button className="btn-ghost text-xs" onClick={testConnection} disabled={connTest?.loading} data-testid="secrets-test-btn">
                  {connTest?.loading ? "Testando no sandbox..." : "Testar credencial (cria preferência de R$ 1 no sandbox)"}
                </button>
              )}
              {connTest && !connTest.loading && (
                <div className={`text-xs rounded-lg p-3 border ${connTest.credential_accepted_for_preferences ? "border-[#7FBF7F]/40 text-[#7FBF7F]" : "border-[#8A2436]/50 text-[#D88A96]"}`} data-testid="secrets-test-result">
                  <div>Credencial aceita para preferências: <strong>{connTest.credential_accepted_for_preferences ? "sim" : "não"}</strong></div>
                  <div className="text-[#A89B8C] mt-1">{connTest.detail}</div>
                  <div className="text-[#A89B8C] mt-1">Pagamentos homologados: <strong>não</strong> — bateria de testes pendente.</div>
                </div>
              )}
            </div>
          </div>
          <button className="btn-primary" onClick={saveSettings} data-testid="settings-save-btn">Salvar configurações</button>
        </div>
      )}

      {editing && (
        <div className="fixed inset-0 z-50 bg-[#0A0806]/90 backdrop-blur-sm flex items-start md:items-center justify-center p-4 overflow-auto">
          <div className="surface p-6 max-w-3xl w-full my-8">
            <h3 className="font-serif text-2xl text-[#F7F2EB] mb-4">{editing.wine_id ? "Editar vinho" : "Novo vinho"}</h3>
            <div className="grid md:grid-cols-2 gap-3 mb-4">
              {["name", "winery", "country", "region", "alcohol", "body", "sweetness", "service_temp", "badge", "image"].map((f) => (
                <input key={f} className="input-cellar" placeholder={f} value={editing[f] || ""} onChange={(e) => setEditing({ ...editing, [f]: e.target.value })} data-testid={`admin-field-${f}`} />
              ))}
              <select className="input-cellar" value={editing.type} onChange={(e) => setEditing({ ...editing, type: e.target.value })}>
                {["Tinto", "Branco", "Rosé", "Espumante", "Fortificado", "Sobremesa"].map((t) => <option key={t}>{t}</option>)}
              </select>
              <input className="input-cellar" placeholder="Uvas (separar por vírgula)" value={Array.isArray(editing.grapes) ? editing.grapes.join(", ") : editing.grapes} onChange={(e) => setEditing({ ...editing, grapes: e.target.value })} />
              <textarea className="input-cellar md:col-span-2" placeholder="Notas de aroma e sabor" value={editing.aromas || ""} onChange={(e) => setEditing({ ...editing, aromas: e.target.value })} rows={2} />
              <textarea className="input-cellar md:col-span-2" placeholder="Harmonizações" value={editing.pairing_notes || ""} onChange={(e) => setEditing({ ...editing, pairing_notes: e.target.value })} rows={2} />
              <textarea className="input-cellar md:col-span-2" placeholder="História / descrição do vinho" value={editing.story || ""} onChange={(e) => setEditing({ ...editing, story: e.target.value })} rows={3} data-testid="admin-field-story" />
              <label className="flex items-center gap-2 text-sm text-[#D5C7B7]"><input type="checkbox" checked={editing.featured} onChange={(e) => setEditing({ ...editing, featured: e.target.checked })} /> Destaque na home</label>
            </div>
            <h4 className="font-serif text-lg text-[#F7F2EB] mb-2">Variantes (safra / volume)</h4>
            <div className="space-y-2 mb-4" data-testid="variant-editor">
              {(editing.variants || []).map((v, i) => (
                <div key={i} className="grid grid-cols-3 md:grid-cols-6 gap-2 items-center">
                  <input className="input-cellar text-xs" placeholder="Safra (vazio = sem safra)" value={v.vintage || ""} onChange={(e) => { const vs = [...editing.variants]; vs[i] = { ...v, vintage: e.target.value }; setEditing({ ...editing, variants: vs }); }} />
                  <input className="input-cellar text-xs" type="number" placeholder="ml" value={v.volume_ml} onChange={(e) => { const vs = [...editing.variants]; vs[i] = { ...v, volume_ml: parseInt(e.target.value) || 750 }; setEditing({ ...editing, variants: vs }); }} />
                  <input className="input-cellar text-xs" type="number" placeholder="Preço" value={v.price} onChange={(e) => { const vs = [...editing.variants]; vs[i] = { ...v, price: parseFloat(e.target.value) || 0 }; setEditing({ ...editing, variants: vs }); }} />
                  <input className="input-cellar text-xs" type="number" placeholder="Promo" value={v.discount_price || ""} onChange={(e) => { const vs = [...editing.variants]; vs[i] = { ...v, discount_price: e.target.value ? parseFloat(e.target.value) : null }; setEditing({ ...editing, variants: vs }); }} />
                  <input className="input-cellar text-xs" type="number" placeholder="Estoque" value={v.stock} onChange={(e) => { const vs = [...editing.variants]; vs[i] = { ...v, stock: parseInt(e.target.value) || 0 }; setEditing({ ...editing, variants: vs }); }} />
                  <button className="text-[#8A2436] text-xs hover:underline" onClick={() => setEditing({ ...editing, variants: editing.variants.filter((_, j) => j !== i) })}>remover</button>
                </div>
              ))}
              <button className="btn-ghost text-xs" onClick={() => setEditing({ ...editing, variants: [...(editing.variants || []), { variant_id: null, vintage: "", volume_ml: 750, price: 0, discount_price: null, cost: 0, stock: 0 }] })} data-testid="variant-add-btn">+ adicionar variante</button>
            </div>
            <div className="flex gap-2 justify-end">
              <button className="btn-ghost" onClick={() => setEditing(null)}>Cancelar</button>
              <button className="btn-primary" onClick={saveWine} data-testid="admin-save-wine">Salvar</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function MetricCard({ label, value, testId }) {
  return (
    <div className="surface p-5" data-testid={testId}>
      <div className="eyebrow mb-2">{label}</div>
      <div className="font-serif text-2xl md:text-3xl text-[#F7F2EB]">{value}</div>
    </div>
  );
}
