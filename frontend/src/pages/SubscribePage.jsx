import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, formatBRL } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";

const FREQ = (b) => (b?.frequency_type === "months"
  ? (b.frequency === 1 ? "mensal" : `a cada ${b.frequency} meses`)
  : `a cada ${b?.frequency} dias`);

const EMPTY_ADDR = { country: "BR", cep: "", street: "", number: "", complement: "", district: "", city: "", state: "", postal_code: "" };

export default function SubscribePage() {
  const { planId } = useParams();
  const { user, loading } = useAuth();
  const nav = useNavigate();
  const [plan, setPlan] = useState(null);
  const [cycle, setCycle] = useState(null);
  const [settings, setSettings] = useState(null);
  const [addr, setAddr] = useState(EMPTY_ADDR);
  const [birthDate, setBirthDate] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const requestId = useMemo(() => crypto.randomUUID(), []);

  useEffect(() => {
    if (!loading && !user) nav(`/login?next=/assinar/${planId}`);
    api.get(`/subscriptions/plans/${planId}`).then((r) => {
      setPlan(r.data.plan);
      setCycle(r.data.next_cycle);
    }).catch(() => { toast.error("Plano indisponível"); nav("/assinaturas"); });
    api.get("/settings").then((r) => setSettings(r.data)).catch(() => {});
  }, [planId, user, loading, nav]);

  if (!plan) return <div className="text-[#A89B8C]">Carregando...</div>;

  const shippingMode = plan.shipping?.mode || "table";

  const submit = async (e) => {
    e.preventDefault();
    if (!accepted) return toast.error("Aceite as condições para continuar");
    setBusy(true);
    try {
      const address = addr.country === "BR"
        ? { country: "BR", cep: addr.cep, street: addr.street, number: addr.number, complement: addr.complement, district: addr.district, city: addr.city, state: addr.state }
        : { country: addr.country, postal_code: addr.postal_code, street: addr.street, number: addr.number, complement: addr.complement, city: addr.city, state: addr.state };
      const { data } = await api.post("/subscriptions/subscribe", {
        plan_id: planId, client_request_id: requestId, birth_date: birthDate,
        address, accepted_terms: accepted,
      });
      setResult(data);
      toast.success("Assinatura registrada");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Falha ao contratar");
    } finally { setBusy(false); }
  };

  const confirmDemo = async () => {
    try {
      await api.post("/subscriptions/mine/confirm-demo");
      toast.success("Demonstração confirmada — assinatura ativa");
      nav("/conta");
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha"); }
  };

  if (result) {
    return (
      <div className="surface p-8 max-w-xl mx-auto text-center fade-up" data-testid="subscribe-success">
        <h1 className="font-serif text-3xl text-[#F7F2EB] mb-3">Assinatura {result.status === "active" ? "ativa" : "registrada"}</h1>
        {result.mode === "demo" ? (
          <>
            <p className="text-sm text-[#A89B8C]">Modo demonstração: nenhuma cobrança real será feita.</p>
            <button className="btn-primary mt-6" onClick={confirmDemo} data-testid="demo-confirm-sub-btn">Simular 1ª cobrança aprovada (demo)</button>
          </>
        ) : result.init_point ? (
          <>
            <p className="text-sm text-[#A89B8C]">Conclua a autorização da cobrança recorrente no ambiente seguro do Mercado Pago.</p>
            <a href={result.init_point} className="btn-primary mt-6 inline-block" data-testid="mp-authorize-btn">Autorizar no Mercado Pago</a>
          </>
        ) : (
          <p className="text-sm text-[#A89B8C]">Sua cobrança recorrente foi autorizada. Acompanhe em Minha Conta.</p>
        )}
        {result.first_cycle && (
          <p className="text-xs text-[#C28D58] mt-4">Primeiro kit: ciclo {result.first_cycle.key} · postagem prevista {result.first_cycle.ship_at?.slice(0, 10) || "a definir"}</p>
        )}
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto fade-up" data-testid="subscribe-page">
      <div className="eyebrow mb-2">Contratação de assinatura</div>
      <h1 className="font-serif text-4xl text-[#F7F2EB] mb-6">{plan.name}</h1>

      <div className="surface p-6 mb-6" data-testid="subscribe-summary">
        <h2 className="font-serif text-xl text-[#F7F2EB] mb-3">Resumo das condições</h2>
        <ul className="text-sm text-[#D5C7B7] space-y-1.5">
          <li>• Valor: <strong>{formatBRL(plan.price)}</strong> ({FREQ(plan.billing)})</li>
          <li>• Composição: {plan.tier === "premium" ? "ao menos um vinho de cada nível, conforme configurado" : `${plan.kit?.bottles ?? "?"} garrafa(s) por kit`}{(plan.kit?.gifts || []).length > 0 && " + brinde exclusivo"}</li>
          <li>• Frete: {shippingMode === "included" ? "incluso no preço" : shippingMode === "custom" ? `valor fixo de ${formatBRL(plan.shipping?.custom_price || 0)}` : "calculado pela tabela da loja conforme seu CEP"}
            {plan.shipping?.promo_note ? ` — ${plan.shipping.promo_note}` : ""}</li>
          {plan.regions?.international_enabled && plan.regions?.taxes_note && <li>• Destinos internacionais: {plan.regions.taxes_note}</li>}
          {cycle ? (
            <li data-testid="subscribe-first-cycle">• Primeiro kit: ciclo <strong>{cycle.key}</strong> — adesão e pagamento até {cycle.cutoff_at?.slice(0, 10).split("-").reverse().join("/")}.
              {cycle.ship_at && <> Postagem prevista: {cycle.ship_at.slice(0, 10).split("-").reverse().join("/")}.</>}
              {cycle.delivery_estimate && <> Entrega estimada: {cycle.delivery_estimate}.</>}
              {" "}Quem aderir após o corte entra no próximo ciclo elegível.</li>
          ) : (
            <li className="text-[#C28D58]" data-testid="subscribe-no-cycle">• Calendário de ciclos em definição — seu primeiro kit será confirmado assim que o ciclo for publicado.</li>
          )}
          <li>• Cancelamento a qualquer momento, sem multa, direto em Minha Conta. Kits de ciclos já pagos seguem as condições contratadas.</li>
        </ul>
      </div>

      <form onSubmit={submit} className="surface p-6 space-y-4">
        <h2 className="font-serif text-xl text-[#F7F2EB]">Seus dados de entrega</h2>
        <div className="grid md:grid-cols-2 gap-4">
          <label className="text-xs text-[#A89B8C]">Data de nascimento (maioridade obrigatória)
            <input type="date" className="input-cellar w-full" value={birthDate} onChange={(e) => setBirthDate(e.target.value)} required data-testid="subscribe-birthdate" /></label>
          <label className="text-xs text-[#A89B8C]">País
            <select className="input-cellar w-full" value={addr.country} onChange={(e) => setAddr({ ...addr, country: e.target.value })} data-testid="subscribe-country">
              <option value="BR">Brasil</option>
              {(plan.regions?.international_enabled ? (plan.regions?.countries || []).filter((c) => c !== "BR") : []).map((c) => <option key={c} value={c}>{c}</option>)}
            </select></label>
          {addr.country === "BR" ? (
            <label className="text-xs text-[#A89B8C]">CEP
              <input className="input-cellar w-full" value={addr.cep} onChange={(e) => setAddr({ ...addr, cep: e.target.value })} required data-testid="subscribe-cep" /></label>
          ) : (
            <label className="text-xs text-[#A89B8C]">Código postal
              <input className="input-cellar w-full" value={addr.postal_code} onChange={(e) => setAddr({ ...addr, postal_code: e.target.value })} required data-testid="subscribe-postal" /></label>
          )}
          <label className="text-xs text-[#A89B8C]">Rua
            <input className="input-cellar w-full" value={addr.street} onChange={(e) => setAddr({ ...addr, street: e.target.value })} required data-testid="subscribe-street" /></label>
          <label className="text-xs text-[#A89B8C]">Número
            <input className="input-cellar w-full" value={addr.number} onChange={(e) => setAddr({ ...addr, number: e.target.value })} required data-testid="subscribe-number" /></label>
          <label className="text-xs text-[#A89B8C]">Complemento
            <input className="input-cellar w-full" value={addr.complement} onChange={(e) => setAddr({ ...addr, complement: e.target.value })} data-testid="subscribe-complement" /></label>
          <label className="text-xs text-[#A89B8C]">Cidade
            <input className="input-cellar w-full" value={addr.city} onChange={(e) => setAddr({ ...addr, city: e.target.value })} required data-testid="subscribe-city" /></label>
          <label className="text-xs text-[#A89B8C]">Estado/UF
            <input className="input-cellar w-full" value={addr.state} onChange={(e) => setAddr({ ...addr, state: e.target.value })} required data-testid="subscribe-state" /></label>
        </div>

        <label className="flex items-start gap-3 text-sm text-[#D5C7B7] cursor-pointer" data-testid="subscribe-terms-label">
          <input type="checkbox" className="mt-1" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} data-testid="subscribe-terms" />
          <span>
            Li e aceito as condições da assinatura: cobrança recorrente {FREQ(plan.billing)} de {formatBRL(plan.price)} autorizada por mim,
            calendário de ciclos exibido acima e política de cancelamento sem multa. Estou ciente de que passarei a fazer parte do
            Clube Casa da Barrica (versão das condições registrada no aceite), sem custo adicional, e que meus consentimentos de
            comunicação por e-mail/WhatsApp não são alterados por esta adesão.
          </span>
        </label>

        <button type="submit" disabled={busy || !accepted} className="btn-primary w-full" data-testid="subscribe-submit-btn">
          {busy ? "Processando..." : settings?.payment_mode === "demo" ? "Contratar (demonstração)" : "Contratar assinatura"}
        </button>
        {settings?.payment_mode === "demo" && (
          <p className="text-[11px] text-[#A89B8C] text-center" data-testid="subscribe-demo-note">Loja em modo demonstração — nenhuma cobrança real será feita.</p>
        )}
      </form>
    </div>
  );
}
