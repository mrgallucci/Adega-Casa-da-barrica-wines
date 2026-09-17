import "@/App.css";
import { useEffect } from "react";
import { BrowserRouter, Routes, Route, useLocation } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthProvider } from "@/context/AuthContext";
import { CartProvider } from "@/context/CartContext";
import { trackPageview } from "@/lib/analytics";
import Layout from "@/components/Layout";
import HomePage from "@/pages/HomePage";
import CatalogPage from "@/pages/CatalogPage";
import WineDetailPage from "@/pages/WineDetailPage";
import PairingPage from "@/pages/PairingPage";
import CartPage from "@/pages/CartPage";
import LoginPage from "@/pages/LoginPage";
import ForgotPasswordPage from "@/pages/ForgotPasswordPage";
import ResetPasswordPage from "@/pages/ResetPasswordPage";
import MfaRecoveryPage from "@/pages/MfaRecoveryPage";
import AccountPage from "@/pages/AccountPage";
import AdminPage from "@/pages/AdminPage";
import AuthCallback from "@/pages/AuthCallback";
import SubscriptionsPage from "@/pages/SubscriptionsPage";
import SubscribePage from "@/pages/SubscribePage";

function AppRouter() {
  const location = useLocation();
  useEffect(() => {
    if (!location.pathname.startsWith("/admin")) trackPageview(location.pathname);
  }, [location.pathname]);
  if (location.hash?.includes("session_id=")) return <AuthCallback />;
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/catalogo" element={<CatalogPage />} />
        <Route path="/vinho/:id" element={<WineDetailPage />} />
        <Route path="/harmonizar" element={<PairingPage />} />
        <Route path="/carrinho" element={<CartPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/esqueci-senha" element={<ForgotPasswordPage />} />
        <Route path="/redefinir-senha" element={<ResetPasswordPage />} />
        <Route path="/recuperar-mfa" element={<MfaRecoveryPage />} />
        <Route path="/conta" element={<AccountPage />} />
        <Route path="/assinaturas" element={<SubscriptionsPage />} />
        <Route path="/assinar/:planId" element={<SubscribePage />} />
        <Route path="/admin" element={<AdminPage />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <div className="App">
      <BrowserRouter>
        <AuthProvider>
          <CartProvider>
            <AppRouter />
            <Toaster position="top-right" theme="dark" richColors />
          </CartProvider>
        </AuthProvider>
      </BrowserRouter>
    </div>
  );
}
