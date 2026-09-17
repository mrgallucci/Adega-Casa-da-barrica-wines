# PRD — Minha Adega (E-commerce de Vinhos Premium)

## Problema (resumo do enunciado original)
Aplicativo completo de comércio eletrônico de vinhos em pt-BR (R$), experiência premium de adega rústica sofisticada, PWA responsivo, catálogo detalhado, harmonização com IA, carrinho/checkout, perfis (proprietário/funcionário/cliente), painel admin, estoque com concorrência, pagamentos via provedor (Mercado Pago), LGPD, relatórios e segurança OWASP ASVS 2.

## Decisões do usuário
- Pagamento: Mercado Pago (Checkout Pro) em preparação/homologação; **modo demo até aprovação explícita**.
- Auth: JWT e-mail/senha + Google (Emergent Auth); MFA TOTP obrigatório para admins + códigos de recuperação.
- IA: Claude Sonnet 5 via Emergent LLM Key (fallback por regras).
- E-mails: Resend gerenciado pela Emergent (implementado, 6 templates).

## Arquitetura
- Frontend: React 19 + Tailwind + shadcn. Rotas: /, /catalogo, /vinho/:id, /harmonizar, /carrinho, /login, /esqueci-senha, /redefinir-senha, /conta, /admin.
- Backend: FastAPI + Motor/MongoDB (/api/*): server.py + email_service.py + payments_mp.py.
- PWA: manifest.json + sw.js (cache apenas de assets estáticos públicos; /api e páginas privadas excluídos).
- Crons (.emergent/crons.yml): release-reservations (*/15min) + daily-backup (03h America/Sao_Paulo). Execuções registradas em cron_runs (monitoramento no admin > Frete & Entrega).
- Backups: scripts/backup.sh + restore.sh (testados); cron diário grava em /app/backups (disco do pod — efêmero; produção exige Mongo gerenciado com snapshots).

## Implementado
### Iteração 1 — MVP (catálogo, IA, carrinho, checkout demo, admin básico)
### Iteração 2 — Segurança/estoque/pagamentos/e-mails
- Senha exposta removida; reset por link (uso único, 30 min, anti-enumeração, revoga sessões); Argon2id; rate limit; MFA TOTP admin; papéis server-side; auditoria append-only.
- Variantes safra/volume; reservas atômicas 30 min; cron de expiração; movimentações de estoque; cupons configuráveis; MP preparado (Checkout Pro + webhook HMAC); LGPD export/delete; maioridade; relatórios demo vs real; PWA.
### Iteração 3 — Homologação
- **Bug corrigido**: pagamento confirmado após expiração da reserva era ignorado (pedido ficava "expired" mesmo pago). Agora: status do provedor prevalece → pedido "approved" + fulfillment "revisao_estoque" + flag needs_stock_review.
- **Fluxo de resolução** (admin > Pedidos): "Baixar estoque e enviar" (falha 409 se sem estoque) ou "Reembolsar" (marca refunded + orienta estorno no provedor + e-mail ao cliente).
- **Frete**: tabela própria administrável (Admin > Frete & Entrega): regiões por prefixo de CEP, preço, prazo. CEP fora das regiões → checkout bloqueado (400). Instruções operacionais de maioridade na entrega documentadas no painel.
- **MFA recovery codes**: 8 códigos de uso único (SHA-256 no banco) exibidos uma vez no setup; aceitos na tela de MFA.
- **Reserva de 30 min garantida**: liberação preguiçosa por vinho antes de cada nova reserva (não depende só do cron de 15 min — sem espera de até 45 min).
- **Monitoramento de crons**: cron_runs com status ok/erro por execução (run_id idempotente), visível no admin.
- **Anonimização real na exclusão**: endereço removido, CEP reduzido a prefixo, nascimento apagado, sessões bloqueadas (401 pós-delete, testado).
- Seed de 2 regiões de entrega demo (SP capital R$29,90/1-3 dias; capitais Sudeste R$49,90/3-6 dias) — marcadas como exemplo, editáveis.

## Testes executados (evidências)
| Cenário | Resultado |
|---|---|
| pytest backend (33 casos) | 33/33 |
| Concorrência última unidade (2 clientes) | 200 + 409, sem estoque negativo |
| Reserva expira → liberação preguiçosa (sem esperar cron) | 2º cliente compra imediatamente; 1º pedido "expired" |
| Pagamento tardio (pós-expiração) | approved + revisao_estoque + flag |
| Resolução: fulfill com estoque / sem estoque / refund | 200 + baixa / 409 / refunded+cancelado |
| Webhook duplicado (mesma transição) | baixa única (idempotente) |
| Webhook fora de ordem (expired→approved) | provedor autoritativo, sinaliza revisão |
| Webhook sem/adulterada assinatura | 401 |
| MFA recovery code: uso único | 200 depois 401 no reuso |
| Frete: zona SP/RJ ok, CEP fora da área bloqueado | 29,90 / 49,90 / 400 |
| Cron backup + release: execução + log de monitoramento | ok em cron_runs |
| Exclusão LGPD: anonimização de endereço/CEP/nascimento | ok; sessão 401 |
| E-mails: 6 templates aceitos (202) para delivered@resend.dev | aceitação API ✓ |
| E-mail para destinatário falso | 422 "undeliverable recipient" (proteção de bounce do proxy) |

**Nota sobre e-mails**: o que está comprovado é a *aceitação pela API* do proxy (202) e a *rejeição antecipada de destinatários inválidos* (422). Entrega efetiva na caixa do cliente (MG/ESP) depende de reputação do domínio gerenciado e deve ser observada em operação real; o proxy não expõe webhook de entrega nesta integração.

### Iteração 4 — Correções de homologação (pré-pagamentos)
- **Reembolso em duas fases**: resolve-stock refund agora cria `refund_requested` (nunca "refunded" direto). Confirmação via `/admin/orders/{id}/refund/confirm`: em modo MP consulta o provedor (GET payment, executa POST /v1/payments/{id}/refunds com X-Idempotency-Key estável se ainda não enviado) e só marca `refunded` quando o provedor confirma, registrando id/valor/data/confirmado-por. E-mail de estorno ao cliente só após confirmação. Repetição idempotente (já em andamento → 200 sem duplicar), reembolso parcial suportado (amount), valor acima do total rejeitado (400), tudo auditado (refund_requested/refund_confirmed) e restrito a admin.
- **Modo "Vendas suspensas"** (Admin > Configurações): checkout novo → 403 com mensagem clara + banner no carrinho e botão desabilitado. Webhooks, confirmações de pagamentos anteriores e gestão de pedidos/reembolsos seguem ativos (testado).
- **Frete**: zonas seed são exemplos NÃO confirmados — checkout bloqueia até o proprietário confirmar região/preço/prazo no painel (botão "Confirmar"). Zonas confirmadas podem ser a solução definitiva de frete.
- **Backups externos**: mongodump diário (cron 03h) enviado ao Emergent Object Storage (fora do pod), registro em `backups`, botão "Testar restauração" baixa do storage externo e restaura em banco scratch (restore_verify) — testado com arquivo local apagado. Falhas vão para cron_runs.
- payment_mode "demo" segue separando pedidos demo:true dos reais nos relatórios.

