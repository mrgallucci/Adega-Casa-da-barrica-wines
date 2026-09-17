import { useEffect, useState } from "react";
import { api, formatBRL } from "@/lib/api";
import { toast } from "sonner";

const TIER_LABELS = { entrada: "Entrada", reserva: "Reserva", gran_reserva: "Gran Reserva", gran_cru: "Gran Cru", premium: "Premium" };
const STATUS_LABELS = { draft: "Rascunho", published: "Publicado", archived: "Arquivado" };
const EMPTY_PLAN = {
  name: "", tier: "entrada", tagline: "", description: "", image: "", price: "",
  billing: { frequency: 1, frequency_type: "months" },
  kit: { bottles: "", volume_ml: 750, levels: {}, gifts: [] },
  eligibility: { wine_ids: [] },
  internal: { price_ref: "price", reserva_min: 80, reserva_max: 200, apply_price_filter: true, cost: "", margin_note: "", admin_notes: "" },
  limits: { max_subscribers: "" },
  regions: { countries: ["BR"], states: [], international_enabled: false, taxes_note: "" },
  shipping: { mode: "table", custom_price: "", promo_note: "" },
  points_eligible: false, substitution: { allow: false, note: "" }, no_repeat_window: 0,
};
const PREMIUM_LEVELS = ["entrada", "reserva", "gran_reserva", "gran_cru"];

const inputCls = "input-cellar w-full";
const toNum = (v) => (v === "" || v === null || v === undefined ? null : Number(v));

