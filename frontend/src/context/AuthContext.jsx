import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/me");
      setUser(data);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (window.location.hash?.includes("session_id=")) {
      setLoading(false);
      return;
    }
    refresh();
  }, [refresh]);

  // Retorna a resposta bruta: pode ser {token,user} ou {mfa_required|mfa_setup_required, mfa_token}
  const login = async (email, password) => {
    const { data } = await api.post("/auth/login", { email, password });
    if (data.token) {
      localStorage.setItem("adega_token", data.token);
      setUser(data.user);
    }
    return data;
  };

  const verifyMfa = async (mfa_token, code) => {
    const { data } = await api.post("/auth/mfa/verify", { mfa_token, code });
    localStorage.setItem("adega_token", data.token);
    setUser(data.user);
    return data.user;
  };

  const setupMfa = async (mfa_token) => {
    const { data } = await api.post("/auth/mfa/setup", { mfa_token });
    return data; // {secret, otpauth_url}
  };

  const register = async (email, password, name, birth_date, club = null) => {
    const { data } = await api.post("/auth/register", { email, password, name, birth_date, club });
    localStorage.setItem("adega_token", data.token);
    setUser(data.user);
    return data.user;
  };

  const logout = async () => {
    try { await api.post("/auth/logout"); } catch {}
    localStorage.removeItem("adega_token");
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, refresh, setUser, verifyMfa, setupMfa }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
