import { useState } from "react";
import { api } from "@/lib/api";
import { useSearchParams, Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";

export default function MfaRecoveryPage() {
  const [sp] = useSearchParams();
  const token = sp.get("token") || "";
  const [email, setEmail] = useState("");
  const [recCode, setRecCode] = useState("");
  const [sent, setSent] = useState(false);
  const [done, setDone] = useState(false);
  const [loading, setLoading] = useState(false);
  const nav = useNavigate();

  const requestRecovery = async (e) => {
    e.preventDefault();
    setLoading(true);
    try { await api.post("/auth/mfa/recovery-request", { email }); } catch {}
    setSent(true);
    setLoading(false);
  };

  const confirmRecovery = async () => {
    setLoading(true);
    try {
      await api.post("/auth/mfa/recovery-confirm", { token, recovery_code: recCode });
      setDone(true);
      toast.success("MFA removido. Entre com sua senha e cadastre um novo autenticador.");
      setTimeout(() => nav("/login"), 2500);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Link inválido, já utilizado ou expirado");
    } finally { setLoading(false); }
  };

  return (
    <div className="max-w-md mx-auto fade-up">
      <div className="surface p-8">
        <div className="eyebrow mb-2">Recuperação de acesso</div>
        <h1 className="font-serif text-3xl text-[#F7F2EB] mb-4">Perdi meu autenticador</h1>
        {token ? (
          done ? (
            <p className="text-[#D5C7B7]" data-testid="mfa-recovery-done">MFA removido com segurança. Redirecionando para o login...</p>
          ) : (
            <div data-testid="mfa-recovery-confirm">
              <p className="text-sm text-[#D5C7B7] mb-4">O link de e-mail sozinho não remove o MFA. Informe também um dos <strong>códigos de recuperação de uso único</strong> gerados quando você ativou o autenticador. Todas as sessões serão encerradas e você entrará com sua senha para cadastrar um novo autenticador.</p>
              <input data-testid="mfa-recovery-code" className="input-cellar mb-3 font-mono" placeholder="Código de recuperação (ex.: ab12cd-ef3456)" value={recCode} onChange={(e) => setRecCode(e.target.value.trim())} />
              <p className="text-[11px] text-[#A89B8C] mb-3">Sem os códigos de recuperação, o acesso segue o procedimento manual de verificação de identidade documental com a operação da loja.</p>
              <button onClick={confirmRecovery} disabled={loading || !recCode} className="btn-primary w-full" data-testid="mfa-recovery-confirm-btn">
                {loading ? "Processando..." : "Confirmar recuperação de MFA"}
              </button>
            </div>
          )
        ) : sent ? (
          <div data-testid="mfa-recovery-sent">
            <p className="text-[#D5C7B7] text-sm mb-6">Se a conta existir e tiver MFA ativo, enviaremos um link de recuperação válido por 30 minutos.</p>
            <Link to="/login" className="btn-ghost inline-block">Voltar ao login</Link>
          </div>
        ) : (
          <form onSubmit={requestRecovery} className="space-y-3">
            <p className="text-sm text-[#A89B8C]">Informe o e-mail da sua conta administrativa. Enviaremos um link de uso único. Para concluir, será exigido também um <strong>código de recuperação</strong> gerado na ativação do MFA — o e-mail sozinho não remove a proteção.</p>
            <input data-testid="mfa-recovery-email" type="email" className="input-cellar" placeholder="E-mail" value={email} onChange={(e) => setEmail(e.target.value)} required />
            <button type="submit" disabled={loading} className="btn-primary w-full" data-testid="mfa-recovery-request-btn">
              {loading ? "Enviando..." : "Enviar link de recuperação"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
