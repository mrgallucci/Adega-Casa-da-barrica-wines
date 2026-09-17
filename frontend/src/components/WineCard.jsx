import { Link } from "react-router-dom";
import { formatBRL } from "@/lib/api";
import { useCart } from "@/context/CartContext";
import { toast } from "sonner";

export default function WineCard({ wine }) {
  const { add } = useCart();
  const price = wine.discount_price || wine.price;
  const hasDiscount = wine.discount_price && wine.discount_price < wine.price;

  return (
    <div className="card-wine group" data-testid={`wine-card-${wine.wine_id}`}>
      <Link to={`/vinho/${wine.wine_id}`} className="block">
        <div className="relative aspect-[3/4] overflow-hidden rounded-xl bg-gradient-to-br from-[#25201B] to-[#120F0D] mb-4 flex items-center justify-center">
          <img
            src={wine.image}
            alt={wine.name}
            className="h-full w-full object-cover bottle-shadow group-hover:scale-105 transition-transform duration-500"
            loading="lazy"
          />
          {wine.badge && (
            <span className="absolute top-3 left-3 badge-pill">{wine.badge}</span>
          )}
          {hasDiscount && (
            <span className="absolute top-3 right-3 badge-pill" style={{ background: "rgba(138,36,54,0.85)", color: "#F7F2EB", borderColor: "rgba(194,141,88,0.5)" }}>
              -{Math.round(((wine.price - wine.discount_price) / wine.price) * 100)}%
            </span>
          )}
        </div>
        <div className="eyebrow mb-1">{wine.country} · {wine.type}</div>
        <h3 className="font-serif text-lg leading-snug text-[#F7F2EB] mb-1">{wine.name}</h3>
        <p className="text-xs text-[#A89B8C] mb-3">{wine.winery} {wine.vintage && `· ${wine.vintage}`}</p>
      </Link>
      <div className="mt-auto flex flex-wrap items-end justify-between gap-2">
        <div>
          {hasDiscount && (
            <div className="text-[11px] line-through text-[#A89B8C]">{formatBRL(wine.price)}</div>
          )}
          <div className="font-serif text-xl text-[#C28D58]">{formatBRL(price)}</div>
        </div>
        <button
          onClick={(e) => { e.preventDefault(); add(wine); toast.success(`${wine.name} adicionado`); }}
          className="btn-copper text-xs px-4 py-2"
          data-testid={`btn-add-to-cart-${wine.wine_id}`}
        >
          Adicionar
        </button>
      </div>
    </div>
  );
}