export default function AdminSubscriptions({ user }) {
  const isOwner = user?.role === "owner";
  const [sub, setSub] = useState("planos");
  const [plans, setPlans] = useState([]);
  const [cycles, setCycles] = useState([]);
  const [gifts, setGifts] = useState([]);
  const [subs, setSubs] = useState([]);
  const [wines, setWines] = useState([]);
  const [editing, setEditing] = useState(null);
  const [cycleForm, setCycleForm] = useState({ key: "", cutoff_at: "", prep_at: "", ship_at: "", delivery_estimate: "" });
  const [giftForm, setGiftForm] = useState({ name: "", description: "", stock: 0, active: true });
  const [proposal, setProposal] = useState(null);
  const [proposeFor, setProposeFor] = useState({ plan_id: "", cycle_id: "" });
  const [recon, setRecon] = useState(null);

  const reload = () => {
    api.get("/admin/subscriptions/plans").then((r) => setPlans(r.data));
    api.get("/admin/subscriptions/cycles").then((r) => setCycles(r.data));
    api.get("/admin/subscriptions/gifts").then((r) => setGifts(r.data));
    api.get("/admin/subscriptions/subscribers").then((r) => setSubs(r.data));
    api.get("/admin/wines-full").then((r) => setWines(r.data));
    api.get("/admin/subscriptions/reconciliation/status").then((r) => setRecon(r.data)).catch(() => {});
  };
  useEffect(reload, []);

  const savePlan = async () => {
    try {
      const p = { ...editing, price: toNum(editing.price),
        kit: { ...editing.kit, bottles: toNum(editing.kit.bottles), gifts: editing.kit.gifts || [] },
        limits: { max_subscribers: toNum(editing.limits.max_subscribers) },
        shipping: { ...editing.shipping, custom_price: toNum(editing.shipping.custom_price) },
        internal: { ...editing.internal, cost: toNum(editing.internal.cost),
          reserva_min: toNum(editing.internal.reserva_min), reserva_max: toNum(editing.internal.reserva_max) } };
      if (editing.plan_id) await api.put(`/admin/subscriptions/plans/${editing.plan_id}`, p);
      else await api.post("/admin/subscriptions/plans", p);
      toast.success("Plano salvo");
      setEditing(null);
      reload();
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha ao salvar plano"); }
  };

  const planAction = async (id, action, body = {}) => {
    try {
      await api.post(`/admin/subscriptions/plans/${id}/${action}`, body);
      toast.success("Feito");
      reload();
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha"); }
  };

  const move = async (plan, dir) => {
    const sorted = [...plans].sort((a, b) => a.position - b.position);
    const i = sorted.findIndex((p) => p.plan_id === plan.plan_id);
    const j = i + dir;
    if (j < 0 || j >= sorted.length) return;
    [sorted[i], sorted[j]] = [sorted[j], sorted[i]];
    try {
      await api.put("/admin/subscriptions/plans-order", { plan_ids: sorted.map((p) => p.plan_id) });
      reload();
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha ao reordenar"); }
  };

  const saveCycle = async () => {
    try {
      await api.post("/admin/subscriptions/cycles", { ...cycleForm,
        cutoff_at: new Date(cycleForm.cutoff_at).toISOString(),
        prep_at: cycleForm.prep_at ? new Date(cycleForm.prep_at).toISOString() : null,
        ship_at: cycleForm.ship_at ? new Date(cycleForm.ship_at).toISOString() : null });
      toast.success("Ciclo salvo");
      setCycleForm({ key: "", cutoff_at: "", prep_at: "", ship_at: "", delivery_estimate: "" });
      reload();
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha ao salvar ciclo"); }
  };

  const saveGift = async () => {
    try {
      await api.post("/admin/subscriptions/gifts", { ...giftForm, stock: Number(giftForm.stock) });
      toast.success("Brinde salvo");
      setGiftForm({ name: "", description: "", stock: 0, active: true });
      reload();
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha ao salvar brinde"); }
  };

  const propose = async () => {
    try {
      const r = await api.post(`/admin/subscriptions/plans/${proposeFor.plan_id}/cycles/${proposeFor.cycle_id}/propose`);
      setProposal(r.data);
      toast.success("Proposta gerada — revise antes de confirmar");
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha ao gerar proposta"); }
  };

  const confirmProposal = async () => {
    try {
      const r = await api.post(`/admin/subscriptions/proposals/${proposeFor.plan_id}/${proposeFor.cycle_id}/confirm`);
      toast.success(`${r.data.kits} kit(s) criado(s) com reserva de estoque`);
      setProposal(null);
      reload();
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha ao confirmar"); }
  };

  const kitStatus = async (kit_id, status) => {
    const tracking_code = status === "shipped" ? prompt("Código de rastreamento:") : null;
    try {
      await api.post(`/admin/subscriptions/kits/${kit_id}/status`, { status, tracking_code });
      toast.success("Kit atualizado");
      reload();
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha"); }
  };

  const toggleWine = (wid) => setEditing((e) => {
    const ids = new Set(e.eligibility.wine_ids);
    ids.has(wid) ? ids.delete(wid) : ids.add(wid);
    return { ...e, eligibility: { wine_ids: [...ids] } };
  });

  return (
    <div data-testid="admin-subscriptions">
      <div className="flex gap-2 mb-6 flex-wrap">
        {[["planos", "Planos"], ["ciclos", "Ciclos & Kits"], ["brindes", "Brindes"], ["assinantes", "Assinantes"]].map(([id, label]) => (
          <button key={id} onClick={() => setSub(id)} data-testid={`sub-tab-${id}`}
            className={`px-4 py-2 rounded-full text-sm border transition-colors ${sub === id ? "bg-[#5E1925]/40 text-[#F7F2EB] border-[#C28D58]/60" : "text-[#A89B8C] border-transparent hover:text-[#F7F2EB]"}`}>{label}</button>
        ))}
      </div>

      {sub === "planos" && (
        <div>
          <div className="flex justify-between items-center mb-4">
            <p className="text-sm text-[#A89B8C]">Somente o proprietário altera preços e regras comerciais. Publicar exige preço e composição definidos.</p>
            {isOwner && <button className="btn-copper" data-testid="new-plan-btn" onClick={() => setEditing({ ...EMPTY_PLAN })}>Novo plano</button>}
          </div>
          <div className="space-y-3">
            {plans.map((p, i) => (
              <div key={p.plan_id} className="surface p-4 flex flex-wrap items-center gap-3" data-testid={`plan-row-${p.tier}`}>
                <span className="text-[#A89B8C] text-xs w-6">{i + 1}.</span>
                <div className="flex-1 min-w-[180px]">
                  <div className="text-[#F7F2EB] font-medium">{p.name} <span className="text-xs text-[#A89B8C]">({TIER_LABELS[p.tier]})</span></div>
                  <div className="text-xs text-[#A89B8C]">{p.price ? formatBRL(p.price) : "preço a definir"} · {p.billing?.frequency}x/{p.billing?.frequency_type === "months" ? "mês" : "dia"} · v{p.version}</div>
                </div>
                <span className={`text-xs px-2 py-1 rounded-full border ${p.status === "published" ? "border-green-600 text-green-400" : p.status === "archived" ? "border-[#5a5148] text-[#A89B8C]" : "border-[#C28D58]/50 text-[#C28D58]"}`} data-testid={`plan-status-${p.tier}`}>{STATUS_LABELS[p.status]}</span>
                <div className="flex gap-2 flex-wrap">
                  <button className="btn-ghost text-xs" data-testid={`edit-plan-${p.tier}`} onClick={() => setEditing({ ...EMPTY_PLAN, ...p, internal: { ...EMPTY_PLAN.internal, ...(p.internal || {}) }, kit: { ...EMPTY_PLAN.kit, ...(p.kit || {}) }, regions: { ...EMPTY_PLAN.regions, ...(p.regions || {}) }, shipping: { ...EMPTY_PLAN.shipping, ...(p.shipping || {}) }, limits: { max_subscribers: p.limits?.max_subscribers ?? "" }, price: p.price ?? "" })}>Editar</button>
                  {isOwner && <>
                    <button className="btn-ghost text-xs" onClick={() => move(p, -1)} data-testid={`plan-up-${p.tier}`}>↑</button>
                    <button className="btn-ghost text-xs" onClick={() => move(p, 1)} data-testid={`plan-down-${p.tier}`}>↓</button>
                    <button className="btn-ghost text-xs" onClick={() => planAction(p.plan_id, "duplicate")} data-testid={`dup-plan-${p.tier}`}>Duplicar</button>
                    {p.status !== "published" && <button className="btn-copper text-xs" onClick={() => planAction(p.plan_id, "publish")} data-testid={`publish-plan-${p.tier}`}>Publicar</button>}
                    {p.status === "archived"
                      ? <button className="btn-ghost text-xs" onClick={() => planAction(p.plan_id, "archive", { unarchive: true })} data-testid={`unarchive-plan-${p.tier}`}>Desarquivar</button>
                      : <button className="btn-ghost text-xs text-red-400" onClick={() => window.confirm("Arquivar impede novas adesões e preserva assinaturas existentes. Continuar?") && planAction(p.plan_id, "archive")} data-testid={`archive-plan-${p.tier}`}>Arquivar</button>}
                  </>}
                </div>
              </div>
            ))}
          </div>

          {editing && (
            <div className="surface p-6 mt-6 space-y-4" data-testid="plan-editor">
              <h3 className="font-serif text-2xl text-[#F7F2EB]">{editing.plan_id ? "Editar plano" : "Novo plano"}</h3>
              <div className="grid md:grid-cols-2 gap-4">
                <input className={inputCls} placeholder="Nome público" value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} data-testid="plan-name" />
                <select className={inputCls} value={editing.tier} onChange={(e) => setEditing({ ...editing, tier: e.target.value })} data-testid="plan-tier">
                  {Object.entries(TIER_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
                <input className={inputCls} placeholder="Tagline" value={editing.tagline} onChange={(e) => setEditing({ ...editing, tagline: e.target.value })} data-testid="plan-tagline" />
                <input className={inputCls} placeholder="URL da imagem" value={editing.image} onChange={(e) => setEditing({ ...editing, image: e.target.value })} data-testid="plan-image" />
              </div>
              <textarea className={inputCls} rows={3} placeholder="Descrição pública" value={editing.description} onChange={(e) => setEditing({ ...editing, description: e.target.value })} data-testid="plan-description" />

              <div className="border border-[#C28D58]/30 rounded-xl p-4 space-y-3">
                <div className="eyebrow">Comercial {isOwner ? "" : "— somente proprietário"}</div>
                <div className="grid md:grid-cols-4 gap-4">
                  <label className="text-xs text-[#A89B8C]">Preço (R$)
                    <input type="number" step="0.01" className={inputCls} value={editing.price} disabled={!isOwner} onChange={(e) => setEditing({ ...editing, price: e.target.value })} data-testid="plan-price" /></label>
                  <label className="text-xs text-[#A89B8C]">Cobrança a cada
                    <input type="number" min="1" className={inputCls} value={editing.billing.frequency} disabled={!isOwner} onChange={(e) => setEditing({ ...editing, billing: { ...editing.billing, frequency: Number(e.target.value) } })} data-testid="plan-billing-freq" /></label>
                  <label className="text-xs text-[#A89B8C]">Unidade
                    <select className={inputCls} value={editing.billing.frequency_type} disabled={!isOwner} onChange={(e) => setEditing({ ...editing, billing: { ...editing.billing, frequency_type: e.target.value } })} data-testid="plan-billing-type">
                      <option value="months">mês(es)</option><option value="days">dia(s)</option>
                    </select></label>
                  {editing.tier !== "premium" && (
                    <label className="text-xs text-[#A89B8C]">Garrafas por kit
                      <input type="number" min="1" className={inputCls} value={editing.kit.bottles} disabled={!isOwner} onChange={(e) => setEditing({ ...editing, kit: { ...editing.kit, bottles: e.target.value } })} data-testid="plan-bottles" /></label>)}
                </div>
                {editing.tier === "premium" && (
                  <div className="grid md:grid-cols-4 gap-4">
                    {PREMIUM_LEVELS.map((lv) => (
                      <label key={lv} className="text-xs text-[#A89B8C]">{TIER_LABELS[lv]} por kit (mín. 1)
                        <input type="number" min="1" className={inputCls} value={editing.kit.levels?.[lv] ?? 1} disabled={!isOwner}
                          onChange={(e) => setEditing({ ...editing, kit: { ...editing.kit, levels: { ...(editing.kit.levels || {}), [lv]: Number(e.target.value) } } })} data-testid={`plan-level-${lv}`} /></label>
                    ))}
                  </div>
                )}
                <div className="grid md:grid-cols-3 gap-4">
                  <label className="text-xs text-[#A89B8C]">Frete
                    <select className={inputCls} value={editing.shipping.mode} disabled={!isOwner} onChange={(e) => setEditing({ ...editing, shipping: { ...editing.shipping, mode: e.target.value } })} data-testid="plan-shipping-mode">
                      <option value="table">Tabela de frete da loja (CEP)</option>
                      <option value="included">Incluso no preço</option>
                      <option value="custom">Valor fixo do plano</option>
                    </select></label>
                  {editing.shipping.mode === "custom" && (
                    <label className="text-xs text-[#A89B8C]">Frete fixo (R$)
                      <input type="number" step="0.01" className={inputCls} value={editing.shipping.custom_price} disabled={!isOwner} onChange={(e) => setEditing({ ...editing, shipping: { ...editing.shipping, custom_price: e.target.value } })} data-testid="plan-shipping-custom" /></label>)}
                  <label className="text-xs text-[#A89B8C]">Limite de assinantes (vazio = sem limite)
                    <input type="number" min="1" className={inputCls} value={editing.limits.max_subscribers} disabled={!isOwner} onChange={(e) => setEditing({ ...editing, limits: { max_subscribers: e.target.value } })} data-testid="plan-max-subs" /></label>
                </div>
                <div className="grid md:grid-cols-3 gap-4 items-end">
                  <label className="text-xs text-[#A89B8C]">Estados atendidos (UFs, vazio = todos)
                    <input className={inputCls} placeholder="SP, RJ" value={(editing.regions.states || []).join(", ")} disabled={!isOwner}
                      onChange={(e) => setEditing({ ...editing, regions: { ...editing.regions, states: e.target.value.split(",").map((x) => x.trim().toUpperCase()).filter(Boolean) } })} data-testid="plan-states" /></label>
                  <label className="text-xs text-[#A89B8C] flex items-center gap-2">
                    <input type="checkbox" checked={!!editing.regions.international_enabled} disabled={!isOwner}
                      onChange={(e) => setEditing({ ...editing, regions: { ...editing.regions, international_enabled: e.target.checked } })} data-testid="plan-intl" />
                    Ativar destinos internacionais</label>
                  <label className="text-xs text-[#A89B8C] flex items-center gap-2">
                    <input type="checkbox" checked={!!editing.points_eligible} disabled={!isOwner}
                      onChange={(e) => setEditing({ ...editing, points_eligible: e.target.checked })} data-testid="plan-points" />
                    Elegível a pontos do Clube (somente cobranças reais)</label>
                </div>
                {editing.regions.international_enabled && (
                  <div className="grid md:grid-cols-2 gap-4">
                    <label className="text-xs text-[#A89B8C]">Países atendidos (siglas)
                      <input className={inputCls} placeholder="BR, PT, US" value={(editing.regions.countries || []).join(", ")} disabled={!isOwner}
                        onChange={(e) => setEditing({ ...editing, regions: { ...editing.regions, countries: e.target.value.split(",").map((x) => x.trim().toUpperCase()).filter(Boolean) } })} data-testid="plan-countries" /></label>
                    <label className="text-xs text-[#A89B8C]">Aviso de tributos de importação
                      <input className={inputCls} value={editing.regions.taxes_note || ""} disabled={!isOwner}
                        onChange={(e) => setEditing({ ...editing, regions: { ...editing.regions, taxes_note: e.target.value } })} data-testid="plan-taxes-note" /></label>
                  </div>
                )}
                <label className="text-xs text-[#A89B8C] flex items-center gap-2">
                  <input type="checkbox" checked={!!editing.substitution.allow} disabled={!isOwner}
                    onChange={(e) => setEditing({ ...editing, substitution: { ...editing.substitution, allow: e.target.checked } })} data-testid="plan-substitution" />
                  Permitir substituição de rótulo por outro de nível igual ou superior (regra de substituição)</label>
                <label className="text-xs text-[#A89B8C]">Evitar repetir rótulos dos últimos N ciclos (0 = sem controle)
                  <input type="number" min="0" className={inputCls} value={editing.no_repeat_window} disabled={!isOwner}
                    onChange={(e) => setEditing({ ...editing, no_repeat_window: Number(e.target.value) })} data-testid="plan-no-repeat" /></label>
              </div>

              <div className="border border-[#5a5148] rounded-xl p-4 space-y-3">
                <div className="eyebrow">Uso interno — nunca exibido ao público</div>
                {editing.tier === "reserva" && (
                  <div className="grid md:grid-cols-4 gap-4">
                    <label className="text-xs text-[#A89B8C]">Faixa mín. (R$)
                      <input type="number" step="0.01" className={inputCls} value={editing.internal.reserva_min ?? ""} disabled={!isOwner} onChange={(e) => setEditing({ ...editing, internal: { ...editing.internal, reserva_min: e.target.value } })} data-testid="plan-reserva-min" /></label>
                    <label className="text-xs text-[#A89B8C]">Faixa máx. (R$)
                      <input type="number" step="0.01" className={inputCls} value={editing.internal.reserva_max ?? ""} disabled={!isOwner} onChange={(e) => setEditing({ ...editing, internal: { ...editing.internal, reserva_max: e.target.value } })} data-testid="plan-reserva-max" /></label>
                    <label className="text-xs text-[#A89B8C]">Preço de referência
                      <select className={inputCls} value={editing.internal.price_ref} disabled={!isOwner} onChange={(e) => setEditing({ ...editing, internal: { ...editing.internal, price_ref: e.target.value } })} data-testid="plan-price-ref">
                        <option value="price">Preço cheio</option><option value="discount_price">Preço promocional</option>
                      </select></label>
                    <label className="text-xs text-[#A89B8C] flex items-center gap-2 mt-4">
                      <input type="checkbox" checked={!!editing.internal.apply_price_filter} disabled={!isOwner}
                        onChange={(e) => setEditing({ ...editing, internal: { ...editing.internal, apply_price_filter: e.target.checked } })} data-testid="plan-apply-filter" />
                      Aplicar filtro de faixa</label>
                  </div>
                )}
                <div className="grid md:grid-cols-3 gap-4">
                  <label className="text-xs text-[#A89B8C]">Custo estimado (R$)
                    <input type="number" step="0.01" className={inputCls} value={editing.internal.cost ?? ""} disabled={!isOwner} onChange={(e) => setEditing({ ...editing, internal: { ...editing.internal, cost: e.target.value } })} data-testid="plan-cost" /></label>
                  <label className="text-xs text-[#A89B8C] md:col-span-2">Nota de margem
                    <input className={inputCls} value={editing.internal.margin_note || ""} disabled={!isOwner} onChange={(e) => setEditing({ ...editing, internal: { ...editing.internal, margin_note: e.target.value } })} data-testid="plan-margin-note" /></label>
                </div>
                <textarea className={inputCls} rows={2} placeholder="Observações administrativas" value={editing.internal.admin_notes || ""} onChange={(e) => setEditing({ ...editing, internal: { ...editing.internal, admin_notes: e.target.value } })} data-testid="plan-admin-notes" />
              </div>

              <div className="border border-[#C28D58]/30 rounded-xl p-4">
                <div className="eyebrow mb-2">Rótulos elegíveis (curadoria do administrador)</div>
                <div className="max-h-56 overflow-y-auto grid md:grid-cols-2 gap-1">
                  {wines.filter((w) => !w.archived).map((w) => (
                    <label key={w.wine_id} className="text-xs text-[#D5C7B7] flex items-center gap-2 py-0.5">
                      <input type="checkbox" checked={editing.eligibility.wine_ids.includes(w.wine_id)} onChange={() => toggleWine(w.wine_id)} data-testid={`plan-wine-${w.wine_id}`} />
                      {w.name} <span className="text-[#A89B8C]">({formatBRL(w.variants?.[0]?.price || 0)} · disp. {(w.variants?.[0]?.stock || 0) - (w.variants?.[0]?.reserved || 0)})</span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="border border-[#C28D58]/30 rounded-xl p-4">
                <div className="eyebrow mb-2">Brindes do kit {(editing.tier === "gran_cru" || editing.tier === "premium") && "(obrigatório neste plano)"}</div>
                {(editing.kit.gifts || []).map((g, i) => (
                  <div key={i} className="flex gap-2 items-center mb-2">
                    <select className={inputCls} value={g.gift_id} disabled={!isOwner}
                      onChange={(e) => setEditing({ ...editing, kit: { ...editing.kit, gifts: editing.kit.gifts.map((x, xi) => xi === i ? { ...x, gift_id: e.target.value } : x) } })} data-testid={`plan-gift-${i}`}>
                      <option value="">Escolha o brinde</option>
                      {gifts.map((gf) => <option key={gf.gift_id} value={gf.gift_id}>{gf.name} (disp. {gf.stock - (gf.reserved || 0)})</option>)}
                    </select>
                    <input type="number" min="1" className="input-cellar w-20" value={g.qty} disabled={!isOwner}
                      onChange={(e) => setEditing({ ...editing, kit: { ...editing.kit, gifts: editing.kit.gifts.map((x, xi) => xi === i ? { ...x, qty: Number(e.target.value) } : x) } })} data-testid={`plan-gift-qty-${i}`} />
                    <button className="btn-ghost text-xs" onClick={() => setEditing({ ...editing, kit: { ...editing.kit, gifts: editing.kit.gifts.filter((_, xi) => xi !== i) } })}>remover</button>
                  </div>
                ))}
                {isOwner && <button className="btn-ghost text-xs" onClick={() => setEditing({ ...editing, kit: { ...editing.kit, gifts: [...(editing.kit.gifts || []), { gift_id: "", qty: 1 }] } })} data-testid="plan-add-gift">+ adicionar brinde</button>}
              </div>

              <div className="flex gap-3">
                <button className="btn-primary" onClick={savePlan} data-testid="plan-save-btn">Salvar plano</button>
                <button className="btn-ghost" onClick={() => setEditing(null)} data-testid="plan-cancel-btn">Cancelar</button>
              </div>
            </div>
          )}
        </div>
      )}

      {sub === "ciclos" && (
        <div className="grid lg:grid-cols-2 gap-6">
          <div className="surface p-6 space-y-3">
            <h3 className="font-serif text-2xl text-[#F7F2EB]">Calendário de ciclos</h3>
            <p className="text-xs text-[#A89B8C]">O envio segue o calendário abaixo, não o aniversário da adesão. Quem aderir após o corte entra no próximo ciclo elegível.</p>
            <input className={inputCls} placeholder="Competência (ex.: 2026-08)" value={cycleForm.key} onChange={(e) => setCycleForm({ ...cycleForm, key: e.target.value })} data-testid="cycle-key" />
            <label className="text-xs text-[#A89B8C] block">Data limite de adesão/pagamento
              <input type="datetime-local" className={inputCls} value={cycleForm.cutoff_at} onChange={(e) => setCycleForm({ ...cycleForm, cutoff_at: e.target.value })} data-testid="cycle-cutoff" /></label>
            <label className="text-xs text-[#A89B8C] block">Preparação prevista
              <input type="datetime-local" className={inputCls} value={cycleForm.prep_at} onChange={(e) => setCycleForm({ ...cycleForm, prep_at: e.target.value })} data-testid="cycle-prep" /></label>
            <label className="text-xs text-[#A89B8C] block">Data de postagem
              <input type="datetime-local" className={inputCls} value={cycleForm.ship_at} onChange={(e) => setCycleForm({ ...cycleForm, ship_at: e.target.value })} data-testid="cycle-ship" /></label>
            <input className={inputCls} placeholder="Estimativa de entrega (ex.: 3 a 8 dias úteis após postagem)" value={cycleForm.delivery_estimate} onChange={(e) => setCycleForm({ ...cycleForm, delivery_estimate: e.target.value })} data-testid="cycle-delivery" />
            {isOwner && <button className="btn-primary" onClick={saveCycle} data-testid="cycle-save-btn">Salvar ciclo</button>}
            <div className="divide-y divide-[#C28D58]/10 mt-4">
              {cycles.map((c) => (
                <div key={c.cycle_id} className="py-3" data-testid={`cycle-row-${c.key}`}>
                  <div className="flex justify-between items-center">
                    <span className="text-[#F7F2EB]">{c.key} <span className="text-xs text-[#A89B8C]">({c.status})</span></span>
                    {isOwner && c.status === "open" && <button className="btn-ghost text-xs" onClick={async () => { await api.post(`/admin/subscriptions/cycles/${c.cycle_id}/status`, { status: "closed" }); reload(); }} data-testid={`cycle-close-${c.key}`}>Fechar adesões</button>}
                  </div>
                  <div className="text-xs text-[#A89B8C]">corte: {c.cutoff_at?.slice(0, 10)} · postagem: {c.ship_at?.slice(0, 10) || "a definir"} · {c.delivery_estimate}</div>
                  {(c.history || []).length > 0 && <div className="text-[10px] text-[#C28D58] mt-1">{c.history.length} alteração(ões) registrada(s) — assinantes notificados</div>}
                </div>
              ))}
            </div>
          </div>

          <div className="surface p-6 space-y-3">
            <h3 className="font-serif text-2xl text-[#F7F2EB]">Curadoria do kit por ciclo</h3>
            <p className="text-xs text-[#A89B8C]">Gere a proposta, revise pendências e confirme para reservar o estoque (compartilhado com a loja avulsa).</p>
            <select className={inputCls} value={proposeFor.plan_id} onChange={(e) => setProposeFor({ ...proposeFor, plan_id: e.target.value })} data-testid="propose-plan">
              <option value="">Plano</option>
              {plans.map((p) => <option key={p.plan_id} value={p.plan_id}>{p.name}</option>)}
            </select>
            <select className={inputCls} value={proposeFor.cycle_id} onChange={(e) => setProposeFor({ ...proposeFor, cycle_id: e.target.value })} data-testid="propose-cycle">
              <option value="">Ciclo</option>
              {cycles.map((c) => <option key={c.cycle_id} value={c.cycle_id}>{c.key}</option>)}
            </select>
            <button className="btn-copper" disabled={!proposeFor.plan_id || !proposeFor.cycle_id} onClick={propose} data-testid="propose-btn">Gerar proposta</button>
            {proposal && (
              <div className="mt-4 space-y-2" data-testid="proposal-box">
                <div className="text-sm text-[#F7F2EB]">{proposal.subscribers?.length || 0} assinante(s) elegível(is) · status: {proposal.status}</div>
                <div className="text-xs text-[#D5C7B7]">
                  {proposal.composition?.map((it, i) => <div key={i}>• {it.qty}× {it.name} <span className="text-[#A89B8C]">({TIER_LABELS[it.level] || it.level})</span></div>)}
                  {proposal.gifts?.map((g, i) => <div key={`g${i}`} className="text-[#C28D58]">+ brinde: {g.qty}× {g.name}</div>)}
                </div>
                {proposal.issues?.length > 0 && (
                  <div className="text-xs text-red-400 space-y-1" data-testid="proposal-issues">
                    {proposal.issues.map((iss, i) => <div key={i}>⚠ {iss}</div>)}
                  </div>
                )}
                <button className="btn-primary" disabled={proposal.issues?.length > 0 || proposal.status === "confirmed"} onClick={confirmProposal} data-testid="confirm-proposal-btn">
                  {proposal.status === "confirmed" ? "Confirmada" : "Confirmar e reservar estoque"}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {sub === "brindes" && (
        <div className="grid lg:grid-cols-2 gap-6">
          <div className="surface p-6 space-y-3">
            <h3 className="font-serif text-2xl text-[#F7F2EB]">Novo brinde</h3>
            <input className={inputCls} placeholder="Nome (ex.: Abridor sommelier)" value={giftForm.name} onChange={(e) => setGiftForm({ ...giftForm, name: e.target.value })} data-testid="gift-name" />
            <input className={inputCls} placeholder="Descrição" value={giftForm.description} onChange={(e) => setGiftForm({ ...giftForm, description: e.target.value })} data-testid="gift-desc" />
            <label className="text-xs text-[#A89B8C] block">Estoque próprio
              <input type="number" min="0" className={inputCls} value={giftForm.stock} onChange={(e) => setGiftForm({ ...giftForm, stock: e.target.value })} data-testid="gift-stock" /></label>
            <button className="btn-primary" onClick={saveGift} data-testid="gift-save-btn">Salvar brinde</button>
          </div>
          <div className="space-y-3">
            {gifts.map((g) => (
              <div key={g.gift_id} className="surface p-4 flex justify-between items-center" data-testid={`gift-row-${g.gift_id}`}>
                <div>
                  <div className="text-[#F7F2EB]">{g.name}</div>
                  <div className="text-xs text-[#A89B8C]">estoque {g.stock} · reservado {g.reserved || 0}</div>
                </div>
                <button className="btn-ghost text-xs text-red-400" onClick={async () => { await api.delete(`/admin/subscriptions/gifts/${g.gift_id}`); reload(); }} data-testid={`gift-del-${g.gift_id}`}>Excluir</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {sub === "assinantes" && (
        <div className="space-y-3">
          {recon && <div className="text-xs text-[#A89B8C] mb-2" data-testid="sub-recon-status">Reconciliação: {recon.last_run ? `${recon.last_run.status} em ${recon.last_run.at?.slice(0, 16).replace("T", " ")}` : "nunca executada"} · ativas: {recon.active_subscriptions} · inadimplentes: {recon.delinquent}</div>}
          {subs.length === 0 && <div className="surface p-8 text-[#A89B8C]" data-testid="subscribers-empty">Nenhum assinante ainda.</div>}
          {subs.map((s) => (
            <div key={s.sub_id} className="surface p-4" data-testid={`subscriber-${s.sub_id}`}>
              <div className="flex flex-wrap justify-between gap-2">
                <div>
                  <span className="text-[#F7F2EB]">{s.user_email}</span> · <span className="text-[#C28D58]">{s.plan_name}</span> · {formatBRL(s.amount)}
                  {s.demo && <span className="text-xs text-[#A89B8C]"> (demo)</span>}
                </div>
                <span className="text-xs px-2 py-1 rounded-full border border-[#C28D58]/40 text-[#C28D58]" data-testid={`subscriber-status-${s.sub_id}`}>{s.status_label}</span>
              </div>
              <div className="text-xs text-[#A89B8C] mt-2">
                cobranças: {s.charges.map((c) => `${c.cycle_id || "—"}:${c.status}`).join(" · ") || "nenhuma"}<br />
                kits: {s.kits.map((k) => `${k.cycle_id}:${k.status}`).join(" · ") || "nenhum"}
              </div>
              <div className="flex gap-2 mt-2">
                {s.kits.filter((k) => ["reserved", "preparing", "shipped"].includes(k.status)).map((k) => (
                  <button key={k.kit_id} className="btn-ghost text-xs"
                    onClick={() => kitStatus(k.kit_id, { reserved: "preparing", preparing: "shipped", shipped: "delivered" }[k.status])}
                    data-testid={`kit-advance-${k.kit_id}`}>
                    kit {k.cycle_id}: marcar {({ reserved: "em preparação", preparing: "postado", shipped: "entregue" })[k.status]}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
