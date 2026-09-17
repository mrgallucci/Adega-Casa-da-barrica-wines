import { useEffect, useRef, useState } from "react";
import { MessageCircle, X, Send, UserRound } from "lucide-react";
import { api } from "@/lib/api";

const WA_MSG = "Olá! Preciso de ajuda com a Casa da Barrica Wines.";
const WA_FALLBACK = "5524981293634";

export default function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [cfg, setCfg] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [greeted, setGreeted] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    api.get("/settings").then((r) => setCfg(r.data.chat || null)).catch(() => {});
    const openHandler = () => setOpen(true);
    window.addEventListener("open-chat", openHandler);
    return () => window.removeEventListener("open-chat", openHandler);
  }, []);

  useEffect(() => {
    if (open && !greeted && cfg) {
      setMessages([{ role: "assistant", content: cfg.greeting }]);
      setGreeted(true);
    }
  }, [open, greeted, cfg]);

  useEffect(() => { boxRef.current?.scrollTo(0, boxRef.current.scrollHeight); }, [messages]);

  const sessionId = () => {
    let s = sessionStorage.getItem("cdb_chat_session");
    if (!s) { s = crypto.randomUUID(); sessionStorage.setItem("cdb_chat_session", s); }
    return s;
  };

  const humanLink = () =>
    `https://wa.me/${cfg?.whatsapp_number || WA_FALLBACK}?text=${encodeURIComponent(WA_MSG)}`;

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setSending(true);
    try {
      const { data } = await api.post("/chat", { session_id: sessionId(), message: text });
      setMessages((m) => [...m, { role: "assistant", content: data.reply }]);
    } catch {
      setMessages((m) => [...m, { role: "assistant", content: "O Sommelier Virtual está indisponível no momento. Fale com uma pessoa pelo WhatsApp." }]);
    } finally { setSending(false); }
  };

  return (
    <>
      {!open && (
        <button onClick={() => setOpen(true)} data-testid="chat-open-btn"
          className="fixed bottom-20 right-4 md:bottom-6 md:right-6 z-50 btn-copper rounded-full px-4 py-3 inline-flex items-center gap-2 shadow-lg">
          <MessageCircle className="w-5 h-5" /> Precisa de ajuda?
        </button>
      )}
      {open && (
        <div data-testid="chat-panel"
          className="fixed bottom-20 right-4 md:bottom-6 md:right-6 z-50 w-[calc(100vw-2rem)] max-w-sm bg-[#16120E] border border-[#C28D58]/30 rounded-2xl shadow-2xl flex flex-col overflow-hidden"
          style={{ height: "28rem" }}>
          <div className="px-4 py-3 border-b border-[#C28D58]/20 flex items-center justify-between bg-[#1A1410]">
            <div>
              <div className="font-serif text-[#F7F2EB]" data-testid="chat-title">Sommelier Virtual</div>
              <div className="text-[10px] text-[#A89B8C]">Assistente automatizado · Casa da Barrica Wines</div>
            </div>
            <button onClick={() => setOpen(false)} data-testid="chat-close-btn" className="text-[#A89B8C] hover:text-[#F7F2EB]"><X className="w-5 h-5" /></button>
          </div>
          <div ref={boxRef} className="flex-1 overflow-auto p-3 space-y-2" data-testid="chat-messages">
            {messages.map((m, i) => (
              <div key={i} className={`max-w-[85%] px-3 py-2 rounded-xl text-sm leading-relaxed whitespace-pre-wrap ${m.role === "user" ? "ml-auto bg-[#5E1925]/50 text-[#F7F2EB]" : "bg-[#241C15] text-[#D5C7B7]"}`}>
                {m.content}
              </div>
            ))}
            {sending && <div className="text-xs text-[#A89B8C]" data-testid="chat-typing">Sommelier Virtual está digitando...</div>}
          </div>
          <div className="p-3 border-t border-[#C28D58]/20 space-y-2">
            <div className="flex gap-2">
              <input value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()}
                className="input-cellar flex-1" placeholder="Escreva sua dúvida..." maxLength={500} data-testid="chat-input" />
              <button onClick={send} disabled={sending || !input.trim()} data-testid="chat-send-btn" className="btn-copper px-3"><Send className="w-4 h-4" /></button>
            </div>
            <a href={humanLink()} target="_blank" rel="noopener noreferrer" data-testid="chat-human-btn"
              className="flex items-center justify-center gap-2 text-xs text-[#C28D58] hover:text-[#D8A36E] border border-[#C28D58]/30 rounded-full py-2 transition-colors">
              <UserRound className="w-4 h-4" /> Falar com uma pessoa
            </a>
          </div>
        </div>
      )}
    </>
  );
}
