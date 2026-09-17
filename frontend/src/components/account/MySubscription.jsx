import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatBRL } from "@/lib/api";
import { toast } from "sonner";

const KIT_STATUS = { reserved: "reservado", preparing: "em preparação", shipped: "postado", delivered: "entregue", received: "recebido" };

export default function MySubscription() {
  const [data, setData] = useState(null);
  const [none, setNone] = useState(false);
  const [addr, setAddr] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = () => {
    api.get("/subscriptions/mine").then((r) => {
      setData(r.data);
      setAddr({ ...(r.data.subscription.address || {}) });
    }).catch((e) => { if (e?.response?.status === 404) setNone(true); });
  };
  useEffect(load, []);

  if (none) return (
    <div className="surface p-8 text-[#A89B8C]" data-testid="no-subscription">
      Você ainda não possui uma assinatura. <Link to="/assinaturas" className="text-[#C28D58]">Conhecer os planos</Link>
    </div>
  );
  if (!data || !addr) return <div className="text-[#A89B8C]">Carregando...</div>;

  const s = data.subscription;

  const cancel = async () => {
    if (!window.confirm("Cancelar sua assinatura? Sem multa: cobranças futuras serão interrompidas e os ciclos já pagos são preservados. Sua conta e o Clube gratuito continuam ativos.")) return;
    setBusy(true);
    try {
      const r = await api.post("/subscriptions/mine/cancel");
      toast.success(r.data.status === "canceled" ? "Assinatura cancelada" : "Cancelamento em processamento");
      load();
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha ao cancelar"); }
    finally { setBusy(false); }
  };

  const saveAddress = async () => {
    try {
      const r = await api.patch("/subscriptions/mine/address", { address: addr });
      toast.success(r.data.message || "Endereço atualizado");
      load();
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha ao salvar endereço"); }
  };

  const confirmReceived = async (kitId) => {
    try {
      await api.post(`/subscriptions/mine/kits/${kitId}/confirm-received`);
      toast.success("Recebimento confirmado");
      load();
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha"); }
  };

  const acceptChange = async () => {
    try {
      await api.post("/subscriptions/mine/accept-change");
      toast.success("Alteração aceita");
      load();
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha"); }
  };

  return (
    <div className="space-y-6" data-testid="my-subscription">
      <div className="surface p-6">
        <div className="flex flex-wrap justify-between gap-3 items-start">
          <div>
            <h3 className="font-serif text-2xl text-[#F7F2EB]">{s.plan_snapshot?.name}</h3>
            <p className="text-sm text-[#A89B8C]">{formatBRL(s.amount)} · {s.plan_snapshot?.billing?.frequency_type === "months" ? "cobrança mensal" : "cobrança por período"} · condições da versão {s.plan_version}</p>
          </div>
          <span className="text-xs px-3 py-1 rounded-full border border-[#C28D58]/40 text-[#C28D58]" data-testid="my-sub-status">{s.status_label}</span>
        </div>
        {s.plan_snapshot?.description && <p className="text-sm text-[#D5C7B7] mt-3">{s.plan_snapshot.description}</p>}
        {s.pending_change && !s.pending_change.accepted && s.status !== "canceled" && (
          <div className="mt-4 p-4 rounded-xl border border-[#C28D58]/50 text-sm text-[#D5C7B7]" data-testid="pending-change-box">
            O plano atualizou suas condições (novo valor: {formatBRL(s.pending_change.new_price)}). Seu valor atual só muda após seu aceite.
            <button className="btn-copper ml-3 text-xs" onClick={acceptChange} data-testid="accept-change-btn">Aceitar novo valor</button>
          </div>
        )}
        <div className="text-xs text-[#A89B8C] mt-3 space-y-1">
          {data.current_cycle && <div data-testid="my-sub-cycle">Ciclo atual: {data.current_cycle.key} · postagem prevista {data.current_cycle.ship_at?.slice(0, 10) || "a definir"} · {data.current_cycle.delivery_estimate}</div>}
          {data.next_cycle && <div>Próximo ciclo elegível: {data.next_cycle.key} (adesão/pagamento até {data.next_cycle.cutoff_at?.slice(0, 10)})</div>}
          {s.demo && <div className="text-[#C28D58]">Assinatura em modo demonstração — sem cobrança real.</div>}
        </div>
        {s.status !== "canceled" && (
          <button onClick={cancel} disabled={busy} className="btn-ghost text-xs text-red-400 mt-4" data-testid="cancel-subscription-btn">
            {s.status === "cancel_pending" ? "Cancelamento em processamento" : "Cancelar assinatura (sem multa)"}
          </button>
        )}
      </div>

      <div className="surface p-6">
        <h3 className="font-serif text-xl text-[#F7F2EB] mb-3">Histórico por ciclo</h3>
        {data.kits.length === 0 && data.charges.length === 0 && <p className="text-sm text-[#A89B8C]" data-testid="sub-history-empty">Nenhum ciclo processado ainda.</p>}
        <div className="space-y-3">
          {data.charges.map((c) => (
            <div key={c.charge_id} className="text-sm text-[#D5C7B7]" data-testid={`charge-${c.charge_id}`}>
              Cobrança {formatBRL(c.amount)} — ciclo {c.cycle_id || "a atribuir"} · <span className="text-[#A89B8C]">{c.status}</span>
            </div>
          ))}
          {data.kits.map((k) => (
            <div key={k.kit_id} className="border-t border-[#C28D58]/10 pt-3" data-testid={`kit-${k.kit_id}`}>
              <div className="text-sm text-[#F7F2EB]">Kit do ciclo {k.cycle_id} · <span className="text-[#C28D58]">{KIT_STATUS[k.status]}</span></div>
              <div className="text-xs text-[#A89B8C] mt-1">
                {k.composition?.map((it, i) => <div key={i}>• {it.qty}× {it.name}</div>)}
                {k.gifts?.map((g, i) => <div key={`g${i}`} className="text-[#C28D58]">+ brinde: {g.qty}× {g.name}</div>)}
              </div>
              <div className="text-xs text-[#A89B8C] mt-1">
                {k.shipped_at && <>Postado em {k.shipped_at.slice(0, 10)}{k.tracking_code && <> · rastreio <code className="text-[#C28D58]">{k.tracking_code}</code></>}</>}
                {k.delivered_at && <> · entregue em {k.delivered_at.slice(0, 10)}</>}
                {k.received_at && <> · recebimento confirmado em {k.received_at.slice(0, 10)}</>}
              </div>
              {k.status === "delivered" && (
                <button className="btn-copper text-xs mt-2" onClick={() => confirmReceived(k.kit_id)} data-testid={`confirm-received-${k.kit_id}`}>Confirmar recebimento</button>
              )}
            </div>
          ))}
        </div>
      </div>

      {s.status !== "canceled" && (
        <div className="surface p-6">
          <h3 className="font-serif text-xl text-[#F7F2EB] mb-3">Endereço de entrega</h3>
          <p className="text-xs text-[#A89B8C] mb-3">Alterações respeitam a data de corte do ciclo atual — depois dela, valem a partir do próximo ciclo.</p>
          <div className="grid md:grid-cols-2 gap-3">
            {[["cep", "CEP"], ["street", "Rua"], ["number", "Número"], ["complement", "Complemento"], ["district", "Bairro"], ["city", "Cidade"], ["state", "UF"]].map(([f, label]) => (
              <input key={f} className="input-cellar" placeholder={label} value={addr[f] || ""} onChange={(e) => setAddr({ ...addr, [f]: e.target.value })} data-testid={`sub-addr-${f}`} />
            ))}
          </div>
          <button className="btn-primary mt-4" onClick={saveAddress} data-testid="sub-addr-save">Salvar endereço</button>
        </div>
      )}

      <div className="text-xs text-[#A89B8C]">
        Precisa de ajuda? <button onClick={() => window.dispatchEvent(new CustomEvent("open-chat"))} className="text-[#C28D58]" data-testid="sub-support-link">Fale com o atendimento</button>.
        {s.status === "canceled" && " Seu histórico permanece disponível aqui mesmo após o cancelamento."}
      </div>
    </div>
  );
}
