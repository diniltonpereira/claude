# Implantação do CarbonMG em produção

Roteiro para colocar o piloto no ar com uma prefeitura. Tudo aqui foi testado com a imagem Docker do projeto. A integração real com o gov.br depende de credenciais que só o órgão obtém; o fluxo foi testado com um provedor simulado.

## 1. Arquitetura do piloto

```
Internet ──TLS──► proxy reverso (Caddy/Nginx/balanceador) ──HTTP──► contêiner carbonmg :8000
                                                                     ├─ volume /dados  (carbono.db + anexos/)
                                                                     └─ pasta /backups (cópias diárias)
```

**Banco: SQLite em modo WAL.** Para um órgão no piloto, com dezenas de usuários, é suficiente, simples de operar e tem backup consistente sem parar o sistema.

**Quando migrar para PostgreSQL:**
- mais de um órgão grande na mesma instância;
- escrita concorrente intensa (erros `database is locked` nos logs);
- exigência de alta disponibilidade com réplica.

A migração exige trocar `?` por `%s`, `sqlite3.Row` por um cursor de dicionário, `BEGIN IMMEDIATE` por `SELECT … FOR UPDATE` e os gatilhos para PL/pgSQL. As regras de negócio não mudam.

## 2. Passo a passo

### 2.1 Configuração
```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # cole em CARBONO_SEGREDO
```
Preencha:
- `CARBONO_URL_PUBLICA`: o endereço público, usado nos QR Codes e nos convites;
- `CARBONO_CONTROLADOR` e `CARBONO_ENCARREGADO`: dados exigidos pela LGPD;
- as variáveis `GOVBR_*`, depois da seção 3.

**O `CARBONO_SEGREDO` não pode ser trocado depois:** ele gera os códigos HMAC dos CPFs, e trocá-lo invalida o login gov.br de todos. Guarde-o num cofre de senhas. Sem ele, o sistema não sobe em modo produção.

### 2.2 Subir
```bash
docker compose build
docker compose run --rm carbonmg python -m plataforma criar-admin --email adm@orgao.mg.gov.br --nome "Administração"
docker compose up -d
```
O `criar-admin` pede a senha (mínimo de 12 caracteres) e mostra o segredo da verificação em duas etapas, que deve ser cadastrado num aplicativo autenticador. A administração não entra sem esse código.

**Rede com proxy corporativo que inspeciona TLS:** passe a CA do proxy apenas durante a construção da imagem:

```bash
docker build --secret id=ca,src=ca-do-proxy.crt --build-arg HTTPS_PROXY=http://proxy:3128 .
```

### 2.3 Proxy reverso com TLS (exemplo com Caddy)
```
carbonmg.orgao.mg.gov.br {
    reverse_proxy 127.0.0.1:8000
}
```
O cookie de sessão é `Secure`: em produção, o acesso **tem** de ser por HTTPS. A variável `CARBONO_COOKIE_INSEGURO=1` serve só para teste local e nunca deve ser usada em produção.

### 2.4 Primeiros cadastros (pelo painel, como administração)
1. Organizações: prefeitura, organismo de inspeção acreditado, terceirizadas.
2. Usuários:
   - servidores: informe o CPF, que é guardado só como código, para entrarem com gov.br;
   - empresas: informe o e-mail e clique em **Gerar convite**. Envie o link à pessoa; ele vale 72 horas e só pode ser usado uma vez.
3. Fatores de emissão: publique os valores oficiais vigentes (MCTI, Programa Brasileiro GHG Protocol).

## 3. Login gov.br

1. O **órgão** solicita a integração com o Login Único no portal gov.br e cadastra:
   - URL de retorno: `https://SEU_ENDERECO/auth/govbr/retorno`;
   - escopos: `openid email profile`.
2. Primeiro em homologação (`GOVBR_URL=https://sso.staging.acesso.gov.br`), depois em produção (`https://sso.acesso.gov.br`).
3. Preencha `GOVBR_CLIENT_ID`, `GOVBR_CLIENT_SECRET` e `GOVBR_REDIRECT_URI` no `.env` e reinicie. O botão "Entrar com gov.br" aparece sozinho.

