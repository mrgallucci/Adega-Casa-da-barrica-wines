# Auth Testing Playbook — Minha Adega

## Injetar sessão de CLIENTE para testes
```bash
mongosh --quiet test_database --eval "
var userId = 'test-user-' + Date.now();
var sessionToken = 'test_session_' + Date.now();
db.users.insertOne({
  user_id: userId, email: 'teste.cliente.' + Date.now() + '@example.com',
  name: 'Cliente Teste', role: 'customer', password_version: 1,
  mfa_enabled: false, marketing_opt_in: false, created_at: new Date().toISOString()
});
db.user_sessions.insertOne({
  user_id: userId, session_token: sessionToken, pv: 1,
  expires_at: new Date(Date.now() + 7*24*60*60*1000).toISOString(),
  created_at: new Date().toISOString()
});
print('SESSION=' + sessionToken);
"
```
Uso: cookie httpOnly `session_token` ou header `Authorization: Bearer <sessionToken>`.

## Injetar sessão de ADMIN (owner/staff) para testes
ATENÇÃO: `require_admin` exige `role` in (owner,staff) **E** `mfa_enabled: true` no documento do usuário.
```bash
mongosh --quiet test_database --eval "
var userId = 'test-admin-' + Date.now();
var sessionToken = 'test_adm_session_' + Date.now();
db.users.insertOne({
  user_id: userId, email: 'teste.admin.' + Date.now() + '@example.com',
  name: 'Admin Teste', role: 'owner', password_version: 1,
  mfa_enabled: true, marketing_opt_in: false, created_at: new Date().toISOString()
});
db.user_sessions.insertOne({
  user_id: userId, session_token: sessionToken, pv: 1,
  expires_at: new Date(Date.now() + 7*24*60*60*1000).toISOString(),
  created_at: new Date().toISOString()
});
print('SESSION=' + sessionToken);
"
```

## Fluxos de auth reais
- Registro: POST /api/auth/register {email, password (min 8), name, birth_date (18+ obrigatório)} → JWT (role sempre customer).
- Login customer: POST /api/auth/login → {token, user}.
- Login admin: POST /api/auth/login → {mfa_setup_required|mfa_required, mfa_token}; depois POST /api/auth/mfa/setup (só setup) e POST /api/auth/mfa/verify {mfa_token, code TOTP} → JWT.
- Reset: POST /api/auth/forgot-password (sempre 200) → e-mail com link /redefinir-senha?token=... → POST /api/auth/reset-password (uso único, 30 min, revoga sessões/JWTs).
- Google OAuth: botão na /login; retorno em #session_id processado por AuthCallback; admin também exige MFA após OAuth.
- Limpeza: `mongosh --quiet test_database --eval "db.users.deleteMany({email:/teste\./}); db.user_sessions.deleteMany({session_token:/test_session|test_adm_session/});"`
