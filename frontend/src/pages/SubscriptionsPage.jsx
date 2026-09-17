import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatBRL } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

const FREQ = (b) => (b?.frequency_type === "months"
  ? (b.frequency === 1 ? "cobrança mensal" : `a cada ${b.frequency} meses`)
  : `a cada ${b?.frequency} dias`);

export default function SubscriptionsPage() {
  const { user } = useAuth();
  const [data, setData] = useState(null);

  useEffect(() => {
    api.get("/subscriptions/plans").then((r) => setData(r.data)).catch(() => setData({ plans: [] }));
  }, []);

  if (!data) return <div className="text-[#A89B8C]">Carregando...</div>;

  return (
    <div className="fade-up" data-testid="subscriptions-page">
      <div className="mb-10">
        <div className="eyebrow mb-2">Clube de Assinaturas</div>
        <h1 className="font-serif text-4xl sm:text-5xl text-[#F7F2EB]">Vinhos escolhidos pela curadoria, todo ciclo na sua porta</h1>
        {data.info && <p className="text-[#A89B8C] mt-3 max-w-2xl">{data.info}</p>}
      </div>

      {data.plans.length === 0 && (
        <div className="surface p-10 text-center" data-testid="subscriptions-coming-soon">
          <h2 className="font-serif text-2xl text-[#F7F2EB] mb-3">Nossas assinaturas estão sendo preparadas</h2>
          <p className="text-[#A89B8C] text-sm max-w-lg mx-auto">
            Estamos finalizando as condições dos planos com nossa curadoria. Deixe seu interesse no atendimento
            e avisaremos quando as adesões abrirem — sem compromisso.
          </p>
          <button onClick={() => window.dispatchEvent(new CustomEvent("open-chat"))} className="btn-copper mt-6 inline-block" data-testid="subscriptions-interest-btn">Falar com o atendimento</button>
        </div>
      )}

      <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-6">
        {data.plans.map((p) => (
          <div key={p.plan_id} className="surface p-6 flex flex-col" data-testid={`plan-card-${p.tier}`}>
            {p.image && <img src={p.image} alt={p.name} className="rounded-xl h-40 w-full object-cover mb-4" />}
            <div className="eyebrow">{p.tagline}</div>
            <h2 className="font-serif text-2xl text-[#F7F2EB] mt-1">{p.name}</h2>
            <p className="text-sm text-[#A89B8C] mt-2 flex-1">{p.description}</p>
            <div className="mt-4 text-[#F7F2EB]">
              <span className="text-2xl font-serif">{formatBRL(p.price)}</span>
              <span className="text-xs text-[#A89B8C]"> · {FREQ(p.billing)}</span>
            </div>
            <div className="text-xs text-[#A89B8C] mt-1">
              {p.tier === "premium"
                ? "Kit com vinhos de todos os níveis + brindes exclusivos"
                : `${p.kit?.bottles ?? "?"} garrafa(s) por kit`}
              {(p.kit?.gifts || []).length > 0 && " · inclui brinde exclusivo"}
            </div>
            {data.next_cycle && (
              <div className="text-xs text-[#C28D58] mt-3" data-testid={`plan-cycle-${p.tier}`}>
                Primeiro kit: ciclo {data.next_cycle.key} · adesão até {data.next_cycle.cutoff_at?.slice(0, 10).split("-").reverse().join("/")}
                {data.next_cycle.ship_at && <> · postagem prevista {data.next_cycle.ship_at.slice(0, 10).split("-").reverse().join("/")}</>}
              </div>
            )}
            <Link to={user ? `/assinar/${p.plan_id}` : `/login?next=/assinar/${p.plan_id}`}
              className="btn-primary mt-5 text-center" data-testid={`subscribe-btn-${p.tier}`}>
              Ver condições e assinar
            </Link>
          </div>
        ))}
      </div>

      <div className="mt-10 text-xs text-[#A89B8C] max-w-3xl space-y-2" data-testid="subscriptions-legal">
        <p>• Assinatura é uma cobrança recorrente autorizada por você, processada pelo Mercado Pago. Nunca armazenamos os dados do seu cartão.</p>
        <p>• Você pode cancelar a qualquer momento, sem multa e sem fidelidade, direto em Minha Conta. Ciclos já pagos são preservados conforme as condições contratadas.</p>
        <p>• Ao contratar, você passa a fazer parte do Clube Casa da Barrica sem custo adicional — seus consentimentos de comunicação não são alterados.</p>
        <p>• A entrega de vinho depende das regras do destino e da aceitação da transportadora. Na entrega, um adulto maior de 18 anos apresenta documento com foto.</p>
      </div>
    </div>
  );
}
