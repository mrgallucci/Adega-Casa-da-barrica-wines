import { useState } from "react";
import { api, formatBRL } from "@/lib/api";
import { useCart } from "@/context/CartContext";
import { Link } from "react-router-dom";
import { Sparkles, Loader2 } from "lucide-react";
import { toast } from "sonner";

export default function PairingPage() {
  const [form, setForm] = useState({ dish: "", preparation: "", occasion: "", preferences: "", max_budget: "" });
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const { add } = useCart();

  const submit = async (e) => {
    e.preventDefault();
    if (!form.dish.trim()) return toast.error("Descreva ao menos o prato.");
    setLoading(true);
    setResult(null);
    try {
      const payload = { ...form, max_budget: form.max_budget ? parseFloat(form.max_budget) : null };
      const { data } = await api.post("/pairing", payload);
      setResult(data);
    } catch (err) {
      toast.error("Não foi possível consultar o sommelier agora.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fade-up max-w-5xl">
      <div className="mb-8">
        <div className="eyebrow mb-2">Sommelier Virtual</div>
        <h1 className="font-serif text-4xl md:text-5xl text-[#F7F2EB] mb-3">Qual vinho combina com meu prato?</h1>
        <p className="text-[#D5C7B7] max-w-2xl">Descreva o que vai servir. Nosso Sommelier Virtual analisa intensidade, acidez, gordura, taninos e doçura para sugerir até três vinhos da nossa adega — sempre disponíveis em estoque.</p>
      </div>

      <form onSubmit={submit} className="surface p-6 md:p-8 grid md:grid-cols-2 gap-4 mb-8">
        <div className="md:col-span-2">
          <label className="eyebrow mb-2 block">Prato ou ingrediente principal *</label>
          <input data-testid="pairing-dish" className="input-cellar" placeholder="Ex.: Costela suína ao molho barbecue" value={form.dish} onChange={(e) => setForm({ ...form, dish: e.target.value })} />
        </div>
        <div>
          <label className="eyebrow mb-2 block">Preparo / molho</label>
          <input data-testid="pairing-preparation" className="input-cellar" placeholder="Assado lento, molho encorpado..." value={form.preparation} onChange={(e) => setForm({ ...form, preparation: e.target.value })} />
        </div>
        <div>
          <label className="eyebrow mb-2 block">Ocasião</label>
          <input data-testid="pairing-occasion" className="input-cellar" placeholder="Jantar romântico, comemoração..." value={form.occasion} onChange={(e) => setForm({ ...form, occasion: e.target.value })} />
        </div>
        <div>
          <label className="eyebrow mb-2 block">Preferências</label>
          <input data-testid="pairing-preferences" className="input-cellar" placeholder="Tinto encorpado, evitar muito taninos..." value={form.preferences} onChange={(e) => setForm({ ...form, preferences: e.target.value })} />
        </div>
        <div>
          <label className="eyebrow mb-2 block">Orçamento máximo (R$)</label>
          <input data-testid="pairing-budget" type="number" min="0" className="input-cellar" placeholder="Sem limite" value={form.max_budget} onChange={(e) => setForm({ ...form, max_budget: e.target.value })} />
        </div>
        <div className="md:col-span-2 flex justify-end">
          <button type="submit" disabled={loading} className="btn-primary inline-flex items-center gap-2" data-testid="btn-sommelier-submit">
            {loading ? <><Loader2 className="w-4 h-4 animate-spin" /> Consultando sommelier...</> : <><Sparkles className="w-4 h-4" /> Sugerir vinhos</>}
          </button>
        </div>
      </form>

      {result && (
        <div className="fade-up">
          {result.mode === "rules" && (
            <div className="surface p-4 mb-4 border-[#C28D58]/40 text-sm text-[#D5C7B7]" data-testid="pairing-rules-notice">
              Sommelier Virtual indisponível no momento — exibindo sugestão por regras.
            </div>
          )}
          {result.explanation && (
            <p className="text-[#D5C7B7] italic mb-6" data-testid="pairing-explanation">"{result.explanation}"</p>
          )}
          {result.recommendations.length === 0 ? (
            <div className="surface p-8 text-center" data-testid="pairing-no-results">
              <p className="text-[#D5C7B7]">Nenhum vinho adequado em estoque neste momento. Tente ajustar o orçamento ou o estilo.</p>
            </div>
          ) : (
            <div className="grid md:grid-cols-3 gap-5" data-testid="pairing-results">
              {result.recommendations.map(({ wine, reason }, idx) => (
                <div key={wine.wine_id} className="card-wine">
                  <div className="badge-pill mb-3 self-start">Opção {idx + 1}</div>
                  <Link to={`/vinho/${wine.wine_id}`} className="block">
                    <div className="aspect-[3/4] rounded-xl overflow-hidden mb-4 bg-[#25201B]">
                      <img src={wine.image} alt={wine.name} className="w-full h-full object-cover bottle-shadow" />
                    </div>
                    <h3 className="font-serif text-lg text-[#F7F2EB]">{wine.name}</h3>
                    <p className="text-xs text-[#A89B8C] mb-3">{wine.winery} · {wine.country}</p>
                  </Link>
                  <p className="text-sm text-[#D5C7B7] italic mb-4 leading-relaxed">{reason}</p>
                  <div className="flex items-center justify-between mt-auto">
                    <div className="font-serif text-lg text-[#C28D58]">{formatBRL(wine.discount_price || wine.price)}</div>
                    <button className="btn-copper text-xs" onClick={() => { add(wine); toast.success("Adicionado ao carrinho"); }} data-testid={`pairing-add-${wine.wine_id}`}>Adicionar</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
