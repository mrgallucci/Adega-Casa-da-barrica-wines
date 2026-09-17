import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";

export default function AuthCallback() {
  const location = useLocation();
  const nav = useNavigate();
  const { setUser } = useAuth();

  useEffect(() => {
    const hash = location.hash || "";
    const m = hash.match(/session_id=([^&]+)/);
    if (!m) { nav("/login"); return; }
    const session_id = decodeURIComponent(m[1]);
    (async () => {
      try {
        const { data } = await api.post("/auth/emergent-session", { session_id });
        setUser(data);
        const ALLOWED = ["/carrinho", "/conta", "/catalogo", "/", "/harmonizar"];
        const saved = sessionStorage.getItem("login_next");
        sessionStorage.removeItem("login_next");
        const next = ALLOWED.includes(saved) ? saved : "/conta";
        window.history.replaceState(null, "", next);
        toast.success("Autenticado com Google");
        nav(next);
      } catch {
        toast.error("Falha ao autenticar.");
        nav("/login");
      }
    })();
    // eslint-disable-next-line
  }, []);

  return <div className="p-10 text-center text-[#A89B8C]">Autenticando...</div>;
}
