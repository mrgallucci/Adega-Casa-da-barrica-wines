import { useState } from "react";
import { api } from "@/lib/api";
import { Link } from "react-router-dom";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try { await api.post("/auth/forgot-password", { email }); } catch {}
    setSent(true);
    setLoading(false);
  };

  return (
    <div className="max-w-md mx-auto fade-up">
      <div className="surface p-8">
        <div className="eyebrow mb-2">Recuperação de conta</div>
        <h1 className="font-serif text-3xl text-[#F7F2EB] mb-4">Esqueci minha senha</h1>
        {sent ? (
          <div data-testid="forgot-sent">
            <p className="text-[#D5C7B7] text-sm mb-6">Se o e-mail existir em nossa base, enviaremos um link de redefinição válido por 30 minutos. Verifique também a caixa de spam.</p>
            <Link to="/login" className="btn-ghost inline-block">Voltar ao login</Link>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-3">
            <p className="text-sm text-[#A89B8C]">Informe o e-mail da sua conta. Enviaremos um link de uso único para criar uma nova senha.</p>
            <input data-testid="forgot-email" type="email" className="input-cellar" placeholder="E-mail" value={email} onChange={(e) => setEmail(e.target.value)} required />
            <button type="submit" disabled={loading} className="btn-primary w-full" data-testid="forgot-submit-btn">
              {loading ? "Enviando..." : "Enviar link de redefinição"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