### Iteração 6 — Refino da recuperação de MFA (exceção única vs. fluxo legítimo)
- **Reset de senha volta a PRESERVAR o MFA legítimo.** A limpeza de MFA no reset só ocorre quando a conta tem a flag `mfa_residual_test` — marcada manualmente pelo operador com evidência de auditoria (configuração criada por teste em 14/06, sem códigos de recuperação jamais emitidos). A flag é **consumida no primeiro reset** (uso único): resets seguintes preservam o MFA. Ausência de códigos de recuperação NÃO é usada como prova automática.
- **Fluxo separado de recuperação de MFA** ("Perdi meu autenticador"): `/auth/mfa/recovery-request` (anti-enumeração, rate limit, link de uso único 30 min por e-mail) → `/recuperar-mfa` (UI) → `/auth/mfa/recovery-confirm`: remove o MFA perdido, revoga todas as sessões/JWTs (password_version) e exige login com senha + recadastro do autenticador. Não redefine senha nem concede acesso.
- **Testes nunca mais tocam a conta real do proprietário**: o teste de anti-enumeração do forgot-password passou a usar conta de teste (antes criava tokens e enviava e-mail ao e-mail real).
- Testes novos (48/48, 2× consecutivas): reset preserva MFA legítimo; flag residual consumida uma única vez; fluxo de recuperação completo (anti-enumeração, uso único, sessões revogadas, MFA limpo).
- **Causa raiz comprovada**: teste anterior ativou `mfa_enabled` com segredo TOTP de teste na conta real do proprietário — o usuário nunca viu o QR nem recebeu códigos de recuperação.
- **Fluxo corrigido**: conta admin sem MFA confirmado entra em sessão restrita de configuração (mfa_token não acessa nada além do setup), com QR code + chave manual na UI; MFA só é ativado após código TOTP válido; 8 códigos de recuperação exibidos uma única vez.
- **Recuperação segura sem burlar proteção**: reset de senha por e-mail (uso único) invalida MFA residual e força recadastro do autenticador.
- Link "Painel Admin" só aparece com MFA ativo.
- Testes: E2E 12 passos + frontend 8/8 + pytest 45/45.

### Iteração 7 — Cofre de credenciais (formulário seguro)
- Manage → Secrets do Emergent não tem edição no preview (suporte confirmou: edição visual só pós-deploy; preview usa backend/.env). Alternativa implementada: **formulário seguro no Admin > Configurações** (somente owner com MFA): valores vão direto ao servidor (Mongo `app_secrets` + memória runtime), nunca retornados por API, nunca logados, nunca no frontend. GET /admin/secrets/status expõe apenas flags configurado/não configurado.
- `payments_mp.py`: env vars têm prioridade; cofre do banco é fallback (carregado no startup e ao salvar).
- Teste de conexão ao salvar (GET /users/me do MP) — reporta sucesso/falha sem expor o token.
- Testes: fake token salvo → conexão falha com gracia (403 do MP) sem vazar valor; staff → 403; mp_test ativável com credenciais; checkout com token inválido → 502 gracioso; settings público não vaza. 56/56 pytest.

### Iteração 9 — Isolamento dos testes automatizados
- **Banco exclusivo de testes `adega_test`** + backend isolado na porta 8002 (conftest sobe/derruba automaticamente). Usuário Mongo dedicado `adega_tester` com readWrite somente em `adega_test`. **PENDENTE registrado: o mongod local está sem autenticação — o isolamento por permissões só é efetivo quando a auth estiver ativa (ex.: Atlas/produção).**
- **Trava sem fallback**: conftest aborta a suíte se o alvo for o banco da loja ou não terminar em `_test` (testado: `ADEGA_TEST_DB=test_database` e `qualquer_coisa` → aborto imediato). Todos os helpers de acesso direto passam pela trava.
- Suíte migrada: zero referências ao banco da loja fora do conftest; `test_admin_secrets` usa o backend isolado (sem fallback para o preview); duplicata de config removida; `authSource=admin` na URI do usuário com escopo.
- **Segredos em repouso criptografados**: Fernet (AES) com chave derivada de segredo de ambiente do servidor (`SECRETS_ENC_KEY` ou JWT_SECRET) — **a chave nunca fica no banco**. Migração transparente de valores legados.
- Verificação direcionada (3 testes, dados fictícios): passou; banco da loja idêntico antes/depois (134 users / 114 orders / 10 wines / 0 app_secrets); banco isolado `adega_test` usado (8 wines seed fictícios).
- Credenciais MP: continuam NÃO cadastradas (usuário vai repreencher o formulário seguro). Homologação MP: **pendente** — testes preparados não são testes aprovados.
- **Recuperação de MFA endurecida**: o link de e-mail sozinho NÃO remove mais o MFA. `/auth/mfa/recovery-confirm` agora exige também um **código de recuperação de uso único** (outro fator previamente cadastrado). Link não é consumido em tentativa com código errado. Sem códigos disponíveis → procedimento manual de verificação de identidade documental fora do sistema.
- **Sequência de ataque testada explicitamente**: "redefinir senha por e-mail → recuperar MFA por e-mail" NÃO assume a conta — MFA preservado no reset, recovery-confirm sem código → 400, login com senha nova continua exigindo TOTP (sem token emitido).
- **Flag residual**: verificado — o proprietário recuperou o acesso pelo fluxo de e-mail (versão anterior) e cadastrou MFA legítimo (8 códigos); a flag `mfa_residual_test` não havia sido consumida e foi **retirada pelo operador com registro em auditoria** (evitaria apagar o MFA legítimo num reset futuro). Nenhum teste na conta real.
- Credenciais MP confirmadas salvas (token + webhook secret); homologação em modo de testes iniciada.
- **Incidente**: o fixture de limpeza do teste do cofre (`test_admin_secrets.py`) apagava `app_secrets` inteiro e removeu as credenciais reais salvas pelo usuário; com o restart, a memória também se perdeu. Corrigido: fixture agora faz **snapshot+restore** (nunca destrói dados reais); adicionado endpoint `/admin/secrets/resync` (owner) para regravar memória→cofre sem trafegar valores. Usuário precisa repreencher o formulário seguro uma vez.
1. Criar app no painel MP (Developers → Suas integrações → Criar aplicação → Checkout Pro).
2. **Testes → Credenciais de teste**: Access Token (APP_USR-...). O ambiente é definido pela configuração payment_mode da loja, não pelo prefixo do token.
3. **Webhooks → Configurar notificações** (seção separada!): URL `https://adega-premium-3.preview.emergentagent.com/api/webhooks/mercadopago`, evento Pagamentos → copiar a assinatura secreta gerada após salvar.
4. Inserir `MP_ACCESS_TOKEN` e `MP_WEBHOOK_SECRET` nas variáveis de ambiente do backend (painel Emergent → Environment Variables) — nunca pelo chat.
5. Criar contas de teste vendedor/comprador BR; cartões de teste MP: aprovado (nome APRO), recusado (OTHE), pendente (CONT).
6. Antes de produção: definir domínio definitivo e atualizar PUBLIC_APP_URL (back_urls + webhook), confirmar persistência do Mongo (Atlas/gerenciado) e confirmar zonas de frete reais.