**Como funciona:**
- fluxo authorization code com PKCE (S256), `state` e `nonce` de uso único, válidos por 10 minutos;
- o CPF vem do endpoint userinfo e é comparado por HMAC. Não há autocadastro: o servidor precisa ter sido cadastrado antes.

**Confira no manual de integração vigente do gov.br** os caminhos `/authorize`, `/token` e `/userinfo/` e o formato da claim `sub`. Se mudarem, ajuste `acesso.config_govbr`.

## 4. Rotinas

| Rotina | Comando | Frequência |
| --- | --- | --- |
| Lembretes, alertas e score das terceirizadas | `docker compose exec carbonmg python -m plataforma rotina-diaria` | diária (cron do servidor) |
| Backup | `docker compose exec carbonmg python -m plataforma backup --destino /backups` | diária; copie a pasta `backups/` para outro local em região BR |
| Teste de restauração | ver 4.1 | mensal |
| Atualização de versão | `git pull && docker compose build && docker compose up -d` | as migrações do banco rodam sozinhas ao iniciar |

### 4.1 Restaurar
```bash
docker compose stop carbonmg
docker compose run --rm carbonmg python -m plataforma restaurar /backups/carbonmg-AAAAMMDDTHHMMSSZ.db
docker compose start carbonmg
```
**O que a restauração confere antes de substituir o banco:**
- o SHA-256 do manifesto;
- o `integrity_check` do SQLite;
- a cadeia de hashes do livro-razão.

O banco anterior fica salvo com o sufixo `.antes-da-restauracao`.

## 5. Segurança aplicada

| Controle | Como |
| --- | --- |
| Autenticação | gov.br (servidores) ou e-mail com senha scrypt e 12+ caracteres; TOTP obrigatório para a administração e opcional para os demais (em "Minha conta") |
| Força bruta | 5 falhas bloqueiam por 15 minutos; o código TOTP não pode ser reutilizado |
| Sessão | cookie `HttpOnly`, `Secure` e `SameSite=Lax`; no banco só o hash do token; 8 h de validade, 1 h de inatividade; sessões encerradas ao trocar a senha ou anonimizar |
| CSRF | toda escrita exige `X-CSRF-Token` igual ao da sessão |
| Cabeçalhos | CSP restrita (sem script embutido), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, HSTS |
| Contêiner | usuário sem privilégios (uid 10001), healthcheck, porta publicada só em 127.0.0.1 |
| Logs | JSON por requisição (método, caminho, status, tempo), sem parâmetros nem dados pessoais |
| Anexos | tipo e assinatura do arquivo conferidos, até 10 MB, guardados pelo SHA-256 e verificados ao baixar |

**Pendentes recomendados:**
- antivírus no upload (ClamAV);
- WAF no proxy;
- monitoramento externo de `/saude`;
- armazenamento de anexos em S3 compatível com criptografia, se o volume crescer.

## 6. LGPD: checklist do órgão
- [ ] Definir controlador e encarregado (`CARBONO_CONTROLADOR`, `CARBONO_ENCARREGADO`): aparecem na política e no aceite.
- [ ] Revisar o texto da política (`acesso.politica_privacidade`) com a assessoria jurídica e, se mudar, trocar `CARBONO_TERMOS_VERSAO`. Todos voltam a aceitar.
- [ ] Atender pedidos dos titulares:
  - exportação: o próprio usuário faz em "Minha conta";
  - anonimização: a administração faz em Cadastros. Os atos ficam na trilha (LGPD, art. 16, I).
- [ ] Registrar as operações de tratamento (art. 37) e avaliar se cabe um relatório de impacto (art. 38).
- [ ] WhatsApp e e-mail automáticos: só com opt-in e base legal (a interface `Notificador` está pronta, sem envio).

## 7. Modo demonstração
`python -m plataforma demo --banco demo.db` e `python -m plataforma servir --banco demo.db --demo`.

Nesse modo, a entrada é pelo número do usuário (cabeçalho `X-Usuario-Id`), sem senha. **Nunca use em produção.** O modo produção é o padrão e ignora esse cabeçalho.
