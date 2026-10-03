# Casa da Barrica Wines

Plataforma web para uma adega de vinhos, com catálogo, carrinho, gestão de pedidos, clube de assinaturas e recursos de inteligência artificial para harmonização e atendimento.

Projeto desenvolvido por **Maxwell Gallucci Rodrigues**

> **Status:** em desenvolvimento e validação. O funcionamento das integrações depende da configuração dos serviços externos. Este repositório não representa uma certificação de prontidão para produção.

## Objetivo

Reunir a experiência de compra de vinhos e a administração da adega em uma aplicação, explorando IA aplicada à escolha de produtos e ao atendimento ao cliente.

## Funcionalidades presentes no código

### Experiência do cliente

- Catálogo e páginas de detalhes dos vinhos.
- Carrinho e criação de pedidos.
- Cadastro, login e recuperação de senha.
- Área da conta e acompanhamento de pedidos.
- Lista de favoritos.
- Sugestões de harmonização entre pratos e vinhos.
- Chat de atendimento.
- Consulta de planos e adesão a assinaturas.
- Acompanhamento e cancelamento de assinatura.

### Administração

- Gestão de produtos, variações e estoque.
- Acompanhamento de pedidos e pagamentos.
- Gestão de planos, ciclos e kits de assinatura.
- Consulta de assinantes.
- Configuração de conteúdo e atendimento.
- Indicadores de operação e navegação.
- Gestão de pontos do clube.
- Registros de auditoria.
- Rotinas de backup e verificação de restauração.

## Inteligência artificial aplicada

O projeto utiliza IA em dois contextos:

**Desenvolvimento assistido:** uso do Emergent para apoiar a construção e evolução da aplicação.

**Funcionalidades do produto:** integração com modelos de linguagem para atendimento e sugestões de harmonização. O código também inclui uma alternativa baseada em regras para harmonização.

Esses recursos dependem da configuração das integrações e da avaliação da qualidade das respostas.

## Pagamentos e assinaturas

O código inclui integração com APIs do **Mercado Pago** para:

- Criação de preferências de pagamento com Checkout Pro.
- Consulta e reconciliação do status de pagamentos.
- Tratamento de notificações por webhook.
- Solicitação de estornos.
- Criação e gestão de assinaturas recorrentes via PreApproval.

Há fluxos demonstrativos e configurações de ambiente. A presença da integração no código não comprova homologação de pagamentos reais.

## Tecnologias

| Camada | Tecnologias |
|---|---|
| Frontend | React 19, JavaScript e React Router |
| Interface | Tailwind CSS, Radix UI e Lucide |
| Comunicação com a API | Axios |
| Backend | Python, FastAPI e Pydantic |
| Banco de dados | MongoDB e Motor |
| Autenticação | JWT, Argon2, bcrypt e TOTP |
| Pagamentos | Mercado Pago |
| IA e serviços externos | Integrações do Emergent |
| Testes | pytest e pytest-xdist |
| Ferramentas do frontend | Yarn Classic e CRACO |

## Estrutura do projeto

```text
backend/
  server.py                 # API principal
  payments_mp.py            # Integração de pagamentos
  subscriptions.py          # Regras e rotas de assinaturas
  subscriptions_mp.py       # Integração de recorrência
  email_service.py          # E-mails transacionais
  storage_service.py        # Armazenamento externo de backups
  requirements.txt
  tests/

frontend/
  public/
  src/
    components/
      account/
      admin/
      ui/
    context/
    hooks/
    lib/
    pages/
    App.js
  package.json
  yarn.lock

scripts/                    # Scripts de backup e restauração
memory/                     # Documentação de evolução e requisitos
test_reports/               # Relatórios de testes registrados
```

## Configuração de desenvolvimento

A instalação fora do ambiente original do Emergent ainda precisa de validação. Alguns módulos, testes e scripts dependem de serviços externos ou de caminhos específicos desse ambiente.

### Requisitos

- Python 3.10 ou superior, conforme a sintaxe utilizada.
- Node.js compatível com as dependências do frontend.
- Yarn Classic 1.22.22.
- MongoDB.
- Credenciais dos serviços que serão utilizados.

As rotinas de testes e backup também utilizam ferramentas como `mongosh`, `mongodump` e `mongorestore`.