## Testes executados (evidências)
| Cenário | Resultado |
|---|---|
| pytest backend completo (backend_test + test_iteration3) | 45/45 |
| Reembolso solicitado não aparece como reembolsado | refund_requested, e-mail não enviado |
| Repetição da solicitação / dupla confirmação | idempotente / 400 |
| Reembolso parcial e valor acima do total | registrado / 400 |
| Suspensão: novo checkout | 403 + banner no frontend |
| Suspensão: conciliação de pagamento anterior + gestão admin | 200 |
| Zonas exemplo sem confirmação | checkout 400 |
| Backup externo → apaga local → restaura do storage | wines/users/orders restaurados |
| Frontend (testing agent iter 3): painel resolução, frete, mobile | 100% |

## Simulado (aguardando credenciais MP)
Fluxo real no Checkout Pro: aprovado/recusado/pendente/expirado/reembolsado, retorno ao site, webhooks reais, falha de comunicação com o provedor, estorno real via API.
1. Criar app no painel MP (Developers → Suas integrações → Criar aplicação → Checkout Pro).
2. Copiar **Credenciais de teste** (Pruebas/Testes → Credenciais de teste): Access Token (APP_USR-...).
3. Em **Webhooks → Configurar notificações** (seção SEPARADA das credenciais), cadastrar a URL `https://<dominio-do-app>/api/webhooks/mercadopago` para eventos de Pagamentos e copiar a **assinatura secreta** gerada (ela só aparece após salvar a URL).
4. Inserir `MP_ACCESS_TOKEN` e `MP_WEBHOOK_SECRET` em backend/.env (Settings → Environment Variables do projeto no Emergent, ou edição local). Nunca pelo chat.
5. Criar contas de teste vendedor/comprador (mesmo país, BR) para compras de teste.
6. Após eu rodar a bateria de testes de pagamento, aprovar explicitamente a saída do modo demo.

## Simulado (não comprovado no provedor ainda)
- Aprovado/recusado/pendente/expirado/reembolsado via MP real; redirect de retorno; fechamento do navegador antes do retorno; webhooks reais atrasados/fora de ordem; falha temporária de comunicação com o MP.

## Bloqueios para produção
Credenciais MP + bateria de pagamentos; frete real por transportadora (tabela própria já administrável); verificação documental na entrega (operacional); revisão jurídica/pentest independente; backups gerenciados fora do pod.

## 2026-06 — Edição visual (agentic edit)
- Tagline do header alterada em `Layout.jsx` (linha 27): "Curadoria de Sommelier" → "Curadoria, Histórias & Descobertas em cada vinho". Verificado via screenshot (desktop).
- Status MP: credencial de teste aceita para criar preferências no sandbox (verificado em sessão anterior). Homologação de pagamentos (aprovado/recusado/webhooks) PENDENTE, aguardando aprovação do usuário. Pagamentos reais DESATIVADOS.

## 2026-06 — Edição visual 2 (agentic edit)
- Nome da marca no header desktop alterado em `Layout.jsx` (linha 26): "Minha Adega" → "Casa da Barrica Wines". Verificado via screenshot.
- ATENÇÃO: header mobile (Layout.jsx linha 80) ainda exibe "Minha Adega" — pendente decisão do usuário sobre rebrand completo.

## 2026-06 — Rebrand completo: "Minha Adega" → "Casa da Barrica Wines"
- Frontend: Layout.jsx (header desktop+mobile), LoginPage.jsx (toast), index.html (title+meta), manifest.json (name="Casa da Barrica Wines", short_name="Casa da Barrica"), sw.js (comentário).
- Backend: server.py (FastAPI title, store_name defaults, issuer MFA, health), email_service.py (remetente + 6 assuntos), .env EMAIL_FROM_NAME.
- Banco: store_settings.store_name atualizado para "Casa da Barrica Wines" (matched=1, modified=1).
- Verificado: health retorna "Casa da Barrica Wines API"; zero referências a "Minha Adega" fora de docstrings de teste; mobile 390px PASS.
- Nota: MFA issuer mudou — usuários já cadastrados não são afetados (segredo TOTP inalterado); só novos cadastros veem o novo nome no app autenticador.

## 2026-06 — Conteúdo da marca editável pelo painel (etapa autorizada)
- Backend (server.py): campo `tagline` adicionado a SettingsIn, GET /api/settings, defaults de get_settings() e seed inicial.
- Frontend: Layout.jsx busca GET /api/settings e renderiza store_name+tagline dinamicamente (desktop e mobile, com fallback hardcoded). AdminPage.jsx: novo campo "Tagline" na aba Config (data-testid=settings-tagline) e saveSettings envia tagline.
- Banco: store_settings.tagline gravado ("Curadoria, Histórias & Descobertas em cada vinho").
- Verificado: GET /api/settings retorna store_name+tagline; header desktop PASS; mobile 390px PASS sem overflow.
- Escopo respeitado: sem logo, sem geração de imagens, sem outras melhorias. Pagamentos reais DESATIVADOS.

## 2026-06 — Conteúdo editável pelo painel (etapa final autorizada)
- Backend (server.py): DEFAULT_HOME_CONTENT (16 chaves: hero/destaques/CTA, textos+destinos de botões), DEFAULT_FOOTER_CONTENT (about/email/phone/address), whitelist ALLOWED_CONTENT_LINKS, validador _validate_content (links internos, e-mail, máx. 500 chars, rejeita chaves desconhecidas). GET /api/settings expõe home+footer (merge com defaults). PUT /admin/settings agora é SOMENTE OWNER + valida conteúdo; merge preserva campos não enviados.
- Frontend: HomePage.jsx usa conteúdo da API (fallback = conteúdo atual); Layout.jsx ganhou rodapé (data-testid=site-footer) com apresentação+contatos editáveis; AdminPage.jsx aba Config tem editores "Conteúdo da página inicial" e "Rodapé" (destinos como select de rotas válidas); formulário de vinho ganhou campo `story` (descrição) — backend e WineDetailPage já suportavam, apenas faltava no form.
- Testes: tests/test_content_settings.py — 6/6 PASS no ambiente isolado (porta 8002, banco adega_test): defaults públicos, salvar+recarregar owner, staff bloqueado (403), links inválidos rejeitados (400), campo desconhecido (400), e-mail inválido (400). Suíte completa NÃO executada (instrução do usuário).
- Verificado: GET /api/settings (16 home + 4 footer keys); desktop e mobile 390px sem overflow; rodapé visível nos dois.
- Lição: backend isolado de testes na porta 8002 pode ficar rodando com código velho — matar processo uvicorn:8002 antes de rodar pytest após mudanças no backend.
- Escopo respeitado: sem IA nas edições, sem deploy, sem logo/imagens, sem frete/pagamento. Pagamentos reais DESATIVADOS.

