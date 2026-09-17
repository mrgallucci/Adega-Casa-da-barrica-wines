import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { Home, Compass, Sparkles, ShoppingBag, User, LogOut, MessageCircle, Instagram } from "lucide-react";
import { useCart } from "@/context/CartContext";
import { useAuth } from "@/context/AuthContext";
import { api } from "@/lib/api";
import ChatWidget from "@/components/ChatWidget";

// Para migrar o logo para SVG, basta trocar o arquivo/caminho aqui.
const LOGO_SRC = "/brand/logo.png";

const desktopNav = [
  { to: "/", label: "Início", testId: "nav-desktop-home" },
  { to: "/catalogo", label: "Explorar Vinhos", testId: "nav-desktop-catalog" },
  { to: "/harmonizar", label: "Harmonizar com Sommelier", testId: "nav-desktop-sommelier" },
  { to: "/assinaturas", label: "Assinaturas", testId: "nav-desktop-subscriptions" },
  { to: "/conta", label: "Minha Conta", testId: "nav-desktop-account" },
];

export default function Layout() {
  const { count } = useCart();
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const [brand, setBrand] = useState({
    store_name: "Casa da Barrica Wines",
    tagline: "Curadoria, Histórias & Descobertas em cada vinho",
    footer: {
      about: "Casa da Barrica Wines — e-commerce premium de vinhos com curadoria de sommelier, harmonização com o Sommelier Virtual e entrega climatizada.",
      email: "contato@casadabarrica.com.br",
      phone: "(11) 4000-0000",
      address: "São Paulo — SP",
    },
    chat: null,
  });
  useEffect(() => {
    api.get("/settings")
      .then((r) => setBrand((b) => ({
        ...b,
        store_name: r.data.store_name || b.store_name,
        tagline: r.data.tagline || b.tagline,
        footer: { ...b.footer, ...(r.data.footer || {}) },
        chat: r.data.chat || null,
      })))
      .catch(() => {});
  }, []);

  return (
    <div className="grain-overlay">
      {/* Desktop header */}
      <header className="header-glass sticky top-0 z-40 hidden md:block">
        <div className="max-w-7xl mx-auto flex items-center justify-between px-8 py-3">
          <Link to="/" className="flex items-center gap-3" data-testid="brand-logo">
            <img src={LOGO_SRC} alt="Casa da Barrica Wines" className="h-10 w-auto object-contain shrink-0" data-testid="brand-logo-img" />
            <div className="eyebrow hidden lg:block">{brand.tagline}</div>
          </Link>
          <nav className="flex items-center gap-1">
            {desktopNav.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === "/"}
                data-testid={n.testId}
                className={({ isActive }) =>
                  `px-4 py-2 rounded-full text-sm transition-colors ${isActive ? "text-[#F7F2EB] bg-[#5E1925]/40 border border-[#C28D58]/40" : "text-[#D5C7B7] hover:text-[#F7F2EB]"}`
                }
              >
                {n.label}
              </NavLink>
            ))}
            {user?.role && (user.role === "owner" || user.role === "staff") && user?.mfa_enabled && (
              <NavLink
                to="/admin"
                data-testid="nav-desktop-admin"
                className={({ isActive }) => `px-4 py-2 rounded-full text-sm ${isActive ? "text-[#F7F2EB] bg-[#5E1925]/40 border border-[#C28D58]/40" : "text-[#C28D58] hover:text-[#D8A36E]"}`}
              >
                Painel Admin
              </NavLink>
            )}
          </nav>
          <div className="flex items-center gap-3">
            <Link to="/carrinho" className="relative btn-ghost inline-flex items-center gap-2" data-testid="cart-header-btn">
              <ShoppingBag className="w-4 h-4" />
              <span>Carrinho</span>
              {count > 0 && (
                <span className="absolute -top-2 -right-2 bg-[#C28D58] text-[#120F0D] text-[10px] font-bold rounded-full min-w-[20px] h-5 px-1 inline-flex items-center justify-center" data-testid="cart-count-badge">{count}</span>
              )}
            </Link>
            {user ? (
              <div className="flex items-center gap-2">
                <span className="text-sm text-[#D5C7B7] hidden lg:inline">Olá, {user.name?.split(" ")[0]}</span>
                <button onClick={async () => { await logout(); nav("/"); }} className="btn-ghost inline-flex items-center gap-2" data-testid="logout-btn">
                  <LogOut className="w-4 h-4" /> Sair
                </button>
              </div>
            ) : (
              <Link to="/login" className="btn-copper" data-testid="login-header-btn">Entrar</Link>
            )}
          </div>
        </div>
      </header>

      {/* Mobile top brand */}
      <div className="md:hidden header-glass sticky top-0 z-40 px-4 py-2.5 flex items-center justify-between">
        <Link to="/" className="flex items-center min-w-0">
          <img src={LOGO_SRC} alt="Casa da Barrica Wines" className="h-8 w-auto object-contain shrink-0" data-testid="brand-logo-img-mobile" />
        </Link>
        {user ? (
          <button onClick={async () => { await logout(); nav("/"); }} className="text-xs text-[#C28D58]" data-testid="mobile-logout-btn">Sair</button>
        ) : (
          <Link to="/login" className="text-xs text-[#C28D58]" data-testid="mobile-login-btn">Entrar</Link>
        )}
      </div>

      <main className="max-w-7xl mx-auto px-4 md:px-8 py-6 md:py-10">
        <Outlet />
      </main>

      {/* Mobile bottom nav */}
      <nav className="md:hidden fixed bottom-0 left-0 right-0 z-50 bg-[#16120E]/95 backdrop-blur-xl border-t border-[#C28D58]/25 px-2 py-2 grid grid-cols-5 gap-1">
        {[
          { to: "/", label: "Início", icon: Home, testId: "mobile-nav-home" },
          { to: "/catalogo", label: "Explorar", icon: Compass, testId: "mobile-nav-explore" },
          { to: "/harmonizar", label: "Harmonizar", icon: Sparkles, testId: "mobile-nav-sommelier" },
          { to: "/carrinho", label: "Carrinho", icon: ShoppingBag, badge: count, testId: "mobile-nav-cart" },
          { to: "/conta", label: "Conta", icon: User, testId: "mobile-nav-account" },
        ].map(({ to, label, icon: Icon, badge, testId }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            data-testid={testId}
            className={({ isActive }) =>
              `flex flex-col items-center justify-center gap-1 py-1.5 rounded-lg transition-colors ${isActive ? "text-[#C28D58]" : "text-[#A89B8C]"}`
            }
          >
            <div className="relative">
              <Icon className="w-5 h-5" />
              {!!badge && badge > 0 && (
                <span className="absolute -top-2 -right-2 bg-[#8A2436] text-white text-[9px] rounded-full min-w-[16px] h-4 px-1 inline-flex items-center justify-center">{badge}</span>
              )}
            </div>
            <span className="text-[10px] font-medium">{label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <footer className="border-t border-[#C28D58]/20 mt-10 pb-24 md:pb-10" data-testid="site-footer">
        <div className="max-w-7xl mx-auto px-4 md:px-8 py-10 grid md:grid-cols-2 gap-8">
          <div>
            <img src={LOGO_SRC} alt="Casa da Barrica Wines" className="h-10 w-auto object-contain" data-testid="footer-logo-img" />
            <div className="eyebrow mt-2">{brand.tagline}</div>
            <p className="text-sm text-[#A89B8C] mt-4 max-w-md leading-relaxed" data-testid="footer-about">{brand.footer.about}</p>
          </div>
          <div className="md:text-right">
            <div className="eyebrow mb-3">Contato</div>
            <div className="text-sm text-[#D5C7B7] space-y-2">
              <div data-testid="footer-email">{brand.footer.email}</div>
              <div data-testid="footer-address">{brand.footer.address}</div>
              <button onClick={() => window.dispatchEvent(new CustomEvent("open-chat"))}
                className="text-[#C28D58] hover:text-[#D8A36E] transition-colors inline-flex items-center gap-1.5 md:ml-auto md:flex md:justify-end"
                data-testid="footer-atendimento">
                <MessageCircle className="w-4 h-4" /> Atendimento
              </button>
              {brand.chat?.instagram_url && (
                <a href={brand.chat.instagram_url} target="_blank" rel="noopener noreferrer"
                  className="text-[#C28D58] hover:text-[#D8A36E] transition-colors inline-flex items-center gap-1.5 md:ml-auto md:flex md:justify-end"
                  data-testid="footer-instagram">
                  <Instagram className="w-4 h-4" /> Instagram
                </a>
              )}
            </div>
          </div>
        </div>
      </footer>
      <ChatWidget />
    </div>
  );
}