### Variáveis do backend

Crie um arquivo `backend/.env` local. Não publique valores de credenciais.

| Variável | Finalidade |
|---|---|
| `MONGO_URL` | Conexão com o MongoDB |
| `DB_NAME` | Nome do banco da aplicação |
| `JWT_SECRET` | Segredo de assinatura dos tokens |
| `OWNER_EMAIL` | E-mail utilizado para identificar o proprietário |
| `EMERGENT_LLM_KEY` | Credencial das integrações do Emergent |
| `WEBHOOK_CRON_SECRET` | Segredo das rotas de tarefas programadas |
| `PUBLIC_APP_URL` | Endereço público da aplicação |
| `CORS_ORIGINS` | Origens configuradas para acesso à API |
| `SECRETS_ENC_KEY` | Material utilizado na criptografia dos segredos |
| `MP_ACCESS_TOKEN` | Credencial da integração de pagamentos |
| `MP_WEBHOOK_SECRET` | Segredo para validação de webhooks |
| `MP_ACCESS_TOKEN_LIVE` | Credencial específica para pedidos em modo real |
| `EMERGENT_EMAIL_KEY` | Credencial de envio de e-mails |
| `EMAIL_FROM_NAME` | Nome do remetente |
| `EMAIL_REPLY_TO` | Endereço de resposta dos e-mails |

Algumas variáveis são exigidas já na importação da aplicação; outras são utilizadas conforme a integração habilitada.

### Backend

Com um ambiente virtual Python ativado:

```bash
cd backend
python -m pip install -r requirements.txt
python -m uvicorn server:app --reload --port 8000
```

**Pendência de dependências:** o código importa `httpx`, `argon2` e `pyotp`, mas os pacotes correspondentes não estão declarados diretamente no `requirements.txt` atual. Revise e registre essas dependências antes de considerar a instalação reproduzível.

Com a API em execução, a documentação interativa fica disponível em:

```text
http://localhost:8000/docs
```

### Frontend

Crie `frontend/.env`:

```dotenv
REACT_APP_BACKEND_URL=http://localhost:8000
```

Em outro terminal:

```bash
cd frontend
yarn install --frozen-lockfile
yarn start
```

Abra o endereço informado no terminal. Reinicie o frontend após alterar variáveis de ambiente.

## Testes

O repositório contém testes relacionados a:

- Autenticação, MFA e recuperação de acesso.
- Configuração de segredos administrativos.
- Cadastro e checkout.
- Reconciliação de pagamentos.
- Atendimento e configurações de conteúdo.
- Pontos do clube e indicadores.
- Assinaturas.

A configuração atual utiliza `pytest-xdist`. Parte da infraestrutura de testes depende do caminho `/app/backend`, de MongoDB e de um backend de testes separado.

Antes de executar a suíte em outra máquina, ajuste esses caminhos e confirme o isolamento do banco de testes. A existência dos arquivos e relatórios não garante que todos os testes passem na versão atual.

## Pontos de atenção

- A configuração de CORS inclui uma expressão permissiva e deve ser revisada antes de exposição pública.
- Integrações de pagamento precisam de validação completa em ambiente de testes.
- Recursos de e-mail, IA e armazenamento dependem de serviços externos.
- Caminhos fixos em scripts e testes precisam ser adaptados para execução fora do Emergent.
- Os agendamentos do ambiente original não acompanham esta exportação e precisam ser configurados separadamente.
- O ZIP deste repositório contém código e documentação; não inclui banco de dados, arquivos `.env` nem backups operacionais.

## Próximas melhorias

- Consolidar as dependências e validar a instalação em ambiente limpo.
- Criar arquivos `.env.example` sem credenciais.
- Tornar caminhos de testes e scripts configuráveis.
- Validar compra, pagamento, webhook, estorno e recorrência de ponta a ponta.
- Automatizar as verificações do projeto.
- Avaliar as respostas de harmonização e atendimento com cenários documentados.
- Publicar capturas de tela e uma demonstração com dados fictícios.

## Autor

**Maxwell Gallucci Rodrigues**

Desenvolvimento web e inteligência artificial aplicada à construção de soluções.

[GitHub](https://github.com/mrgallucci) · [LinkedIn](https://www.linkedin.com/in/mrgallucci/)