## 2026-06 — Homologação MP: preparação (compra de teste única autorizada)
- Produto homologação criado: wine_a076c0a779 "[HOMOLOGACAO] Vinho Teste MP - NAO COMPRAR", variante var_6c451170, SKU-AF76A1, R$ 5,00, estoque 1, fora dos destaques.
- Cliente homologação: comprador.homologacao@example.com (customer).
- Webhook público verificado: 401 sem assinatura (alcançável + protegido).
- PENDENTE: conta compradora de teste oficial do MP (painel do desenvolvedor) — sem ela o Checkout Pro não conclui. payment_mode segue "demo" até a compra; mudar para mercadopago_test só no momento do teste. Pagamentos reais DESATIVADOS.

## 2026-06 — Homologação MP: tentativa 1 BLOQUEADA por bug (aguardando autorização de fix)
- Fluxo UI OK até o carrinho (login cliente homologação, frete SP R$ 29,90, total R$ 34,90). Checkout retornou 502.
- CAUSA RAIZ: payments_mp.py linha ~46 usa it["sku"], mas linhas de checkout_quote não têm chave sku -> KeyError engolido -> 502. Sem chamada HTTP ao MP (nada no log). test_connection passa sku, por isso funcionava.
- Reproduzido isoladamente (sem sku=KeyError; com sku=montagem OK). Credenciais/cofre íntegros (mp_configured=true).
- Resíduo: ord_66e3216898c8 pendente, reserva expira 02:57:37 UTC, cron libera.
- Estado restaurado: payment_mode=demo, sales_status=suspended. Pagamentos reais DESATIVADOS.
- FIX PROPOSTO (aguardando autorização): it.get("sku") em payments_mp.py; opcional logar exceção real no 502.

## 2026-06 — Homologação MP: reserva anterior liberada; limitação de vendas suspensas
- ord_66e3216898c8 liberado pelo mecanismo do cron (expired/cancelado); estoque teste: stock=1 reserved=0; movimento liberacao registrado.
- Fix autorizado e aplicado: payments_mp.py it["sku"] -> it.get("sku") (fallback wine_id preservado). Verificação pontual: montagem dos itens OK com e sem sku. Sem logs extras, sem outras alterações.
- LIMITAÇÃO: checkout bloqueado globalmente com sales_status=suspended (server.py ~992). Não existe fluxo restrito de homologação. Usuário NÃO autorizou abrir vendas. Aguardando decisão: (a) abrir vendas brevemente, (b) autorizar bypass de homologação no código, (c) adiar.
- Estado: payment_mode=demo, sales_status=suspended, pagamentos reais DESATIVADOS.

## 2026-06 — Exceção restrita de homologação (autorizada pelo owner)
- server.py: helper _is_homologation_checkout — checkout com vendas suspensas SÓ se: payment_mode==mercadopago_test (nunca demo/live) + conta com flag homologation_buyer re-lida do banco a cada requisição + TODOS os itens com homologation:true. Nada do navegador autoriza.
- /auth/me e login expõem homologation_buyer (UX apenas); CartPage libera o botão só para essa conta (suspendedForMe).
- Flags no banco: comprador.homologacao@example.com homologation_buyer=true; wine_a076c0a779 homologation=true.
- Sondas: D (modo demo + conta/produto homologação) = 403 ✅; A (cliente comum + produto teste) = 403 ✅; B (conta homologação + produto comercial) = 403 ✅. Zero pedidos criados nas sondas; estoques intactos.
- Estado durante a tentativa: payment_mode=mercadopago_test, sales_status=suspended (inalterado).

## 2026-06 — Homologação MP: compra criada, aguardando pagamento do usuário
- ord_6783460b5cb7 | total R$ 34,90 (vinho teste R$ 5,00 + frete R$ 29,90) | mp_preference_id 3691494566-2e96e8dd-f2e2-4c6d-8819-f4f03e3267ad | pending, reservation_active, expira 03:40:31 UTC.
- Estoque teste: stock=1 reserved=1 (reserva única).
- Link Checkout Pro enviado ao usuário. Verificações PENDENTES pós-pagamento: valor/id vs preferência, confirmação via API do provedor (get_payment), baixa única de estoque, webhook REAL (assinatura HMAC) vs simulado. Não declarar webhook homologado sem prova.
- Ao concluir/interromper: remover flag homologation_buyer, restaurar payment_mode=demo, manter sales_status=suspended.

## 2026-06 — Homologação MP: RESULTADO da compra de teste única
- Pagamento 178102235185 verificado NA API do provedor: approved/accredited, R$ 34,90, external_reference=ord_6783460b5cb7, meio=account_money (SALDO da conta de teste, não cartão), pagador @testuser.com. ✅
- Pedido NÃO atualizado: webhooks AUTÊNTICOS do MP chegaram (formatos id/topic e data.id/type) mas TODOS rejeitados com 401. Webhooks NÃO homologados. ❌
- Diagnóstico: validação HMAC do código está CORRETA (teste sintético: assinatura válida passa, adulterada rejeita); segredo do cofre decifra OK (64 chars). Causa provável: segredo salvo no cofre ≠ segredo configurado no painel MP para a URL de notificação (config, não código).
- Pendente: pedido pago no provedor mas pending na loja; reserva expira 03:40 UTC e cron libera; webhook tardio válido marcaria pago+needs_stock_review (por design).
- Limpeza feita: flag homologation_buyer removida, payment_mode=demo, sales_status=suspended. Pagamentos reais DESATIVADOS.
- Aguardando: owner confirmar/regravar o segredo do webhook (Admin > Config) igual ao do painel MP; depois decidir reconciliação manual do pedido ord_6783460b5cb7 ou novo teste.

## 2026-06 — Homologação MP: verificação pós-regravação do segredo (janela encerrada 03:43 UTC)
- NENHUMA notificação nova chegou após a regravação do segredo (03:34:33). Total histórico: 33x 401, 0x 200. Webhooks NÃO homologados.
- Diferenciação: formato Webhooks (data.id/type) teve 2 tentativas com o segredo ANTIGO (401) e não foi re-testado; formato IPN legado (id/topic) tem 401 esperado — docs oficiais MP: IPN não suporta validação por assinatura.
- Pedido ord_6783460b5cb7: pending, reservation_active=true (cron libera na próxima execução agendada). Pagamento segue approved no provedor (account_money). Fluxo de pagamento tardio + revisão de estoque preservado, sem alteração de código.
- Reconciliação por consulta: NÃO existe caminho no código atual (único get_payment fora do webhook é o fluxo de estorno). Exige código novo — informado, AGUARDANDO autorização.
- Estado preservado: payment_mode=demo, sales_status=suspended, exceção de homologação desativada, pagamentos reais DESATIVADOS.

