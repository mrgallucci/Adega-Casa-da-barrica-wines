import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import WineCard from "@/components/WineCard";
import { Sparkles, ArrowRight, Award, Wine } from "lucide-react";

const HOME_DEFAULTS = {
  hero_eyebrow: "Adega Rústica Sofisticada",
  hero_title_start: "Cada garrafa,",
  hero_title_highlight: "uma história",
  hero_title_end: "para servir.",
  hero_description: "Vinhos selecionados manualmente, harmonização inteligente com o Sommelier Virtual e entrega climatizada. Bem-vindo à sua adega pessoal.",
  hero_primary_label: "Explorar Catálogo",
  hero_primary_href: "/catalogo",
  hero_secondary_label: "Harmonizar meu prato",
  hero_secondary_href: "/harmonizar",
  featured_eyebrow: "Seleção do sommelier",
  featured_title: "Rótulos em destaque",
  cta_eyebrow: "Experiência exclusiva",
  cta_title: "Qual vinho combina com seu prato?",
  cta_description: "Descreva o prato, a ocasião e seu orçamento. Nosso sommelier virtual sugere até três garrafas da nossa adega, com explicação técnica.",
  cta_button_label: "Consultar sommelier",
  cta_button_href: "/harmonizar",
  card_badge: "Novo",
  card_text: "Sommelier Virtual",
  card_title: "Qual vinho combina com seu prato?",
  card_description: "Sugestões personalizadas para o seu prato em segundos.",
  card_button_label: "Ver harmonização",
};

export default function HomePage() {
  const [featured, setFeatured] = useState([]);
  const [c, setC] = useState(HOME_DEFAULTS);
  useEffect(() => {
    api.get("/wines", { params: { featured: true } }).then((r) => setFeatured(r.data));
    api.get("/settings").then((r) => setC({ ...HOME_DEFAULTS, ...(r.data.home || {}) })).catch(() => {});
  }, []);
  return (
    <div className="fade-up">
      {/* Hero */}
      <section className="relative overflow-hidden cellar-radial rounded-3xl p-8 md:p-14 mb-12 border border-[#C28D58]/20">
        <div className="grid md:grid-cols-2 gap-10 items-center">
          <div>
            <div className="eyebrow mb-4" data-testid="hero-eyebrow">{c.hero_eyebrow}</div>
            <h1 className="font-serif text-4xl sm:text-5xl lg:text-6xl leading-[1.05] tracking-tight text-[#F7F2EB] mb-6">
              {c.hero_title_start}<br/> <em className="text-[#C28D58]">{c.hero_title_highlight}</em> {c.hero_title_end}
            </h1>
            <p className="text-base md:text-lg text-[#D5C7B7]/90 max-w-lg mb-8 leading-relaxed">
              {c.hero_description}
            </p>
            <div className="flex flex-wrap gap-3">
              <Link to={c.hero_primary_href} className="btn-primary inline-flex items-center gap-2" data-testid="hero-explore-btn">
                {c.hero_primary_label} <ArrowRight className="w-4 h-4" />
              </Link>
              <Link to={c.hero_secondary_href} className="btn-ghost inline-flex items-center gap-2" data-testid="hero-sommelier-btn">
                <Sparkles className="w-4 h-4" style={{ color: "var(--copper)" }} /> {c.hero_secondary_label}
              </Link>
            </div>
            <div className="mt-8 flex items-center gap-6 text-xs text-[#A89B8C]">
              <div className="flex items-center gap-2"><Award className="w-4 h-4 text-[#C28D58]" /> Curadoria assinada</div>
              <div className="flex items-center gap-2"><Wine className="w-4 h-4 text-[#C28D58]" /> +200 rótulos</div>
            </div>
          </div>
          <div className="relative">
            <img
              src="https://images.unsplash.com/photo-1724882207681-9e7e8c3dd45c?crop=entropy&cs=srgb&fm=jpg&q=85&w=900"
              alt="Adega premium"
              className="rounded-2xl w-full aspect-[4/5] object-cover shadow-2xl"
            />
            <div className="absolute -bottom-4 -left-4 surface p-4 hidden md:block" data-testid="hero-card">
              <div className="eyebrow">{c.card_badge}</div>
              <div className="font-serif text-lg text-[#F7F2EB]">{c.card_text}</div>
              <div className="text-xs text-[#A89B8C]">{c.card_title}</div>
              <div className="text-xs text-[#A89B8C]/80 mt-1 max-w-[220px]">{c.card_description}</div>
              <Link to="/harmonizar" className="text-xs font-semibold text-[#C28D58] inline-flex items-center gap-1 mt-2 hover:text-[#D4A96A] transition-colors" data-testid="hero-card-btn">
                {c.card_button_label} <ArrowRight className="w-3 h-3" />
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* Featured */}
      <section className="mb-16">
        <div className="flex items-end justify-between mb-8">
          <div>
            <div className="eyebrow mb-2">{c.featured_eyebrow}</div>
            <h2 className="font-serif text-3xl md:text-4xl text-[#F7F2EB]">{c.featured_title}</h2>
          </div>
          <Link to="/catalogo" className="text-sm text-[#C28D58] hover:text-[#D8A36E] inline-flex items-center gap-1" data-testid="see-all-featured">
            Ver todos <ArrowRight className="w-4 h-4" />
          </Link>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-5">
          {featured.map((w) => <WineCard key={w.wine_id} wine={w} />)}
        </div>
      </section>

      {/* Pairing CTA */}
      <section className="cellar-radial rounded-3xl p-8 md:p-12 border border-[#C28D58]/25 grid md:grid-cols-[1fr_auto] gap-6 items-center">
        <div>
          <div className="eyebrow mb-3">{c.cta_eyebrow}</div>
          <h2 className="font-serif text-3xl md:text-4xl text-[#F7F2EB] mb-3">{c.cta_title}</h2>
          <p className="text-[#D5C7B7] max-w-xl">{c.cta_description}</p>
        </div>
        <Link to={c.cta_button_href} className="btn-primary inline-flex items-center gap-2 justify-self-start md:justify-self-end" data-testid="home-sommelier-cta">
          <Sparkles className="w-4 h-4" /> {c.cta_button_label}
        </Link>
      </section>
    </div>
  );
}
