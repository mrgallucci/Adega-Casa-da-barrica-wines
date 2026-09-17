import { useState } from "react";
import { api } from "@/lib/api";
import { useSearchParams, useNavigate, Link } from "react-router-dom";
import { toast } from "sonner";

export default function ResetPasswordPage() {
  const [sp] = useSearchParams();
  const token = sp.get("token") || "";
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const nav = useNavigate();

  const submit = async (e) => {
    e.preventDefault();
    if (pw !== pw2) return toast.error("As senhas não conferem");
    if (pw.length < 8) return toast.error("Senha deve ter ao menos 8 caracteres");
    setLoading(true);
    try {
      await api.post("/auth/reset-password", { token, new_password: pw });
      setDone(true);
      toast.success("Senha redefinida. Faça login novamente.");
      setTimeout(() => nav("/login"), 1500);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Link inválido ou expirado");
    } finally { setLoading(false); }
  };

  if (!token) {
    return (
      <div className="max-w-md mx-auto surface p-8 text-center" data-testid="reset-invalid">
        <p className="text-[#D5C7B7] mb-4">Link de redefinição inválido.</p>
        <Link to="/esqueci-senha" className="text-[#C28D58]">Solicitar novo link</Link>
      </div>
    );
  }

  return (
    <div className="max-w-md mx-auto fade-up">
      <div className="surface p-8">
        <div className="eyebrow mb-2">Nova senha</div>
        <h1 className="font-serif text-3xl text-[#F7F2EB] mb-4">Redefinir senha</h1>
        {done ? (
          <p className="text-[#D5C7B7]" data-testid="reset-success">Senha atualizada. Redirecionando para o login...</p>
        ) : (
          <form onSubmit={submit} className="space-y-3">
            <input data-testid="reset-password" type="password" className="input-cellar" placeholder="Nova senha (mín. 8)" value={pw} onChange={(e) => setPw(e.target.value)} required minLength={8} />
            <input data-testid="reset-password-confirm" type="password" className="input-cellar" placeholder="Confirmar nova senha" value={pw2} onChange={(e) => setPw2(e.target.value)} required minLength={8} />
            <p className="text-[11px] text-[#A89B8C]">Ao redefinir, todas as sessões ativas desta conta serão encerradas.</p>
            <button type="submit" disabled={loading} className="btn-primary w-full" data-testid="reset-submit-btn">
              {loading ? "Salvando..." : "Salvar nova senha"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