## 2026-06 — Homologação MP: reserva expirada + instruções de envio manual pelo painel
- Cron liberou a reserva de ord_6783460b5cb7 (expired/cancelado, stock=1 reserved=0, movimento liberacao). Pagamento tardio cairá em needs_stock_review/revisao_estoque por design.
- Instruído o usuário a usar "Enviar teste" no painel MP (Webhooks > URL > Pagamentos > Data ID 178102235185). Deixado claro: não é reenvio do registro histórico, mas o envio sai assinado pelo MP com o segredo da aplicação = notificação autêntica do provedor se a assinatura validar.
- Critérios de aceite pós-envio: 200 com HMAC válido + get_payment confirma + pedido approved/paid_via=mercadopago + needs_stock_review + estoque SEM baixa duplicada (nenhum movimento venda).
- Aguardando envio do usuário. Estado: demo + vendas suspensas.

## 2026-06 — Homologação MP: novas tentativas AUTÊNTICAS após regravação do segredo seguem 401
- Entre 03:43-03:48 UTC chegaram novas notificações (origem nova): data.id=178102235185&type=payment (Webhooks), id=178102235185&topic=payment (IPN), merchant_order — TODAS 401 com o segredo NOVO.
- Construção da assinatura confere com a doc oficial (manifest id/data.id minúsculo + request-id + ts, HMAC-SHA256 hex vs v1). IPN legado não suporta assinatura (401 esperado).
- CONCLUSÃO: segredo regravado ainda não corresponde ao que o MP usa para assinar (hipóteses: valor divergente/espaço em branco, app diferente — a de TESTE vs produção, ou envio de teste do painel sem assinatura). Código não alterado; validação não flexibilizada.
- Webhooks permanecem NÃO homologados. Pedido ord_6783460b5cb7: expired/cancelado, estoque liberado (1/0). Pagamento 178102235185 approved no provedor (account_money).
- Estado: demo + vendas suspensas. Aguardando decisão do usuário.

## 2026-06 — Análise dirigida dos 401 de webhook (pós-regravação do segredo)
- err.log termina na linha 2054 (criação da preferência 201 às 03:10). ZERO erros após linha 2053: o único log do validador ("segredo não configurado") NÃO disparou nas tentativas 03:43-03:48 -> rejeição NÃO foi por segredo ausente.
- env MP_WEBHOOK_SECRET vazio (len 0) -> validador usou _RT_SECRET do cofre. Processo 57679 (último reload ~03:05, nenhum após) teve o runtime atualizado in-place pelo save às 03:34:33 -> validou com o segredo NOVO. Sem cenário de memória velha.
- data.id presente na query (log uvicorn). Presença de x-signature/ts/v1/x-request-id: NÃO REGISTRADA nos logs existentes -> impossível dizer a etapa exata (restam: sem ts/v1, ts inválido, ts fora da janela, HMAC divergente). Não especulado.
- Origem simulador vs automática: burst misto (Webhooks+IPN+merchant_order) indica retentativa AUTOMÁTICA do MP (simulador do painel envia só o formato Webhooks de 1 evento) — indicativo, não conclusivo.
- PROPOSTA (aguardando aprovação): log temporário mínimo no validador — só presença booleana dos campos + código da etapa de rejeição (+skew em segundos). Sem segredos/assinaturas/dados pessoais.
- Estado: demo + vendas suspensas. Nada alterado.

## 2026-06 — Instrumentação temporária de diagnóstico do webhook (APROVADA pelo owner)
- valid_webhook_signature (payments_mp.py): log WARNING "WEBHOOK_DIAG" por tentativa com APENAS: formato (webhooks/ipn), presença booleana de x-signature/ts/v1/x-request-id/data.id, etapa (sem_segredo|sem_ts_ou_v1|ts_invalido|ts_fora_janela+skew_s|hmac_divergente|valida) e rótulo da origem do segredo (ambiente|cofre). SEM valores/segredos/assinaturas/dados pessoais. Validação HMAC integralmente preservada.
- REMOVER o diagnóstico após a análise da notificação de teste do usuário.
- Estado: demo + vendas suspensas.

## 2026-06 — Homologação MP: notificação do SIMULADOR validada e processada (04:12 UTC)
- Envio MANUAL do usuário pelo simulador do painel MP (data.id=178102235185). DIAG: valida | origem_segredo=cofre | formato=webhooks | x-signature/ts/v1/x-request-id/data.id todos presentes. HMAC VALIDADO com o segredo do cofre -> segredo atual corresponde ao da aplicação e o validador funciona com o formato real do MP.
- Processamento: get_payment confirmou approved/accredited no provedor; pedido ord_6783460b5cb7 -> payment_status=approved, paid_via=mercadopago, mp_payment_id=178102235185, needs_stock_review=true, fulfillment_status=revisao_estoque (pagamento tardio, reserva já expirada -> envio bloqueado p/ revisão manual). Estoque SEM baixa: stock=1 reserved=0; só movimento revisao_estoque; nenhuma baixa duplicada.
- RESSALVA registrada: foi envio manual do simulador, NÃO entrega automática comprovada. Entregas automáticas anteriores (03:43-03:48) falharam 401 antes da instrumentação — etapa exata desconhecida, não especulada.
- Diagnóstico temporário DESATIVADO (função restaurada ao original). Estado: demo + vendas suspensas.
- PENDENTE p/ homologação completa: uma entrega AUTOMÁTICA do MP validada; resolução da revisão de estoque do pedido (endpoint existente /admin/orders/{id}/resolve-stock, ação do owner).

## 2026-06 — Homologação MP: compra 2 criada, aguardando pagamento do usuário
- ord_a2ccc9837334 | total R$ 34,90 (vinho teste 2 R$ 5,00 + frete R$ 29,90) | pref 3691494566-66d2d5ca-2882-483d-a44a-8b49631ef755 | pending, reserva até 04:54:19 UTC.
- Produto exclusivo: wine_2c478125c8 "[HOMOLOGACAO] Vinho Teste MP 2" (homologation=true, stock=1 reserved=1). Vinho 1 intocado p/ revisão do pedido anterior.
- Exceção reativada: flag homologation_buyer recolocada; payment_mode=mercadopago_test; sales_status=suspended mantido.
- OBJETIVO: comprovar ENTREGA AUTOMÁTICA do webhook (assinatura válida + 200 + processamento) — simulador NÃO vale como prova. Verificar pós-pagamento: webhook automático 200, get_payment approved, baixa única (venda) do estoque 2.
- Ao concluir/interromper: remover flag, restaurar demo, manter suspended.

