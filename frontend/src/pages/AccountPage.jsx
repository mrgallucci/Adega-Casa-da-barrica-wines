import { useEffect, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { api, formatBRL } from "@/lib/api";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Download, Trash2 } from "lucide-react";
import MySubscription from "@/components/account/MySubscription";

const statusLabel = (s) => ({
  pending: "Estamos confirmando seu pagamento",
  in_process: "Estamos confirmando seu pagamento (em análise)",
  approved: "Pago",
  rejected: "Recusado",
  expired: "Expirado",
  cancelled: "Cancelado",
  refund_requested: "Reembolso solicitado",
  refunded: "Reembolsado",
}[s] || s);

export default function AccountPage() {
  const { user, loading, logout } = useAuth();
  const nav = useNavigate();
  const [orders, setOrders] = useState([]);
  const [tab, setTab] = useState("pedidos");
  const [marketing, setMarketing] = useState(false);
  const [prefs, setPrefs] = useState("");
  const [clubData, setClubData] = useState(null);
  const [pointsData, setPointsData] = useState(null);
  const [clubForm, setClubForm] = useState({ cpf: "", phone: "", cep: "", street: "", number: "", complement: "", district: "", city: "", state: "", optin_email: false, optin_whatsapp: false, accepted_terms: false, accepted_privacy: false });

  useEffect(() => {
    if (!loading && !user) nav("/login?next=/conta");
    if (user) {
      api.get("/orders").then((r) => setOrders(r.data));
      setMarketing(!!user.marketing_opt_in);
      api.get("/account/club").then((r) => {
        setClubData(r.data);
        const a = r.data.address || {};
        setClubForm((f) => ({ ...f, cpf: r.data.cpf || "", phone: r.data.phone || "",
          cep: a.cep || "", street: a.street || "", number: a.number || "", complement: a.complement || "",
          district: a.district || "", city: a.city || "", state: a.state || "",
          optin_email: !!r.data.optin_email, optin_whatsapp: !!r.data.optin_whatsapp }));
      }).catch(() => {});
      api.get("/account/points").then((r) => setPointsData(r.data)).catch(() => {});
    }
  }, [user, loading, nav]);

  if (!user) return <div className="text-[#A89B8C]">Carregando...</div>;

  const exportData = async () => {
    const { data } = await api.get("/account/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "meus-dados-minha-adega.json";
    a.click();
    toast.success("Dados exportados");
  };

  const deleteAccount = async () => {
    if (!confirm("Excluir sua conta? Seus dados pessoais serão anonimizados. Pedidos são mantidos anonimizados por obrigação fiscal. Esta ação é irreversível.")) return;
    await api.post("/account/delete");
    await logout();
    toast.success("Conta excluída e dados anonimizados");
    nav("/");
  };

  const savePrefs = async () => {
    await api.patch("/account/profile", { marketing_opt_in: marketing, wine_preferences: prefs || null });
    toast.success("Preferências salvas");
  };

  const saveClub = async () => {
    try {
      await api.patch("/account/club", {
        join: !clubData?.club_member,
        cpf: clubForm.cpf, phone: clubForm.phone,
        address: (clubForm.cep || clubForm.street || clubForm.city) ? {
          cep: clubForm.cep || null, street: clubForm.street || null, number: clubForm.number || null,
          complement: clubForm.complement || null, district: clubForm.district || null,
          city: clubForm.city || null, state: clubForm.state || null,
        } : null,
        optin_email: clubForm.optin_email, optin_whatsapp: clubForm.optin_whatsapp,
        accepted_terms: clubForm.accepted_terms, accepted_privacy: clubForm.accepted_privacy,
      });
      toast.success(clubData?.club_member ? "Dados do Clube salvos" : "Bem-vindo ao Clube Casa da Barrica Wines");
      const r = await api.get("/account/club");
      setClubData(r.data);
    } catch (err) { toast.error(err?.response?.data?.detail || "Falha ao salvar"); }
  };

  return (
    <div className="fade-up">
      <div className="mb-8">
        <div className="eyebrow mb-2">Minha Conta</div>
        <h1 className="font-serif text-4xl text-[#F7F2EB]">Olá, {user.name?.split(" ")[0]}</h1>
        <p className="text-[#A89B8C] text-sm">{user.email} · Perfil: {user.role === "owner" ? "Proprietário" : user.role === "staff" ? "Funcionário" : "Cliente"}</p>
      </div>

      {(user.role === "owner" || user.role === "staff") && user.mfa_enabled && (
        <Link to="/admin" className="btn-copper inline-block mb-8" data-testid="account-admin-link">Abrir painel administrativo</Link>
      )}
      {(user.role === "owner" || user.role === "staff") && !user.mfa_enabled && (
        <div className="surface p-4 mb-8 text-sm text-[#D8A36E]" data-testid="mfa-pending-notice">
          Sua conta é administrativa. Conclua a configuração do MFA (saia e entre novamente) para acessar o painel.
        </div>
      )}

      <div className="flex gap-2 mb-6 border-b border-[#C28D58]/20 pb-3">
        {[["pedidos", "Pedidos"], ["assinatura", "Minha Assinatura"], ["clube", "Clube & Pontos"], ["preferencias", "Preferências"], ["privacidade", "Privacidade (LGPD)"]].map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)} data-testid={`account-tab-${id}`}
            className={`px-4 py-2 rounded-full text-sm border transition-colors ${tab === id ? "bg-[#5E1925]/40 text-[#F7F2EB] border-[#C28D58]/60" : "text-[#A89B8C] border-transparent hover:text-[#F7F2EB]"}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === "assinatura" && <MySubscription />}
      {tab === "pedidos" && (
        orders.length === 0 ? (
          <div className="surface p-8 text-[#A89B8C]" data-testid="orders-empty">Você ainda não tem pedidos. <Link to="/catalogo" className="text-[#C28D58]">Explorar catálogo</Link></div>
        ) : (
          <div className="space-y-4">
            {orders.map((o) => (
              <div key={o.order_id} className="surface p-5" data-testid={`order-${o.order_id}`}>
                <div className="flex flex-wrap justify-between items-center gap-2 mb-3">
                  <div>
                    <div className="eyebrow">Pedido {o.order_id.slice(-6).toUpperCase()}</div>
                    <div className="text-xs text-[#A89B8C]">{new Date(o.created_at).toLocaleString("pt-BR")}</div>
                  </div>
                  <div className="flex gap-2">
                    <span className="badge-pill">{statusLabel(o.payment_status)}</span>
                    <span className="badge-pill">{o.fulfillment_status}</span>
                  </div>
                </div>
                <div className="text-sm text-[#D5C7B7] mb-2">{o.quote.lines.map((l) => `${l.qty}× ${l.name}${l.vintage ? ` (${l.vintage})` : ""}`).join(" • ")}</div>
                <div className="font-serif text-lg text-[#C28D58]">{formatBRL(o.quote.total)}</div>
              </div>
            ))}
          </div>
        )
      )}

      {tab === "clube" && clubData && (
        <div className="space-y-6 max-w-2xl" data-testid="club-tab">
          <div className="surface p-6" data-testid="points-summary">
            <div className="eyebrow mb-2">Programa de pontos</div>
            <div className="font-serif text-3xl text-[#C28D58]" data-testid="points-balance">{pointsData?.balance ?? 0} pontos</div>
            <p className="text-xs text-[#A89B8C] mt-2">
              {pointsData?.earn_enabled
                ? `Você ganha 1 ponto a cada R$ ${pointsData.reais_per_point} em produtos (frete não conta), creditados após a confirmação do pagamento de compras reais.`
                : "O crédito de pontos está temporariamente desativado."}
              {" "}Resgate: {pointsData?.redeem_enabled
                ? `ativo — 1 ponto = ${formatBRL(pointsData.point_value_brl)}`
                : "em breve (a loja está definindo o valor do ponto)"}.
            </p>
          </div>

          <div className="surface p-6 space-y-3" data-testid="club-panel">
            <h3 className="font-serif text-xl text-[#F7F2EB]">Clube Casa da Barrica Wines</h3>
            {clubData.club_member ? (
              <p className="text-xs text-[#7FBF7F]" data-testid="club-status">
                Membro desde {clubData.accepted_at ? new Date(clubData.accepted_at).toLocaleDateString("pt-BR") : "—"} · Termos de Adesão v{clubData.terms_version}
              </p>
            ) : (
              <p className="text-xs text-[#A89B8C]">Programa de fidelidade gratuito: acumule pontos em compras e receba ofertas exclusivas. Todos os dados abaixo são opcionais.</p>
            )}
            <div className="grid grid-cols-2 gap-2">
              <input className="input-cellar" placeholder="CPF (opcional)" value={clubForm.cpf} onChange={(e) => setClubForm({ ...clubForm, cpf: e.target.value })} data-testid="club-form-cpf" />
              <input className="input-cellar" placeholder="Telefone/WhatsApp (opcional)" value={clubForm.phone} onChange={(e) => setClubForm({ ...clubForm, phone: e.target.value })} data-testid="club-form-phone" />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <input className="input-cellar" placeholder="CEP (opcional)" value={clubForm.cep} onChange={(e) => setClubForm({ ...clubForm, cep: e.target.value })} data-testid="club-form-cep" />
              <input className="input-cellar" placeholder="Número" value={clubForm.number} onChange={(e) => setClubForm({ ...clubForm, number: e.target.value })} data-testid="club-form-number" />
            </div>
            <input className="input-cellar" placeholder="Rua (opcional)" value={clubForm.street} onChange={(e) => setClubForm({ ...clubForm, street: e.target.value })} data-testid="club-form-street" />
            <div className="grid grid-cols-3 gap-2">
              <input className="input-cellar" placeholder="Bairro" value={clubForm.district} onChange={(e) => setClubForm({ ...clubForm, district: e.target.value })} data-testid="club-form-district" />
              <input className="input-cellar" placeholder="Cidade" value={clubForm.city} onChange={(e) => setClubForm({ ...clubForm, city: e.target.value })} data-testid="club-form-city" />
              <input className="input-cellar" placeholder="UF" maxLength={2} value={clubForm.state} onChange={(e) => setClubForm({ ...clubForm, state: e.target.value })} data-testid="club-form-state" />
            </div>
            <input className="input-cellar" placeholder="Complemento (opcional)" value={clubForm.complement} onChange={(e) => setClubForm({ ...clubForm, complement: e.target.value })} data-testid="club-form-complement" />
            <label className="flex items-start gap-2 text-xs text-[#D5C7B7]">
              <input type="checkbox" className="mt-0.5" checked={clubForm.optin_email} onChange={(e) => setClubForm({ ...clubForm, optin_email: e.target.checked })} data-testid="club-form-optin-email" />
              Quero receber novidades e ofertas por <strong>e-mail</strong> (opcional)
            </label>
            <label className="flex items-start gap-2 text-xs text-[#D5C7B7]">
              <input type="checkbox" className="mt-0.5" checked={clubForm.optin_whatsapp} onChange={(e) => setClubForm({ ...clubForm, optin_whatsapp: e.target.checked })} data-testid="club-form-optin-whatsapp" />
              Quero receber novidades e ofertas por <strong>WhatsApp</strong> (opcional)
            </label>
            {!clubData.club_member && (
              <>
                <label className="flex items-start gap-2 text-xs text-[#D5C7B7]">
                  <input type="checkbox" className="mt-0.5" checked={clubForm.accepted_terms} onChange={(e) => setClubForm({ ...clubForm, accepted_terms: e.target.checked })} data-testid="club-form-accept-terms" />
                  Li e aceito os <strong>Termos de Adesão do Clube</strong> (v{clubData.current_terms_version}) *
                </label>
                <label className="flex items-start gap-2 text-xs text-[#D5C7B7]">
                  <input type="checkbox" className="mt-0.5" checked={clubForm.accepted_privacy} onChange={(e) => setClubForm({ ...clubForm, accepted_privacy: e.target.checked })} data-testid="club-form-accept-privacy" />
                  Li e aceito a <strong>Política de Privacidade</strong> (LGPD) *
                </label>
              </>
            )}
            <button onClick={saveClub} className="btn-primary" data-testid="club-save-btn">{clubData.club_member ? "Salvar dados do Clube" : "Aderir ao Clube"}</button>
          </div>

          <div className="surface p-6" data-testid="points-history">
            <h3 className="font-serif text-xl text-[#F7F2EB] mb-3">Histórico de pontos</h3>
            {(!pointsData?.history || pointsData.history.length === 0) && <p className="text-sm text-[#A89B8C]">Nenhum movimento ainda.</p>}
            <div className="space-y-2 text-xs max-h-64 overflow-auto">
              {pointsData?.history?.map((e) => (
                <div key={e.entry_id} className="flex justify-between gap-3 border-b border-[#C28D58]/10 pb-1">
                  <span className="text-[#D5C7B7]">{({ earn: "Pontos ganhos", redeem_reserve: "Reserva no checkout", redeem: "Resgate confirmado", redeem_release: "Reserva liberada", redeem_restore: "Pontos devolvidos (reembolso)", refund_revoke: "Estorno por reembolso", adjust: "Ajuste" })[e.type] || e.type}{e.amount_brl ? ` · ${formatBRL(e.amount_brl)}` : ""}</span>
                  <span className={e.points >= 0 ? "text-[#7FBF7F]" : "text-[#D88A96]"}>{e.points > 0 ? "+" : ""}{e.points} pts</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {tab === "preferencias" && (
        <div className="surface p-6 max-w-xl space-y-4" data-testid="prefs-panel">
          <p className="text-sm text-[#A89B8C]">Suas preferências declaradas ajudam o sommelier. O histórico de compras reflete o que você comprou — não o que consumiu.</p>
          <textarea data-testid="wine-preferences" className="input-cellar" rows={3} placeholder="Ex.: prefiro tintos encorpados, evito muito tanino..." value={prefs} onChange={(e) => setPrefs(e.target.value)} />
          <label className="flex items-center gap-3 text-sm text-[#D5C7B7]">
            <input type="checkbox" checked={marketing} onChange={(e) => setMarketing(e.target.checked)} data-testid="marketing-optin" />
            Aceito receber novidades e ofertas por e-mail (opcional, separado das comunicações sobre seus pedidos)
          </label>
          <button onClick={savePrefs} className="btn-primary" data-testid="save-prefs-btn">Salvar preferências</button>
        </div>
      )}

      {tab === "privacidade" && (
        <div className="surface p-6 max-w-xl space-y-4" data-testid="privacy-panel">
          <h3 className="font-serif text-xl text-[#F7F2EB]">Seus dados (LGPD)</h3>
          <p className="text-sm text-[#A89B8C]">Você pode baixar todos os seus dados ou solicitar a exclusão. Pedidos são mantidos com dados anonimizados por obrigações fiscais.</p>
          <button onClick={exportData} className="btn-ghost inline-flex items-center gap-2" data-testid="export-data-btn">
            <Download className="w-4 h-4" /> Baixar meus dados (JSON)
          </button>
          <div className="border-t border-[#C28D58]/20 pt-4">
            <button onClick={deleteAccount} className="btn-ghost inline-flex items-center gap-2 !border-[#8A2436] text-[#D88a96]" data-testid="delete-account-btn">
              <Trash2 className="w-4 h-4" /> Excluir minha conta
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
