import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api, formatBRL } from "@/lib/api";
import { useCart } from "@/context/CartContext";
import { toast } from "sonner";
import { ArrowLeft, ShoppingBag, Thermometer, Wine, MapPin, Grape } from "lucide-react";

export default function WineDetailPage() {
  const { id } = useParams();
  const [wine, setWine] = useState(null);
  const [qty, setQty] = useState(1);
  const [variantId, setVariantId] = useState(null);
  const { add } = useCart();

  useEffect(() => {
    api.get(`/wines/${id}`).then((r) => {
      setWine(r.data);
      const vars = r.data.variants || [];
      if (vars.length) setVariantId(vars[0].variant_id);
    }).catch(() => setWine(false));
  }, [id]);

  if (wine === null) return <div className="text-[#A89B8C]">Carregando...</div>;
  if (wine === false) return <div className="surface p-8 text-center text-[#A89B8C]">Vinho não encontrado.</div>;

  const variants = wine.variants && wine.variants.length
    ? wine.variants
    : [{ variant_id: "default", vintage: wine.vintage, volume_ml: wine.volume_ml || 750, price: wine.price, discount_price: wine.discount_price, stock: wine.stock }];
  const variant = variants.find((v) => v.variant_id === variantId) || variants[0];
  const price = variant.discount_price || variant.price;
  const available = Math.max(0, (variant.stock || 0) - (variant.reserved || 0));

  return (
    <div className="fade-up">
      <Link to="/catalogo" className="text-sm text-[#A89B8C] hover:text-[#C28D58] inline-flex items-center gap-1 mb-6" data-testid="back-to-catalog">
        <ArrowLeft className="w-4 h-4" /> Voltar ao catálogo
      </Link>

      <div className="grid md:grid-cols-2 gap-10">
        <div className="surface p-6 flex items-center justify-center min-h-[500px]">
          <img src={wine.image} alt={wine.name} className="max-h-[560px] object-contain bottle-shadow" />
        </div>
        <div>
          <div className="eyebrow mb-2">{wine.country} {wine.region && `· ${wine.region}`}</div>
          <h1 className="font-serif text-3xl md:text-5xl text-[#F7F2EB] leading-tight mb-2">{wine.name}</h1>
          <p className="text-[#D5C7B7] mb-6">{wine.winery}</p>

          <div className="flex flex-wrap gap-2 mb-6">
            {wine.badge && <span className="badge-pill">{wine.badge}</span>}
            <span className="badge-pill">{wine.type}</span>
            {wine.body && <span className="badge-pill">Corpo: {wine.body}</span>}
            {wine.sweetness && <span className="badge-pill">{wine.sweetness}</span>}
          </div>

          {variants.length > 1 && (
            <div className="mb-4">
              <div className="eyebrow mb-2">Safra / volume</div>
              <div className="flex flex-wrap gap-2" data-testid="variant-selector">
                {variants.map((v) => (
                  <button
                    key={v.variant_id}
                    onClick={() => setVariantId(v.variant_id)}
                    data-testid={`variant-${v.variant_id}`}
                    className={`px-3 py-2 rounded-lg text-xs border transition-colors ${v.variant_id === variant.variant_id ? "border-[#C28D58] bg-[#C28D58]/10 text-[#C28D58]" : "border-[#C28D58]/25 text-[#D5C7B7]"}`}
                  >
                    {v.vintage || "sem safra"} · {v.volume_ml}ml — {formatBRL(v.discount_price || v.price)}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="surface p-5 mb-6">
            <div className="flex items-end gap-3 mb-4">
              {variant.discount_price && (
                <div className="text-sm line-through text-[#A89B8C]">{formatBRL(variant.price)}</div>
              )}
              <div className="font-serif text-4xl text-[#C28D58]">{formatBRL(price)}</div>
            </div>
            <div className="flex items-center gap-3 mb-4">
              <label className="text-sm text-[#D5C7B7]">Quantidade:</label>
              <div className="flex items-center gap-1">
                <button onClick={() => setQty(Math.max(1, qty - 1))} className="btn-ghost px-3 py-1" data-testid="qty-minus">−</button>
                <span className="w-10 text-center" data-testid="qty-value">{qty}</span>
                <button onClick={() => setQty(qty + 1)} className="btn-ghost px-3 py-1" data-testid="qty-plus">+</button>
              </div>
              <span className="text-xs text-[#A89B8C] ml-auto">{available > 0 ? `${available} disponíveis` : "Esgotado"}</span>
            </div>
            <button
              onClick={() => { add(wine, qty, variant); toast.success(`${qty}x ${wine.name} no carrinho`); }}
              className="btn-primary w-full inline-flex items-center justify-center gap-2"
              data-testid="btn-buy-now"
              disabled={available <= 0}
            >
              <ShoppingBag className="w-4 h-4" /> Adicionar ao carrinho
            </button>
          </div>

          <div className="space-y-5 text-sm">
            {wine.aromas && (
              <div>
                <div className="eyebrow mb-2">Notas de aroma e sabor</div>
                <p className="text-[#D5C7B7] leading-relaxed">{wine.aromas}</p>
              </div>
            )}
            {wine.pairing_notes && (
              <div>
                <div className="eyebrow mb-2">Harmonização recomendada</div>
                <p className="text-[#D5C7B7] leading-relaxed">{wine.pairing_notes}</p>
              </div>
            )}
            {wine.story && (
              <div>
                <div className="eyebrow mb-2">História</div>
                <p className="text-[#D5C7B7] leading-relaxed">{wine.story}</p>
              </div>
            )}
            <div className="grid grid-cols-2 gap-4">
              {wine.grapes?.length > 0 && (
                <div className="surface p-4">
                  <div className="flex items-center gap-2 mb-1"><Grape className="w-4 h-4 text-[#C28D58]" /><div className="eyebrow">Uvas</div></div>
                  <div className="text-sm text-[#D5C7B7]">{wine.grapes.join(", ")}</div>
                </div>
              )}
              {wine.alcohol && (
                <div className="surface p-4">
                  <div className="flex items-center gap-2 mb-1"><Wine className="w-4 h-4 text-[#C28D58]" /><div className="eyebrow">Teor alcoólico</div></div>
                  <div className="text-sm text-[#D5C7B7]">{wine.alcohol}</div>
                </div>
              )}
              {wine.service_temp && (
                <div className="surface p-4">
                  <div className="flex items-center gap-2 mb-1"><Thermometer className="w-4 h-4 text-[#C28D58]" /><div className="eyebrow">Temperatura</div></div>
                  <div className="text-sm text-[#D5C7B7]">{wine.service_temp}</div>
                </div>
              )}
              {wine.region && (
                <div className="surface p-4">
                  <div className="flex items-center gap-2 mb-1"><MapPin className="w-4 h-4 text-[#C28D58]" /><div className="eyebrow">Região</div></div>
                  <div className="text-sm text-[#D5C7B7]">{wine.region}</div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