## 2026-06 — Homologação MP compra 2: ACHADO CRÍTICO — credencial no cofre é de PRODUÇÃO
- Pagamento 178106554507 no provedor: approved/accredited, BRL 34,90, external_reference=ord_a2ccc9837334, Mastercard crédito 1x (****3311), pagador @testuser.com, live_mode=TRUE.
- CREDENCIAL DO COFRE TEM PREFIXO APP_USR (produção), não TEST-. O usuário acreditava ser credencial de teste. Pagador era conta de teste + cartão de teste oficial -> sem movimentação real de dinheiro (contas de teste não liquidam), mas live_mode=true indica ambiente produtivo da aplicação.
- Webhooks AUTOMÁTICOS da compra 2 chegaram (data.id/type + id/topic + merchant_order) e TODOS 401. O simulador (04:12) validou com o segredo do cofre. HIPÓTESE principal (evidências, não prova absoluta): o segredo do webhook no cofre pertence a aplicação MP DIFERENTE da dona da credencial APP_USR — o MP assina a notificação automática com o segredo da aplicação DONA do pagamento; o simulador assina com o segredo da aplicação aberta no painel.
- Pedido ord_a2ccc9837334: pending, reserva ativa até 04:54:19 UTC (cron libera; webhook tardio válido -> pago + revisao_estoque, fluxo preservado). Estoque vinho 2: 1/1, sem baixa (correto — webhook rejeitado).
- Limpeza feita: flag homologation_buyer removida, payment_mode=demo, sales_status=suspended.
- PENDÊNCIAS: (1) usuário verificar no painel MP se credencial e segredo de webhook são da MESMA aplicação e se a credencial deveria ser TEST-; (2) reconciliação dos 2 pedidos pagos no provedor (exige código novo — não autorizado); (3) entrega automática de webhook segue NÃO homologada.

## 2026-06 — Correção de conclusões (rodada de evidências, sem mudança de código/credenciais)
- CORRIGIDO: prefixo APP_USR NÃO discrimina ambiente (doc oficial Checkout Pro: tokens de teste também começam com APP_USR). Classificação anterior ("produção") estava errada.
- PROVADO: GET /users/me com a credencial do cofre -> 200, id=3691494566, @testuser.com, tag "test_user" => credencial pertence a CONTA VENDEDORA DE TESTE. Pagamentos 1 e 2 têm collector_id=3691494566 == collector da preferência criada pela loja => conta recebedora É a vendedora de teste da aplicação. Pagador: 3691494568 (outra conta teste).
- live_mode=true esclarecido com evidência: ambos os pagamentos (test buyer -> test seller) reportam live_mode=true; contas de teste não liquidam dinheiro real => neste fluxo live_mode=true NÃO significa cobrança real.
- Webhook automático 401: registros existentes NÃO identificam a etapa exata. Diagnóstico estava ativo só no envio do simulador (04:12); automáticas da compra 1 chegaram antes da instrumentação e as da compra 2 após a remoção. Única etapa descartável: "segredo ausente" (nenhum log MP_WEBHOOK_SECRET pós-regravação). Não especulado; hipótese de apps diferentes NÃO concluída. Código não descartado nem confirmado como causa.
- Estado: demo + vendas suspensas; exceção desativada; ord_a2ccc9837334 pending (cron libera reserva); estoque vinho 2 sem baixa (1/1).

## 2026-06 — Diagnóstico reativado (filtrado + auto-off 30min) + CORREÇÃO DE REGISTRO
- CORREÇÃO: ord_6783460b5cb7 (compra 1) JÁ foi processado como PAGO via simulador (04:12) e está em revisao_estoque — NÃO reconciliar novamente como pendente. Único pendente de reconciliação: ord_a2ccc9837334 (compra 2).
- Diagnóstico reativado em valid_webhook_signature: filtra APENAS data.id/id ∈ {178102235185, 178106554507} e auto-desliga em 30 min (deadline no carregamento do módulo). Loga só presença de campos, etapa e rótulo da origem do segredo. Validação HMAC inalterada. IPN merchant_order (ids diferentes) não gera log.
- Sem espera ativa: se retentativa automática chegar na janela, a evidência fica no log para análise posterior; se não chegar, causa permanece indeterminada.
- Estado: demo + vendas suspensas.

## 2026-06 — CAUSA RAIZ DO WEBHOOK COMPROVADA (diagnóstico capturou retentativa automática)
- 05:00 UTC: WEBHOOK_DIAG das retentativas AUTOMÁTICAS do pagamento 178106554507: etapa=hmac_divergente, TODOS os campos presentes (x-signature/ts/v1/x-request-id/data.id=sim), origem_segredo=cofre. Formatos webhooks E ipn.
- Cadeia de prova: mesmo validador + mesmo segredo do cofre VALIDOU o simulador (04:12) e REJEITA as automáticas por HMAC divergente => o MP assina notificações automáticas com chave DIFERENTE da do simulador. Como os pagamentos são live_mode=true, as automáticas são assinadas com o segredo de webhook da config de PRODUÇÃO da aplicação; o cofre tem o segredo da config de TESTES (usado pelo simulador). Código do validador correto — não é bug.
- IPN: também hmac_divergente; irrelevante — o formato Webhooks é o que processa.
- Pedido 2 (ord_a2ccc9837334): reserva expirou 04:54:19, cron liberou (expired/cancelado, estoque 1/0). Webhook tardio válido -> pago + revisao_estoque (fluxo preservado).
- BLOQUEIO EXTERNO COMPROVADO: o segredo correto só existe no painel MP. AÇÃO ÚNICA DO USUÁRIO: copiar a assinatura secreta da seção Webhooks > Configurações de PRODUÇÃO da MESMA aplicação e salvar no Admin > Config da loja. Próxima retentativa automática do MP deve validar e fechar o pedido 2 sozinha.
- Diagnóstico ativo até ~05:22 UTC (auto-off). Estado: demo + vendas suspensas.

## 2026-06 — Correção IPN no validador + contexto da config de produção
- Usuário confirmou: URL do webhook estava SÓ na aba de testes do painel MP; cadastrou na aba de produção (evento Pagamentos legacy). Mesmo segredo nos dois ambientes.
- Diagnóstico provou às 05:00: retentativa automática chega COMPLETA (todos os campos) e falha em hmac_divergente — nem ausência, nem formato, nem timestamp.
- BUG DE CÓDIGO ENCONTRADO E CORRIGIDO: para notificações IPN legadas (param "id" na URL), o validador montava o manifest SEM o id; a doc oficial manda incluir id:[id_da_url]. payments_mp.py: manifest_id = data.id OU id (fallback). Formato Webhooks byte a byte inalterado. Antes desta correção, IPN falharia SEMPRE, mesmo com o segredo certo — e o usuário ativou justamente o evento "Pagamentos (legacy)" na produção.
- Janela do diagnóstico estendida para 3h (auto-off preservado) para capturar a retentativa automática, que pode demorar (intervalos crescentes do MP).
- Pedido 2 (ord_a2ccc9837334): expired/cancelado, estoque liberado (1/0) — webhook tardio válido cai em revisao_estoque.
- Estado: demo + vendas suspensas. Aguardando retentativa automática para prova final.

