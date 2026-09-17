import { useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import { toast } from "sonner";
import { ShieldCheck } from "lucide-react";
import QRCode from "react-qr-code";

export default function LoginPage() {
  const { login, register, verifyMfa, setupMfa } = useAuth();
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({ email: "", password: "", name: "", birth_date: "" });
  const [club, setClub] = useState({ join: false, cpf: "", phone: "", cep: "", street: "", number: "", complement: "", district: "", city: "", state: "", optin_email: false, optin_whatsapp: false, accepted_terms: false, accepted_privacy: false });
  const [loading, setLoading] = useState(false);
  const [mfa, setMfa] = useState(null); // {mfa_token, setup: {secret, otpauth_url} | null}
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState(null);
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const ALLOWED_NEXT = ["/carrinho", "/conta", "/catalogo", "/", "/harmonizar"];
  const rawNext = sp.get("next");
  const next = ALLOWED_NEXT.includes(rawNext) ? rawNext : "/conta";

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      if (mode === "register") {
        const clubPayload = club.join ? {
          cpf: club.cpf || null,
          phone: club.phone || null,
          address: (club.cep || club.street || club.city) ? {
            cep: club.cep || null, street: club.street || null, number: club.number || null,
            complement: club.complement || null, district: club.district || null,
            city: club.city || null, state: club.state || null,
          } : null,
          optin_email: club.optin_email, optin_whatsapp: club.optin_whatsapp,
          accepted_terms: club.accepted_terms, accepted_privacy: club.accepted_privacy,
        } : null;
        await register(form.email, form.password, form.name, form.birth_date, clubPayload);
        toast.success("Bem-vindo à Casa da Barrica Wines");
        return nav(next);
      }
      const data = await login(form.email, form.password);
      if (data.token) {
        toast.success("Bem-vindo de volta");
        return nav(next);
      }
      if (data.mfa_setup_required) {
        const setup = await setupMfa(data.mfa_token);
        setMfa({ token: data.mfa_token, setup });
        return;
      }
      if (data.mfa_required) {
        setMfa({ token: data.mfa_token, setup: null });
        return;
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Erro na autenticação");
    } finally { setLoading(false); }
  };

  const submitMfa = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const data = await verifyMfa(mfa.token, code);
      if (data.recovery_codes) {
        setRecoveryCodes(data.recovery_codes);
        return;
      }
      toast.success("Identidade verificada");
      nav(next);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Código inválido");
    } finally { setLoading(false); }
  };

  const googleLogin = () => {
    // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
    sessionStorage.setItem("login_next", next);
    const redirectUrl = window.location.origin + "/conta";
    window.location.href = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
  };

  if (recoveryCodes) {
    return (
      <div className="max-w-md mx-auto fade-up">
        <div className="surface p-8">
          <div className="eyebrow mb-2">Guarde com segurança</div>
          <h1 className="font-serif text-3xl text-[#F7F2EB] mb-4">Códigos de recuperação</h1>
          <p className="text-sm text-[#D5C7B7] mb-4">Estes 8 códigos de <strong>uso único</strong> permitem entrar se você perder o autenticador. Eles são exibidos apenas agora — guarde-os fora do celular usado para o MFA.</p>
          <div className="grid grid-cols-2 gap-2 mb-6" data-testid="recovery-codes">
            {recoveryCodes.map((c) => (
              <code key={c} className="p-2 rounded bg-[#120F0D] border border-[#C28D58]/30 text-[#C28D58] font-mono text-xs text-center">{c}</code>
            ))}
          </div>
          <button className="btn-primary w-full" onClick={() => { toast.success("MFA configurado"); nav(next); }} data-testid="recovery-done-btn">Guardei os códigos — continuar</button>
        </div>
      </div>
    );
  }

  if (mfa) {
    return (
      <div className="max-w-md mx-auto fade-up">
        <div className="surface p-8">
          <div className="eyebrow mb-2 flex items-center gap-2"><ShieldCheck className="w-4 h-4" /> Verificação em duas etapas</div>
          <h1 className="font-serif text-3xl text-[#F7F2EB] mb-6">{mfa.setup ? "Ative o MFA" : "Código de verificação"}</h1>
          {mfa.setup && (
            <div className="mb-6 text-sm text-[#D5C7B7] space-y-3">
              <p>Contas administrativas exigem MFA. Escaneie o QR code com seu app autenticador (Google Authenticator, Authy etc.) ou insira a chave manualmente:</p>
              <div className="bg-white p-4 rounded-xl w-fit mx-auto" data-testid="mfa-qrcode">
                <QRCode value={mfa.setup.otpauth_url} size={160} />
              </div>
              <code className="block p-3 rounded-lg bg-[#120F0D] border border-[#C28D58]/30 text-[#C28D58] font-mono text-sm break-all" data-testid="mfa-secret">{mfa.setup.secret}</code>
              <p className="text-[11px] text-[#A89B8C]">O MFA só será ativado após você informar um código válido gerado pelo app.</p>
            </div>
          )}
          <form onSubmit={submitMfa} className="space-y-3">
            <input data-testid="mfa-code" className="input-cellar text-center tracking-[0.4em] text-lg" placeholder={mfa.setup ? "000000" : "Código do app ou de recuperação"} value={code} onChange={(e) => setCode(e.target.value.trim())} required />
            {!mfa.setup && <p className="text-[11px] text-[#A89B8C]">Sem acesso ao autenticador? Use um dos códigos de recuperação gerados na ativação, ou <Link to="/recuperar-mfa" className="text-[#C28D58] hover:text-[#D8A36E]" data-testid="mfa-lost-link">recupere o acesso por e-mail</Link>.</p>}
            <button type="submit" disabled={loading} className="btn-primary w-full" data-testid="mfa-verify-btn">{loading ? "Verificando..." : "Verificar"}</button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-md mx-auto fade-up">
      <div className="surface p-8">
        <div className="eyebrow mb-2">Acesso da adega</div>
        <h1 className="font-serif text-3xl text-[#F7F2EB] mb-6">{mode === "login" ? "Entrar" : "Criar conta"}</h1>
        {next === "/carrinho" && (
          <div className="mb-4 p-3 rounded-xl border border-[#C28D58]/40 bg-[#5E1925]/20 text-sm text-[#D5C7B7]" data-testid="login-checkout-banner">
            Para finalizar sua compra, entre na sua conta ou cadastre-se.
          </div>
        )}
        <button onClick={googleLogin} className="btn-ghost w-full mb-4" data-testid="google-login-btn">
          Entrar com Google
        </button>
        <div className="divider-gold my-4" />
        <form onSubmit={submit} className="space-y-3">
          {mode === "register" && (
            <>
              <input data-testid="auth-name" className="input-cellar" placeholder="Nome" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
              <div>
                <label className="text-xs text-[#A89B8C] block mb-1">Data de nascimento (venda proibida para menores de 18 anos)</label>
                <input data-testid="auth-birthdate" type="date" className="input-cellar" value={form.birth_date} onChange={(e) => setForm({ ...form, birth_date: e.target.value })} required />
              </div>
            </>
          )}
          <input data-testid="auth-email" type="email" className="input-cellar" placeholder="E-mail" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
          <input data-testid="auth-password" type="password" className="input-cellar" placeholder="Senha (mín. 8 caracteres)" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required minLength={8} />
          {mode === "register" && (
            <div className="border border-[#C28D58]/25 rounded-xl p-3 space-y-3" data-testid="club-join-block">
              <label className="flex items-start gap-2 text-sm text-[#D5C7B7]">
                <input type="checkbox" className="mt-1" checked={club.join} onChange={(e) => setClub({ ...club, join: e.target.checked })} data-testid="club-join-checkbox" />
                <span>Quero participar do <strong className="text-[#C28D58]">Clube Casa da Barrica Wines</strong> — programa de fidelidade gratuito, com pontos em compras</span>
              </label>
              {club.join && (
                <div className="space-y-3" data-testid="club-join-fields">
                  <p className="text-[11px] text-[#A89B8C]">Os dados abaixo são <strong>opcionais</strong> e podem ser completados depois em Minha Conta.</p>
                  <div className="grid grid-cols-2 gap-2">
                    <input className="input-cellar" placeholder="CPF (opcional)" value={club.cpf} onChange={(e) => setClub({ ...club, cpf: e.target.value })} data-testid="club-cpf" />
                    <input className="input-cellar" placeholder="Telefone/WhatsApp (opcional)" value={club.phone} onChange={(e) => setClub({ ...club, phone: e.target.value })} data-testid="club-phone" />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <input className="input-cellar" placeholder="CEP (opcional)" value={club.cep} onChange={(e) => setClub({ ...club, cep: e.target.value })} data-testid="club-cep" />
                    <input className="input-cellar" placeholder="Número" value={club.number} onChange={(e) => setClub({ ...club, number: e.target.value })} data-testid="club-number" />
                  </div>
                  <input className="input-cellar" placeholder="Rua (opcional)" value={club.street} onChange={(e) => setClub({ ...club, street: e.target.value })} data-testid="club-street" />
                  <div className="grid grid-cols-3 gap-2">
                    <input className="input-cellar" placeholder="Bairro" value={club.district} onChange={(e) => setClub({ ...club, district: e.target.value })} data-testid="club-district" />
                    <input className="input-cellar" placeholder="Cidade" value={club.city} onChange={(e) => setClub({ ...club, city: e.target.value })} data-testid="club-city" />
                    <input className="input-cellar" placeholder="UF" maxLength={2} value={club.state} onChange={(e) => setClub({ ...club, state: e.target.value })} data-testid="club-state" />
                  </div>
                  <input className="input-cellar" placeholder="Complemento (opcional)" value={club.complement} onChange={(e) => setClub({ ...club, complement: e.target.value })} data-testid="club-complement" />
                  <label className="flex items-start gap-2 text-xs text-[#D5C7B7]">
                    <input type="checkbox" className="mt-0.5" checked={club.optin_email} onChange={(e) => setClub({ ...club, optin_email: e.target.checked })} data-testid="club-optin-email" />
                    Quero receber novidades e ofertas por <strong>e-mail</strong> (opcional)
                  </label>
                  <label className="flex items-start gap-2 text-xs text-[#D5C7B7]">
                    <input type="checkbox" className="mt-0.5" checked={club.optin_whatsapp} onChange={(e) => setClub({ ...club, optin_whatsapp: e.target.checked })} data-testid="club-optin-whatsapp" />
                    Quero receber novidades e ofertas por <strong>WhatsApp</strong> (opcional)
                  </label>
                  <label className="flex items-start gap-2 text-xs text-[#D5C7B7]">
                    <input type="checkbox" className="mt-0.5" checked={club.accepted_terms} onChange={(e) => setClub({ ...club, accepted_terms: e.target.checked })} data-testid="club-accept-terms" required={club.join} />
                    Li e aceito os <strong>Termos de Adesão do Clube</strong> (v1.0) *
                  </label>
                  <label className="flex items-start gap-2 text-xs text-[#D5C7B7]">
                    <input type="checkbox" className="mt-0.5" checked={club.accepted_privacy} onChange={(e) => setClub({ ...club, accepted_privacy: e.target.checked })} data-testid="club-accept-privacy" required={club.join} />
                    Li e aceito a <strong>Política de Privacidade</strong> (LGPD) *
                  </label>
                </div>
              )}
            </div>
          )}
          <button type="submit" disabled={loading} className="btn-primary w-full" data-testid="auth-submit-btn">
            {loading ? "Enviando..." : mode === "login" ? "Entrar" : "Cadastrar"}
          </button>
        </form>
        <div className="text-center mt-4 text-sm text-[#A89B8C] space-y-2">
          {mode === "login" ? (
            <>
              <div>Não tem conta? <button className="text-[#C28D58] hover:text-[#D8A36E]" onClick={() => setMode("register")} data-testid="switch-register">Cadastre-se</button></div>
              <div><Link to="/esqueci-senha" className="text-[#C28D58] hover:text-[#D8A36E]" data-testid="forgot-password-link">Esqueci minha senha</Link></div>
            </>
          ) : (
            <>Já tem conta? <button className="text-[#C28D58] hover:text-[#D8A36E]" onClick={() => setMode("login")} data-testid="switch-login">Entrar</button></>
          )}
        </div>
        <p className="text-[10px] text-[#A89B8C] mt-6 text-center">Venda e consumo de bebidas alcoólicas são proibidos para menores de 18 anos. A maioridade é verificada no cadastro, no checkout e conferida com documento na entrega.</p>
      </div>
    </div>
  );
}
