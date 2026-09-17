import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import WineCard from "@/components/WineCard";
import { Search } from "lucide-react";

const TYPES = ["Tinto", "Branco", "Rosé", "Espumante", "Fortificado", "Sobremesa"];

export default function CatalogPage() {
  const [wines, setWines] = useState([]);
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [country, setCountry] = useState("");
  const [sort, setSort] = useState("");
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    const params = {};
    if (q) params.q = q;
    if (type) params.type = type;
    if (country) params.country = country;
    if (sort) params.sort = sort;
    api.get("/wines", { params }).then((r) => setWines(r.data)).finally(() => setLoading(false));
  };

  useEffect(load, [type, country, sort]);
  useEffect(() => { const t = setTimeout(load, 300); return () => clearTimeout(t); }, [q]);

  const countries = Array.from(new Set(wines.map((w) => w.country).filter(Boolean)));

  return (
    <div className="fade-up">
      <div className="mb-8">
        <div className="eyebrow mb-2">Nosso Acervo</div>
        <h1 className="font-serif text-4xl md:text-5xl text-[#F7F2EB]">Explore a adega</h1>
      </div>

      <div className="surface p-4 md:p-5 mb-8 grid md:grid-cols-[1fr_repeat(3,180px)] gap-3">
        <div className="relative">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[#A89B8C]" />
          <input
            data-testid="catalog-search"
            className="input-cellar pl-9"
            placeholder="Buscar por nome, uva, país, prato..."
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <select data-testid="filter-type-select" className="input-cellar" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">Todos os tipos</option>
          {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select data-testid="filter-country-select" className="input-cellar" value={country} onChange={(e) => setCountry(e.target.value)}>
          <option value="">Todos os países</option>
          {countries.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select data-testid="filter-sort-select" className="input-cellar" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="">Ordenar</option>
          <option value="price_asc">Menor preço</option>
          <option value="price_desc">Maior preço</option>
          <option value="newest">Novidades</option>
        </select>
      </div>

      {loading ? (
        <div className="text-[#A89B8C]" data-testid="catalog-loading">Carregando adega...</div>
      ) : wines.length === 0 ? (
        <div className="surface p-10 text-center" data-testid="catalog-empty">
          <div className="font-serif text-2xl text-[#F7F2EB] mb-2">Nenhum vinho encontrado</div>
          <p className="text-[#A89B8C]">Ajuste os filtros ou tente outra busca.</p>
        </div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-5" data-testid="catalog-grid">
          {wines.map((w) => <WineCard key={w.wine_id} wine={w} />)}
        </div>
      )}
    </div>
  );
}