## 2026-06 — Webhook MP: bloqueio EXTERNO comprovado (relato para suporte MP preparado)
- Captura real das automáticas (05:28-05:30 UTC): manifest id:178106554507;request-id:85ef49e7-...;ts:1789450235; v1=de87736d58d7... vs calculado ca13ade856f8... | IPN: manifest id:178106554507;request-id:11eebd27-...;ts:1789450248; v1=6104c541b6c6... vs 3cf6cecb7ace...
- Manifest = template oficial byte a byte. ts dentro da janela. Todos os campos presentes.
- Força bruta local: 8 variantes de manifest x 2 chaves (segredo webhook, access token) = ZERO match. O MP assina as automáticas com chave que NÃO é a exibida no painel (a mesma que valida o simulador).
- Fix IPN no manifest (fallback id) MANTIDO: correto conforme doc; não enfraquece validação.
- Diagnóstico segue ativo (auto-off ~08:30 UTC) para capturar retentativa pós-correção do MP.
- Pedidos: ord_6783460b5cb7 approved+revisao_estoque (simulador); ord_a2ccc9837334 expired, virará paid+revisao_estoque se webhook válido chegar (handler processa).
- Estado: demo + vendas suspensas. Sem nova compra. Próximo passo: usuário abre chamado no suporte MP com o relato.

## 2026-06 — Encerramento do ciclo de diagnóstico do webhook MP
- Diagnóstico temporário REMOVIDO por completo (zero resíduos WEBHOOK_DIAG/_DIAG) e fallback de IPN (manifest_id) REVERTIDO — doc citada informa que IPN não permite validação pela chave secreta. valid_webhook_signature restaurada à versão oficial (Webhooks, manifest id+request-id+ts, janela 5 min).
- Fix autorizado anterior MANTIDO: create_preference usa it.get("sku") com fallback wine_id.
- CAUSA REGISTRADA COMO INDETERMINADA, aguardando suporte do Mercado Pago (relato objetivo entregue ao usuário). Fatos provados: simulador valida com o segredo do cofre; automáticas chegam completas e falham só em hmac_divergente; manifest byte a byte conforme doc; nenhuma combinação chave local x variante de manifest reproduz a assinatura automática.
- Pedidos: ord_6783460b5cb7 approved+revisao_estoque (via simulador); ord_a2ccc9837334 expired/cancelado (pagamento approved no provedor; se um dia chegar webhook válido, handler processa como pagamento tardio + revisão de estoque).
- Estado final: payment_mode=demo, sales_status=suspended, exceção de homologação desativada, credenciais intactas, sem novas compras.

## 2026-06 — Chamado ao suporte MP (redigido pelo usuário, reforçado com evidências)
- Aplicação MP: 1559066132100293. Mesma assinatura exibida nas abas Teste e Produção (confirmado pelo usuário 2x). Credencial de "Credenciais de teste" (conta test_user 3691494566).
- Versão reforçada do relato inclui: x-request-id das entregas automáticas (85ef49e7-0e4d-4704-ab23-99320d379bc3, ts=1789450235=2026-09-15 05:30:35 UTC; 11eebd27-f031-483b-9e89-41414bfd389b, ts=1789450248), manifests capturados, prefixos v1 recebido vs calculado, e o ponto IPN (presença de x-signature não distingue formato nem implica validação pela chave secreta).
- Perguntas centrais ao MP: (1) qual chave assina as notificações automáticas desta aplicação e onde obtê-la, já que o painel exibe um único valor; (2) esclarecimento formal sobre IPN/x-signature.
- Estado inalterado: demo + vendas suspensas, validação oficial intacta, sem diagnóstico ativo.

## 2026-06 — Resposta à pergunta do suporte MP (data.id na HMAC)
- Verificado no código: valid_webhook_signature usa data_id = query_params.get("data.id") — valor EXATO da query string, nunca do corpo nem do id do IPN (fallback IPN foi revertido). Chamador: mp_webhook passa request.query_params diretamente.
- Campos ausentes: id: e request-id: são omitidos do manifest quando ausentes; ts/v1 ausentes -> rejeição. Conforme doc.
- Evidência da query recebida: log de acesso "POST /api/webhooks/mercadopago?data.id=178106554507&type=payment" e manifest capturado "id:178106554507;request-id:85ef49e7-...;ts:1789450235;".
- Resposta curta entregue ao usuário para encaminhar ao atendimento. Estado: demo + vendas suspensas, nada alterado.

## 2026-06 — WCS-50562: eliminação completa das causas locais (prova de proxy + implementação independente)
- Implementação independente (do zero, pela referência oficial) == validador em produção: mesmo manifest e mesmo digest (ca13ade856f8) sobre os materiais da entrega automática real. Cálculo local PROVADO correto.
- Força bruta ampliada: 15 variantes de manifest x 3 chaves (segredo str, segredo hex-decodificado, access token) = ZERO match com o v1 das automáticas.
- Sonda controlada pela URL pública (11:19 UTC): aplicação recebeu byte a byte a query crua, o x-request-id e o x-signature enviados — ingress/proxy NÃO transforma nada. Único middleware: CORS.
- Captura temporária (webhook_diag, só os 2 pagamentos, auto-expira ~11:35 UTC): nenhuma retentativa automática do MP chegou entre ~05:35 e 11:19 UTC.
- CONCLUSÃO: bloqueio externo comprovado e caracterizado. Resposta técnica específica para WCS-50562 preparada (rastrear x-request-id 85ef49e7-... e informar chave/materiais exatos da assinatura automática).
- Estado: demo + vendas suspensas; validação intacta; captura expira sozinha.

## 2026-06 — Config: botões do hero com campos separados + bloco do cartão editável
- AdminPage: cada botão (hero principal/secundário, CTA sommelier) agora tem grupo identificado com campos "Texto do botão" (livre) e "Destino do botão" (select de rotas válidas). Novo editor "Cartão sobre a imagem do hero" com 5 campos: selo, texto, título, descrição, texto do botão.
- Backend: DEFAULT_HOME_CONTENT ganhou card_badge/card_text/card_title/card_description/card_button_label (valores atuais como padrão; merge automático — sem migração).
- HomePage: cartão do hero renderiza do conteúdo editável, inclui botão/link para /harmonizar (destino fixo, conforme pedido). Cartão segue oculto no mobile (hidden md:block) — design preservado.
- Verificado: PUT/GET persiste e recarrega (valores de teste), merge preserva campos não enviados, screenshots desktop (botão + cartão com valores editados) e mobile (botões). Valores restaurados aos padrões após a verificação. Sem suíte completa. Pagamentos/frete/design geral intocados.

## 2026-06 — Logo Casa da Barrica Wines aplicado
- Asset anexado processado: 1983x793 -> fundo branco removido com desmultiplicação (bordas limpas), margens vazias cortadas, redimensionado proporcional p/ 800x320 (65 KB), salvo em /frontend/public/brand/logo.png. Caminho centralizado na constante LOGO_SRC em Layout.jsx (futura troca por SVG = 1 linha).
- Aplicado: header desktop (chip creme #F7F2EB, h-9, tagline ao lado), header mobile (chip creme, h-7, max-w-46vw p/ não sobrepor Entrar/Sair), rodapé (chip creme h-10, tagline abaixo). Todos com alt="Casa da Barrica Wines"; headers levam à Home (Link to="/"). object-contain + w-auto = proporção preservada. Favicon NÃO alterado (versão própria virá separada).
- Identidade bordô/creme/madeira/cobre preservada; nenhuma funcionalidade alterada.

## 2026-06 — Logo: verificação visual concluída (tamanho/contraste/alinhamento)
- Desktop: logo 110x44 (ratio 2.50 exato), chip creme shrink-0, header 93px de altura — legível sem exagerar a altura.
- Mobile (390px): logo 80x32 (ratio 2.50), sem sobreposição com botão Entrar/Sair, sem overflow horizontal.
- Rodapé: logo 100x40 (ratio 2.50) sobre chip creme, tagline abaixo.
- Ajuste aplicado pós-1a verificação: chips com shrink-0 (flex não comprime mais o logo) e tamanhos h-9->h-11 (desktop) / h-7->h-8 (mobile).
- Prévia apresentada ao usuário para validação. Favicon intacto aguardando versão própria.

## 2026-06 — Logo: tamanhos finais aprovados por medição
- Desktop header: logo 180x72 (w-[180px] h-auto, ratio 2.50 exato), chip creme shrink-0, tagline oculta abaixo de lg (hidden lg:block) p/ garantir espaço; header 109px (era 93px — aumento contido).
- Mobile header (390px): logo 130x52 (w-[130px] h-auto, max-w-52vw), chip com padding reduzido, header py-2.5; sem sobreposição com Entrar/Sair; sem overflow.
- Rodapé: 100x40, ratio 2.50.
- PNG atual mantido até o SVG definitivo (LOGO_SRC centralizado). Prévias finais apresentadas ao usuário.

## 2026-06 — Logo: variante integrada ao fundo escuro (correção do retângulo preto)
- CAUSA do retângulo preto: meu processamento anterior (branco→alpha) tornou opacos os pixels transparentes do arquivo ORIGINAL, que já tinha transparência real (90,6% do canvas, 81% dentro do bbox do desenho). CSS nunca foi o problema.
- Novo processamento a partir do alpha original: corte rente ao desenho (ratio real 4.0, não 2.5), recolor programático sem redesenho — letras+símbolo em creme suave (#F4ECE2), apenas a faixa da barrica em cobre (#C28D58), detectada por segmentos horizontais longos (30 linhas, 8252 px). Salvo 800x200 em /brand/logo.png.
- Chips creme/moldura/sombra/padding REMOVIDOS do header desktop, mobile e rodapé — logo direto sobre o fundo escuro.
- Medido no site real: desktop 160x40 (ratio 4.00, header 81px, mais compacto que os 109px anteriores); mobile 390px: 128x32, sem sobreposição, sem overflow; rodapé 160x40 integrado. Screenshots confirmam: sem retângulos/molduras, contraste bom, faixa cobre visível.


---

## Status 2026-09-16 — Reconciliação MP por consulta ENTREGUE (detalhes em CHANGELOG.md)
- Rotina única webhook/reconciliação/consulta manual com verificação de recebedor, referência, valor e moeda; divergências → revisão manual sem confirmar.
- Cron `reconcile-payments` a cada 15 min (mínimo da plataforma em produção) com backoff progressivo por pedido; botão owner-only "Consultar pagamento no Mercado Pago" com auditoria e rate-limit.
- Pedidos de homologação reconciliados: ord_a2ccc9837334 confirmado via consulta (revisão de estoque pendente — reserva expirada), ord_6783460b5cb7 preservado. Ambos aguardam decisão de estoque do proprietário (baixar e enviar OU reembolsar).
- CPF duplicado resolvido conforme autorizado; índice parcial ativo no banco de preview.
- Testes: 12/12 novos + 13/13 regressão no isolado. Webhook NÃO homologado (ticket WCS-50562 aberto); a consulta é o mecanismo de segurança até lá.
- PENDÊNCIAS PARA VENDAS REAIS: resolver revisão de estoque dos 2 pedidos de homologação, definir valor do ponto, ativar IA (opcional), cadastrar Instagram (opcional), credenciais MP de produção, sales_status=open, retorno do ticket MP.

---

## Status 2026-09-15 (3) — Duplicados + checkout com login ENTREGUES (detalhes em CHANGELOG.md)
- Unicidade CPF (parcial, opcional) e e-mail no app E no banco; mensagens anti-enumeração exatas; corrida simultânea bloqueada pelo índice único; login legítimo preservado; Google intacto.
- Checkout exige conta ativa no servidor (401/403); visitante é guiado ao login e retorna com carrinho preservado; carrinho da conta com merge sem duplicar variantes.
- ⚠️ CONFLITO PENDENTE NO BANCO DE PREVIEW: 1 CPF (`***47`) em 2 contas (`user_c09…`, `user_6f0…`). Índice único de CPF NÃO criado lá; app funcionando; aguardando decisão do proprietário (não excluí/mescléi nada).
- Testes: 15/15 novos + 37/37 combinados no isolado; E2E UI desktop+mobile aprovado.

---

## Status 2026-09-15 (2) — Atendimento Sommelier Virtual ENTREGUE (detalhes em CHANGELOG.md)
- Chat Sommelier Virtual (Claude Sonnet 5, Universal Key) + botão flutuante + "Atendimento" no rodapé; humano via WhatsApp wa.me/5524981293634 sempre disponível; pedidos só do cliente logado; **IA DESATIVADA por padrão** — ativar em Admin > Config > "Atendimento (Sommelier Virtual)" após revisar limite (padrão 20 msgs/conversa) e custos.
- Instagram no rodapé: configurar URL HTTPS do perfil em Admin > Config (vazio = oculto). WhatsApp de encaminhamento editável no mesmo local.
- Linguagem pública "IA" → "Sommelier (Virtual)" aplicada (migração idempotente, personalizações preservadas).
- Testes: 10/10 pytest isolado + screenshots desktop/mobile verificados. Vendas/pagamento/resgate inalterados.
- AGUARDANDO USUÁRIO: ativar IA de atendimento (ou não), cadastrar Instagram, valor do ponto + ativação do resgate, ticket MP WCS-50562, abertura de vendas reais.

---

## Status 2026-09-15 — Clube + Pontos + Analytics ENTREGUES (detalhes em CHANGELOG.md)
- Clube integrado ao Auth (campos opcionais, opt-ins separados, termos v1.0 versionados) — backend 22/22 pytest isolado + frontend 6/6 (iteration_8).
- Pontos: crédito só em compras reais aprovadas (floor, sem frete), ledger auditável, estorno total/parcial; RESGATE implementado mas DESATIVADO até o proprietário definir `point_value_brl` e ativar em Admin > Config.
- Analytics: aba "Acessos" no Admin, privacy-first, retenção 180 dias, eventos de compra/cadastro server-side.
- PENDENTE (pré-existente, adiado pelo usuário): 2 testes legados test_admin_secrets.py + 1 test_iteration3.py falham na suíte completa — não introduzidos nesta rodada.
- AGUARDANDO USUÁRIO: valor do ponto (R$) + ativação do resgate; abertura de vendas reais; retorno do ticket MP WCS-50562.
- Backlog de polish (sugestões do iteration_8, não bloqueantes): toast PT-BR no lugar da validação nativa `required` dos aceites do Clube; hint de estimativa de pontos antes do CEP; agrupar /vinho/* e paginação em top-pages; caption "resgate desativado" na seção points-config.